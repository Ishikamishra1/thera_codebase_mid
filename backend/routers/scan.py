"""
/scan endpoints — starts a TheraScout opportunity scan and lets the
frontend poll for its result.

POST /scan             -> starts a new scan, returns an execution id
GET  /scan/{execution_id} -> returns status + ranked opportunities once done

Bedrock LLM scoring runs here (in the backend process with SAML credentials)
rather than inside Lambda, because the org SCP blocks bedrock:InvokeModel
on Lambda execution roles.
"""
import os
import json
import re
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import boto3

log = logging.getLogger(__name__)
router = APIRouter()
sfn = boto3.client("stepfunctions")

STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")

DISCLAIMER = (
    "This is a decision-support prioritization draft, not a guarantee of "
    "drug success, a clinical prediction, or a regulatory prediction. A "
    "human researcher must review and validate before any resource is committed."
)


class ScanRequest(BaseModel):
    therapeutic_area: str = "Colorectal Cancer"


class AskRequest(BaseModel):
    opportunity: dict
    question: str
    therapeutic_area: str = "Unknown"


@router.post("")
def start_scan(request: ScanRequest):
    if not STATE_MACHINE_ARN:
        raise HTTPException(status_code=500, detail="STATE_MACHINE_ARN not configured")
    response = sfn.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        input=json.dumps({"therapeutic_area": request.therapeutic_area}),
    )
    return {"execution_arn": response["executionArn"]}


@router.post("/ask")
def ask_why(request: AskRequest):
    opp = request.opportunity
    prompt = f"""You are a pharmaceutical R&D analyst explaining AI-generated research prioritization to a human researcher.

Therapeutic area: {request.therapeutic_area}

Opportunity being discussed:
  Name: {opp.get("name", "Unknown")}
  Overall score: {opp.get("score", "N/A")}/100
  Rationale: {opp.get("rationale", "")}
  Key evidence: {", ".join(opp.get("key_evidence", []))}
  Dimension scores: {json.dumps(opp.get("dimension_scores", {}), indent=2)}

The researcher asks: "{request.question}"

Answer in 3-5 sentences. Be specific, cite the evidence above, and explain the scoring logic clearly. If the question is about a specific dimension score, explain what drove that particular score. End with one concrete suggestion for what the researcher should investigate next."""

    try:
        session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "saml"))
        client = session.client("bedrock-runtime", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        resp = client.converse(
            modelId=BEDROCK_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 512},
        )
        answer = resp["output"]["message"]["content"][0]["text"]
        return {"answer": answer}
    except Exception as exc:
        log.error("Ask-why Bedrock call failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"AI unavailable: {exc}")


@router.get("/{execution_id}")
def get_scan_status(execution_id: str):
    execution_arn = f"{STATE_MACHINE_ARN.replace(':stateMachine:', ':execution:')}:{execution_id}"

    try:
        response = sfn.describe_execution(executionArn=execution_arn)
    except sfn.exceptions.ExecutionDoesNotExist:
        raise HTTPException(status_code=404, detail="Scan not found")

    status = response["status"]
    result = {"status": status}

    if status == "SUCCEEDED":
        raw = json.loads(response.get("output", "{}"))
        # Re-score using Bedrock from the backend (SAML credentials can call Bedrock)
        agent_findings = raw.get("agent_findings", [])
        therapeutic_area = raw.get("therapeutic_area", "Unknown")

        llm_result = _score_with_bedrock(therapeutic_area, agent_findings)
        raw["ranked_opportunities"] = llm_result
        result["output"] = raw

    elif status == "FAILED":
        result["error"] = response.get("cause", "Unknown failure")

    return result


def _score_with_bedrock(therapeutic_area: str, findings: list) -> dict:
    by_agent = {}
    for item in findings:
        if isinstance(item, dict) and item.get("agent"):
            by_agent.setdefault(item["agent"], item)

    prompt = _build_prompt(therapeutic_area, by_agent)

    try:
        session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "saml"))
        client = session.client("bedrock-runtime", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        resp = client.converse(
            modelId=BEDROCK_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 2048},
        )
        text = resp["output"]["message"]["content"][0]["text"]
        return _parse_llm_response(text, therapeutic_area)
    except Exception as exc:
        log.error("Bedrock scoring failed: %s", exc)
        print(f"[BEDROCK ERROR] {exc}")
        return _score_deterministic(therapeutic_area, by_agent, str(exc))


def _build_prompt(therapeutic_area: str, by_agent: dict) -> str:
    def fmt(key, label):
        agent = by_agent.get(key, {})
        records = agent.get("records", [])
        insights = agent.get("llm_insights", "")
        lines = [f"=== {label} ==="]
        if insights:
            lines.append(f"Analysis: {insights}")
        if records:
            lines.append(f"Records: {len(records)}")
            for r in records[:5]:
                lines.append(f"  - {json.dumps(r, default=str)[:200]}")
        else:
            lines.append("No records.")
        return "\n".join(lines)

    sections = "\n\n".join([
        fmt("disease_agent", "DISEASE BURDEN (GLOBOCAN)"),
        fmt("treatment_agent", "APPROVED TREATMENTS (FDA)"),
        fmt("research_agent", "SCIENTIFIC LITERATURE (PubMed)"),
        fmt("clinical_trial_agent", "CLINICAL TRIALS (ClinicalTrials.gov)"),
        fmt("competition_agent", "COMPETITIVE LANDSCAPE (Active Trial Sponsors)"),
        fmt("trend_agent", "RESEARCH MOMENTUM (Publication Trends)"),
    ])

    return f"""You are a pharmaceutical research intelligence analyst.

Analyze data for therapeutic area: {therapeutic_area}

{sections}

Identify and rank the TOP 5 therapeutic R&D opportunities based on disease burden, treatment gaps, scientific evidence, research momentum, competitive landscape, and unmet medical need.

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "opportunities": [
    {{
      "name": "Specific opportunity name",
      "score": 85,
      "rationale": "2-3 sentence evidence-based rationale",
      "key_evidence": ["evidence 1", "evidence 2", "evidence 3"],
      "dimension_scores": {{
        "unmet_medical_need": 90,
        "disease_burden": 85,
        "existing_treatment_gap": 80,
        "scientific_evidence": 75,
        "research_momentum": 70,
        "competitive_landscape": 65
      }}
    }}
  ]
}}"""


def _parse_llm_response(text: str, therapeutic_area: str) -> dict:
    text = text.strip()
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text.strip())
    text = text.strip()
    parsed = json.loads(text)
    opportunities = parsed.get("opportunities", [])
    for opp in opportunities:
        opp["score"] = float(opp.get("score", 0))
        for k, v in opp.get("dimension_scores", {}).items():
            opp["dimension_scores"][k] = float(v)
    return {"opportunities": opportunities, "method": "bedrock_llm", "model": BEDROCK_MODEL_ID}


def _score_deterministic(therapeutic_area: str, by_agent: dict, error: str = "") -> dict:
    disease = by_agent.get("disease_agent", {})
    treatment = by_agent.get("treatment_agent", {})
    research = by_agent.get("research_agent", {})
    trials = by_agent.get("clinical_trial_agent", {})

    n_burden = len(disease.get("records") or [])
    n_drugs = len(treatment.get("records") or [])
    n_papers = len(research.get("records") or [])
    trial_records = trials.get("records") or []
    n_trials = len(trial_records)
    sponsors = {r.get("sponsor") for r in trial_records if r.get("sponsor")}

    def sat(v, full): return min(100.0, 100.0 * v / full) if full > 0 else 0.0

    dims = {
        "disease_burden": sat(n_burden, 10),
        "existing_treatment_gap": 100 - sat(n_drugs, 25),
        "scientific_evidence": sat(n_papers, 30),
        "research_momentum": sat(n_papers, 30),
        "competitive_landscape": 100 - sat(len(sponsors), 15),
        "unmet_medical_need": (sat(n_burden, 10) + (100 - sat(n_drugs, 25))) / 2,
    }
    score = round(sum(dims[k] * w for k, w in {
        "unmet_medical_need": 0.30, "disease_burden": 0.20,
        "existing_treatment_gap": 0.20, "scientific_evidence": 0.15,
        "research_momentum": 0.10, "competitive_landscape": 0.05,
    }.items()), 1)

    result = {
        "opportunities": [{
            "name": f"{therapeutic_area} — evidence-signal composite",
            "score": score,
            "rationale": f"Deterministic fallback: {n_burden} disease records, {n_drugs} drug labels, {n_papers} publications, {n_trials} trials across {len(sponsors)} sponsors.",
            "key_evidence": [f"{n_papers} PubMed publications", f"{n_trials} clinical trials", f"{n_drugs} FDA drug labels"],
            "dimension_scores": {k: round(v, 1) for k, v in dims.items()},
        }],
        "method": "deterministic_fallback",
    }
    if error:
        result["bedrock_error"] = error
    return result
