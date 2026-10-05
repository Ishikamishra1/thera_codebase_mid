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
import itertools
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import boto3
import db

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


class FeedbackRequest(BaseModel):
    execution_arn: str
    opportunity_rank: int
    decision: str           # 'approved' | 'rejected' | 'needs_review'
    comment: str = ""
    submitted_by: str = "researcher"


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
        client = _bedrock_client()
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


@router.post("/feedback")
def submit_feedback(request: FeedbackRequest):
    """Save researcher approve/reject/needs_review decision for one opportunity."""
    valid = {"approved", "rejected", "needs_review"}
    if request.decision not in valid:
        raise HTTPException(status_code=400, detail=f"decision must be one of {valid}")

    saved = db.save_feedback(
        execution_arn=request.execution_arn,
        opportunity_rank=request.opportunity_rank,
        decision=request.decision,
        comment=request.comment,
        submitted_by=request.submitted_by,
    )
    if not saved:
        raise HTTPException(
            status_code=404,
            detail="Scan or opportunity not found — ensure DATABASE_URL is set and the scan was saved to RDS.",
        )
    return {"status": "saved", "decision": request.decision}


@router.post("/weights/retrain")
def retrain_weights():
    """
    Recompute scoring weights from cumulative researcher feedback.
    Dimensions that consistently appear in approved opportunities gain weight.
    """
    new_weights = db.retrain_weights()
    if not new_weights:
        raise HTTPException(
            status_code=422,
            detail="Not enough feedback to retrain — approve or reject some opportunities first.",
        )
    return {"status": "retrained", "new_weights": new_weights}


_DEMO_ROTATION = itertools.cycle([
    ("Colorectal Cancer",   "demo-colorectal-001"),
    ("Non-Small Cell Lung Cancer", "demo-nsclc-002"),
    ("Alzheimer's Disease", "demo-alzheimers-003"),
])

@router.get("/demo")
def get_demo():
    """
    Return a demo scan result — always works, no AWS required.
    Rotates through 3 therapeutic areas on each call.
    Tries DB first (if seeded), falls back to hardcoded showcase data.
    """
    area, demo_id = next(_DEMO_ROTATION)

    # Try DB first
    data = db.get_demo_scan(area)
    if data:
        return {
            "status": "SUCCEEDED",
            "source": "demo_db",
            "output": {
                "therapeutic_area": data["therapeutic_area"],
                "ranked_opportunities": data["ranked_opportunities"],
                "execution_arn": data["execution_arn"],
                "agent_findings": _DEMO_AGENT_FINDINGS_BY_AREA.get(area, _DEMO_AGENT_FINDINGS_CRC),
            },
        }

    # Hardcoded fallback — always available, no DB needed
    datasets = {
        "Colorectal Cancer": (_DEMO_OPPORTUNITIES_CRC, _DEMO_AGENT_FINDINGS_CRC),
        "Non-Small Cell Lung Cancer": (_DEMO_OPPORTUNITIES_NSCLC, _DEMO_AGENT_FINDINGS_NSCLC),
        "Alzheimer's Disease": (_DEMO_OPPORTUNITIES_ALZ, _DEMO_AGENT_FINDINGS_ALZ),
    }
    opps, findings = datasets[area]
    arn_base = "arn:aws:states:us-east-1:446205069645:execution:TheraScoutOpportunityScan"
    return {
        "status": "SUCCEEDED",
        "source": "demo_static",
        "output": {
            "therapeutic_area": area,
            "execution_arn": f"{arn_base}:{demo_id}",
            "ranked_opportunities": opps,
            "agent_findings": findings,
        },
    }


_DEMO_OPPORTUNITIES_CRC = {
    "opportunities": [
        {
            "name": "Targeted therapies for KRAS-mutant metastatic CRC",
            "score": 91.0,
            "rationale": "KRAS mutations in ~45% of CRC patients represent a large underserved population. Recent KRAS G12C inhibitor approvals in lung cancer validate the mechanism. First-line metastatic CRC has poor outcomes with current chemotherapy backbones.",
            "key_evidence": [
                "KRAS G12C inhibitor sotorasib shows 36% ORR in NSCLC — mechanism validated",
                "~45% of mCRC patients carry KRAS mutations (the largest untreated subgroup)",
                "Median OS <24 months on standard chemo in metastatic setting",
            ],
            "dimension_scores": {
                "unmet_medical_need": 95, "disease_burden": 88,
                "existing_treatment_gap": 92, "scientific_evidence": 82,
                "research_momentum": 78, "competitive_landscape": 45,
            },
        },
        {
            "name": "Immunotherapy combinations for MSI-H/dMMR CRC",
            "score": 87.0,
            "rationale": "MSI-H CRC responds to PD-1 blockade but represents only 15% of patients. Combination strategies to expand eligibility to MSS tumors (~85%) are a major unmet need with high commercial value.",
            "key_evidence": [
                "Pembrolizumab approved first-line for MSI-H mCRC (KEYNOTE-177)",
                "MSS CRC (~85% of cases) shows minimal checkpoint inhibitor response",
                "Phase III KEYNOTE-177: 16.5 months PFS vs 8.2 months for chemotherapy",
            ],
            "dimension_scores": {
                "unmet_medical_need": 90, "disease_burden": 85,
                "existing_treatment_gap": 88, "scientific_evidence": 79,
                "research_momentum": 82, "competitive_landscape": 52,
            },
        },
        {
            "name": "Liquid biopsy for early-stage CRC detection",
            "score": 84.0,
            "rationale": "Colonoscopy compliance is low globally. Liquid biopsy ctDNA tests could transform population-level screening — Stage I 5-year survival exceeds 90% vs 14% at Stage IV.",
            "key_evidence": [
                "5-year survival: Stage I CRC 91% vs Stage IV 14% — detection is the lever",
                "Colonoscopy screening compliance <40% in most populations globally",
                "Guardant Shield ctDNA test: 83% sensitivity for Stage I-II CRC",
            ],
            "dimension_scores": {
                "unmet_medical_need": 88, "disease_burden": 90,
                "existing_treatment_gap": 85, "scientific_evidence": 72,
                "research_momentum": 88, "competitive_landscape": 38,
            },
        },
        {
            "name": "ADC therapies targeting CEACAM5 in CRC",
            "score": 76.0,
            "rationale": "Antibody-drug conjugates targeting CEA-related cell adhesion molecule show early promise. CEACAM5 is overexpressed in ~90% of CRC tumors. Phase II data limited but mechanistically well-validated.",
            "key_evidence": [
                "Tusamitamab ravtansine Phase II: 22% ORR in third-line mCRC",
                "CEACAM5 overexpressed in ~90% of CRC tumors — broad patient eligibility",
                "Multiple ADC programs in early development targeting same antigen",
            ],
            "dimension_scores": {
                "unmet_medical_need": 75, "disease_burden": 80,
                "existing_treatment_gap": 70, "scientific_evidence": 55,
                "research_momentum": 65, "competitive_landscape": 30,
            },
        },
        {
            "name": "Microbiome modulation to enhance CRC immunotherapy response",
            "score": 61.0,
            "rationale": "Emerging evidence links gut microbiome composition to checkpoint inhibitor response. Clinical evidence is early-stage and mechanism of action is poorly understood.",
            "key_evidence": [
                "Fusobacterium nucleatum correlated with CRC progression and resistance",
                "Microbiome diversity associated with anti-PD1 response in solid tumors",
                "Phase I FMT trials ongoing but results remain preliminary",
            ],
            "dimension_scores": {
                "unmet_medical_need": 70, "disease_burden": 75,
                "existing_treatment_gap": 65, "scientific_evidence": 35,
                "research_momentum": 55, "competitive_landscape": 70,
            },
        },
    ],
    "method": "bedrock_llm",
    "model": "amazon.nova-pro-v1:0",
}

_DEMO_AGENT_FINDINGS_CRC = [
    {
        "agent": "disease_agent", "tool": "query_globocan_data",
        "record_count": 8, "records": [],
        "llm_insights": "Colorectal cancer ranks 3rd globally by incidence (1.9M new cases/year) and 2nd by mortality. Rising early-onset incidence in adults under 50 is a growing concern.",
    },
    {
        "agent": "treatment_agent", "tool": "query_openfda",
        "record_count": 12, "records": [],
        "llm_insights": "12 FDA-approved therapies identified. Significant gap in KRAS-mutant and MSS populations. Targeted options limited to KRAS G12C (rare mutation) and EGFR inhibitors (RAS wild-type only).",
    },
    {
        "agent": "research_agent", "tool": "query_pubmed",
        "record_count": 35, "records": [],
        "llm_insights": "High publication velocity in liquid biopsy and KRAS inhibition. Strong evidence base for immunotherapy in MSI-H. Microbiome research accelerating but early-stage.",
    },
    {
        "agent": "clinical_trial_agent", "tool": "query_clinicaltrials",
        "record_count": 47, "records": [],
        "llm_insights": "47 active/recruiting trials. High Phase II density suggests validation phase. Notable Phase III programmes: KEYNOTE-177 follow-on, KRAS G12C combinations.",
    },
    {
        "agent": "competition_agent", "tool": "query_competition",
        "record_count": 18,
        "records": [
            {"sponsor_name": "Genentech/Roche", "sponsor_class": "INDUSTRY", "trial_count": 8, "phases": ["PHASE2", "PHASE3"]},
            {"sponsor_name": "Merck Sharp & Dohme", "sponsor_class": "INDUSTRY", "trial_count": 7, "phases": ["PHASE3"]},
            {"sponsor_name": "Bristol-Myers Squibb", "sponsor_class": "INDUSTRY", "trial_count": 6, "phases": ["PHASE2", "PHASE3"]},
            {"sponsor_name": "AstraZeneca", "sponsor_class": "INDUSTRY", "trial_count": 5, "phases": ["PHASE2"]},
            {"sponsor_name": "National Cancer Institute", "sponsor_class": "NIH", "trial_count": 4, "phases": ["PHASE1", "PHASE2"]},
            {"sponsor_name": "Amgen", "sponsor_class": "INDUSTRY", "trial_count": 4, "phases": ["PHASE2"]},
        ],
        "llm_insights": "Competitive landscape dominated by 3 large pharma sponsors in IO combinations. KRAS and ADC space less crowded — white-space opportunity for differentiated entry.",
    },
    {
        "agent": "trend_agent", "tool": "query_trends",
        "record_count": 1,
        "records": [{
            "year_windows": {1: 420, 2: 780, 3: 1100, 5: 1650},
            "year_over_year_change_pct": 12.5,
            "momentum": "accelerating",
        }],
        "llm_insights": "Publication velocity accelerating at 12.5% year-over-year. Liquid biopsy and KRAS inhibition driving the upswing. Strong signal for continued R&D investment.",
    },
    {
        "agent": "europe_pmc_agent", "tool": "query_europe_pmc",
        "record_count": 15, "records": [],
        "llm_insights": "High-citation literature concentrated in IO and targeted therapy. Notable open-access preprint activity in microbiome and early detection research.",
    },
    {
        "agent": "enterprise_kb_agent", "tool": "query_knowledge_base",
        "record_count": 2, "records": [],
        "kb_status": "active",
        "llm_insights": "Internal guidelines emphasise unmet need in KRAS-mutant and MSS populations. Previous portfolio analysis identified liquid biopsy as a platform technology priority.",
    },
]


_DEMO_OPPORTUNITIES_NSCLC = {
    "opportunities": [
        {
            "name": "KRAS G12C inhibitors beyond sotorasib in NSCLC",
            "score": 89.0,
            "rationale": "Sotorasib and adagrasib are approved for KRAS G12C NSCLC, but resistance emerges rapidly. Next-generation KRAS inhibitors and combinations with SHP2 or SOS1 represent a large commercial opportunity with validated mechanism.",
            "key_evidence": [
                "KRAS G12C mutation in ~13% of NSCLC — the most common KRAS variant",
                "Adagrasib (KRYSTAL-1): 43% ORR but median DoR only 8.5 months — resistance is the bottleneck",
                "SHP2 inhibitor combinations show preclinical synergy with KRASG12C inhibitors",
            ],
            "dimension_scores": {
                "unmet_medical_need": 92, "disease_burden": 90,
                "existing_treatment_gap": 88, "scientific_evidence": 85,
                "research_momentum": 86, "competitive_landscape": 40,
            },
        },
        {
            "name": "STK11/KEAP1 co-mutation immunotherapy resistance in NSCLC",
            "score": 85.0,
            "rationale": "STK11 and KEAP1 co-mutations confer primary resistance to PD-1 blockade in KRAS-mutant NSCLC. This large underserved subgroup (~20% of NSCLC) has no approved targeted options and poor outcomes on standard immunotherapy.",
            "key_evidence": [
                "STK11 loss associated with >60% reduction in IO response rate in KRAS-mutant NSCLC",
                "No FDA-approved therapy specifically targeting STK11/KEAP1-altered tumors",
                "STING pathway agonists showing early clinical activity in STK11-mutant disease",
            ],
            "dimension_scores": {
                "unmet_medical_need": 94, "disease_burden": 88,
                "existing_treatment_gap": 95, "scientific_evidence": 72,
                "research_momentum": 75, "competitive_landscape": 68,
            },
        },
        {
            "name": "HER2-targeted ADCs for HER2-mutant NSCLC",
            "score": 82.0,
            "rationale": "T-DXd (trastuzumab deruxtecan) received accelerated approval for HER2-mutant NSCLC in 2022. The ADC platform validated for this population with ~3% mutation prevalence — room for next-generation agents with improved therapeutic index.",
            "key_evidence": [
                "T-DXd: 57.7% ORR in HER2-mutant NSCLC (DESTINY-Lung02)",
                "HER2 mutations in ~2-4% of NSCLC — small but commercially validated niche",
                "Interstitial lung disease signal with T-DXd — safety window improvement needed",
            ],
            "dimension_scores": {
                "unmet_medical_need": 80, "disease_burden": 75,
                "existing_treatment_gap": 78, "scientific_evidence": 82,
                "research_momentum": 80, "competitive_landscape": 35,
            },
        },
        {
            "name": "ctDNA MRD monitoring for NSCLC adjuvant therapy decisions",
            "score": 74.0,
            "rationale": "Circulating tumor DNA minimal residual disease detection after curative resection could identify patients needing adjuvant therapy. Strong biomarker data but no prospective RCT data yet in NSCLC.",
            "key_evidence": [
                "ctDNA detection post-surgery predicts recurrence with ~92% specificity in early NSCLC",
                "ADAURA trial: osimertinib adjuvant in EGFR+ NSCLC — ctDNA could refine selection",
                "Phase III MERMAID-2 ongoing: ctDNA-guided adjuvant immunotherapy in NSCLC",
            ],
            "dimension_scores": {
                "unmet_medical_need": 78, "disease_burden": 82,
                "existing_treatment_gap": 72, "scientific_evidence": 65,
                "research_momentum": 78, "competitive_landscape": 55,
            },
        },
        {
            "name": "Bispecific antibodies targeting PD-1 and TIGIT in NSCLC",
            "score": 65.0,
            "rationale": "TIGIT emerged as a co-inhibitory receptor target after PD-1 success, but Phase III TIGIT monotherapy results have been disappointing. Bispecifics co-targeting PD-1/TIGIT may overcome the single-agent limitations.",
            "key_evidence": [
                "Tiragolumab + atezolizumab SKYSCRAPER-01: failed primary endpoint in PD-L1 high NSCLC",
                "IBI321 (PD-1/TIGIT bispecific) Phase I shows encouraging early signals",
                "Crowded space: 14 anti-TIGIT agents in active development",
            ],
            "dimension_scores": {
                "unmet_medical_need": 72, "disease_burden": 80,
                "existing_treatment_gap": 65, "scientific_evidence": 55,
                "research_momentum": 60, "competitive_landscape": 20,
            },
        },
    ],
    "method": "bedrock_llm",
    "model": "amazon.nova-pro-v1:0",
}

_DEMO_AGENT_FINDINGS_NSCLC = [
    {
        "agent": "disease_agent", "tool": "query_globocan_data",
        "record_count": 9, "records": [],
        "llm_insights": "NSCLC accounts for ~85% of all lung cancers — the leading cause of cancer death globally (1.8M deaths/year). Five-year survival remains <20% overall despite targeted therapy advances.",
    },
    {
        "agent": "treatment_agent", "tool": "query_openfda",
        "record_count": 18, "records": [],
        "llm_insights": "18 FDA-approved therapies — most targeted (EGFR, ALK, ROS1, BRAF, KRAS G12C, HER2, MET, RET, NTRK). Significant gap in STK11/KEAP1-mutant and squamous subtypes.",
    },
    {
        "agent": "research_agent", "tool": "query_pubmed",
        "record_count": 52, "records": [],
        "llm_insights": "Extremely high publication velocity — KRAS resistance mechanisms and bispecific antibodies driving the upswing. STK11 biology papers up 40% YoY.",
    },
    {
        "agent": "clinical_trial_agent", "tool": "query_clinicaltrials",
        "record_count": 68, "records": [],
        "llm_insights": "68 active/recruiting trials. Dense Phase III activity in IO combinations. Notable: MERMAID-2 (ctDNA adjuvant), KRYSTAL-12 (adagrasib 2L), MARIPOSA (amivantamab+lazertinib).",
    },
    {
        "agent": "competition_agent", "tool": "query_competition",
        "record_count": 22,
        "records": [
            {"sponsor_name": "AstraZeneca", "sponsor_class": "INDUSTRY", "trial_count": 12, "phases": ["PHASE2", "PHASE3"]},
            {"sponsor_name": "Genentech/Roche", "sponsor_class": "INDUSTRY", "trial_count": 10, "phases": ["PHASE3"]},
            {"sponsor_name": "Merck Sharp & Dohme", "sponsor_class": "INDUSTRY", "trial_count": 9, "phases": ["PHASE2", "PHASE3"]},
            {"sponsor_name": "Amgen", "sponsor_class": "INDUSTRY", "trial_count": 7, "phases": ["PHASE2"]},
            {"sponsor_name": "Johnson & Johnson", "sponsor_class": "INDUSTRY", "trial_count": 6, "phases": ["PHASE2", "PHASE3"]},
            {"sponsor_name": "National Cancer Institute", "sponsor_class": "NIH", "trial_count": 5, "phases": ["PHASE1", "PHASE2"]},
        ],
        "llm_insights": "Most competitive oncology landscape overall. White-space remains in STK11/KEAP1 co-mutation biology and ctDNA-guided treatment selection.",
    },
    {
        "agent": "trend_agent", "tool": "query_trends",
        "record_count": 1,
        "records": [{"year_windows": {1: 680, 2: 1250, 3: 1720, 5: 2400}, "year_over_year_change_pct": 18.2, "momentum": "accelerating"}],
        "llm_insights": "Publication velocity accelerating at 18.2% YoY — the highest momentum of any solid tumor. KRAS biology and IO resistance mechanisms driving volume.",
    },
    {
        "agent": "europe_pmc_agent", "tool": "query_europe_pmc",
        "record_count": 22, "records": [],
        "llm_insights": "Strong European translational research in EGFR and ALK resistance. High preprint volume in liquid biopsy and ctDNA monitoring.",
    },
    {
        "agent": "enterprise_kb_agent", "tool": "query_knowledge_base",
        "record_count": 3, "records": [],
        "kb_status": "active",
        "llm_insights": "Internal portfolio flagged STK11/KEAP1 as white-space in 2023 pipeline review. ctDNA MRD monitoring identified as platform technology of interest.",
    },
]

_DEMO_OPPORTUNITIES_ALZ = {
    "opportunities": [
        {
            "name": "Anti-tau therapies for early-stage Alzheimer's disease",
            "score": 88.0,
            "rationale": "Amyloid-targeting antibodies (lecanemab, donanemab) achieved regulatory approval but tau pathology drives neurodegeneration downstream. Anti-tau approaches represent the next frontier with large patient populations in early AD.",
            "key_evidence": [
                "Lecanemab (Leqembi) approved 2023: slows decline 27% but does not stop tau spread",
                "Tau PET now enables patient selection — early AD population identifiable pre-symptoms",
                "LUCIDITY trial: semorinemab Phase II missed primary endpoint but signals in early disease",
            ],
            "dimension_scores": {
                "unmet_medical_need": 96, "disease_burden": 95,
                "existing_treatment_gap": 90, "scientific_evidence": 78,
                "research_momentum": 82, "competitive_landscape": 55,
            },
        },
        {
            "name": "Neuroinflammation targets (TREM2/CSF1R) in Alzheimer's disease",
            "score": 84.0,
            "rationale": "Microglial dysfunction via TREM2 and CSF1R pathways is implicated in Alzheimer's neurodegeneration. GWAS studies identify TREM2 variants as significant risk factors — highly validated genetic target with no approved therapy.",
            "key_evidence": [
                "TREM2 R47H variant increases AD risk 2-4x — among the strongest genetic risk factors after APOE4",
                "AL002 (TREM2 agonist antibody) Phase II ongoing: AL002a shows target engagement in CSF",
                "CSF1R inhibitor PLX5622 reduces neuroinflammation in AD mouse models",
            ],
            "dimension_scores": {
                "unmet_medical_need": 88, "disease_burden": 92,
                "existing_treatment_gap": 92, "scientific_evidence": 72,
                "research_momentum": 78, "competitive_landscape": 62,
            },
        },
        {
            "name": "APOE4 gene correction and lipid metabolism in AD",
            "score": 80.0,
            "rationale": "APOE4 is present in ~25% of the population and confers 3-4x higher AD risk. Gene therapy and small molecule approaches targeting APOE4 lipid dysregulation are early but represent a massive prevention opportunity.",
            "key_evidence": [
                "APOE4 homozygotes have ~60% lifetime risk of AD vs ~10% for APOE3",
                "Alector's AL101 (anti-APOE4 antibody) entering Phase I trials",
                "CRISPR APOE4→APOE3 correction validated in iPSC-derived neurons",
            ],
            "dimension_scores": {
                "unmet_medical_need": 90, "disease_burden": 90,
                "existing_treatment_gap": 88, "scientific_evidence": 62,
                "research_momentum": 72, "competitive_landscape": 70,
            },
        },
        {
            "name": "Blood-based biomarkers for pre-symptomatic AD screening",
            "score": 76.0,
            "rationale": "Plasma p-tau217 and Abeta42/40 ratios now approach amyloid PET accuracy for identifying pre-symptomatic AD. Scalable blood tests could enable population-level screening to feed prevention trials.",
            "key_evidence": [
                "Plasma p-tau217 AUC 0.96 for amyloid PET positivity — near-PET accuracy",
                "Lumipulse p-tau217 FDA cleared in 2024 for clinical use",
                "Global Alzheimer's Association calls blood biomarkers 'transformative' for trial enrichment",
            ],
            "dimension_scores": {
                "unmet_medical_need": 82, "disease_burden": 88,
                "existing_treatment_gap": 75, "scientific_evidence": 80,
                "research_momentum": 85, "competitive_landscape": 45,
            },
        },
        {
            "name": "Synaptic protection strategies in mild cognitive impairment",
            "score": 63.0,
            "rationale": "Synaptic loss correlates more closely with cognitive decline than amyloid or tau burden. Neuroprotective strategies targeting synaptic integrity at the MCI stage represent an early-intervention opportunity but mechanism remains unclear.",
            "key_evidence": [
                "Synapse density (SV2A PET) correlates r=0.72 with MMSE decline — strongest structural correlate",
                "Simufilam (PTI-125): Phase III failed primary endpoint despite strong Phase II signals",
                "No approved therapy specifically preserving synaptic density in MCI",
            ],
            "dimension_scores": {
                "unmet_medical_need": 78, "disease_burden": 85,
                "existing_treatment_gap": 80, "scientific_evidence": 42,
                "research_momentum": 55, "competitive_landscape": 65,
            },
        },
    ],
    "method": "bedrock_llm",
    "model": "amazon.nova-pro-v1:0",
}

_DEMO_AGENT_FINDINGS_ALZ = [
    {
        "agent": "disease_agent", "tool": "query_globocan_data",
        "record_count": 7, "records": [],
        "llm_insights": "55 million people globally live with dementia; Alzheimer's accounts for 60-70%. Projected to reach 139M by 2050. Annual global cost exceeds $1.3 trillion — largest unmet need in neurology.",
    },
    {
        "agent": "treatment_agent", "tool": "query_openfda",
        "record_count": 6, "records": [],
        "llm_insights": "6 FDA-approved therapies: 4 symptomatic (cholinesterase inhibitors + memantine) + lecanemab and donanemab (disease-modifying, 2023-2024). Massive gap in tau, neuroinflammation, and prevention.",
    },
    {
        "agent": "research_agent", "tool": "query_pubmed",
        "record_count": 44, "records": [],
        "llm_insights": "High publication velocity. Blood biomarker and neuroinflammation papers up 35% YoY. Anti-amyloid literature plateauing; tau and TREM2 biology accelerating.",
    },
    {
        "agent": "clinical_trial_agent", "tool": "query_clinicaltrials",
        "record_count": 55, "records": [],
        "llm_insights": "55 active/recruiting trials. Dense Phase II in tau and neuroinflammation. Key Phase III: TRAILBLAZER-ALZ 4 (donanemab subQ), AHEAD 3-45 (prevention in pre-symptomatic APOE4).",
    },
    {
        "agent": "competition_agent", "tool": "query_competition",
        "record_count": 16,
        "records": [
            {"sponsor_name": "Eli Lilly", "sponsor_class": "INDUSTRY", "trial_count": 9, "phases": ["PHASE2", "PHASE3"]},
            {"sponsor_name": "Biogen/Eisai", "sponsor_class": "INDUSTRY", "trial_count": 7, "phases": ["PHASE3"]},
            {"sponsor_name": "Roche/Genentech", "sponsor_class": "INDUSTRY", "trial_count": 6, "phases": ["PHASE2", "PHASE3"]},
            {"sponsor_name": "National Institute on Aging", "sponsor_class": "NIH", "trial_count": 6, "phases": ["PHASE2", "PHASE3"]},
            {"sponsor_name": "Alector", "sponsor_class": "INDUSTRY", "trial_count": 3, "phases": ["PHASE1", "PHASE2"]},
            {"sponsor_name": "AC Immune", "sponsor_class": "INDUSTRY", "trial_count": 3, "phases": ["PHASE2"]},
        ],
        "llm_insights": "Anti-amyloid space now crowded with approved agents. Tau, TREM2/neuroinflammation, and APOE4-targeted approaches remain relatively open — white-space for differentiated entry.",
    },
    {
        "agent": "trend_agent", "tool": "query_trends",
        "record_count": 1,
        "records": [{"year_windows": {1: 580, 2: 1020, 3: 1380, 5: 1900}, "year_over_year_change_pct": 15.8, "momentum": "accelerating"}],
        "llm_insights": "Publication velocity up 15.8% YoY, driven by blood biomarker and neuroinflammation research. Post-lecanemab approval surge in early intervention and prevention literature.",
    },
    {
        "agent": "europe_pmc_agent", "tool": "query_europe_pmc",
        "record_count": 19, "records": [],
        "llm_insights": "Strong European research in APOE genetics (UK Biobank) and tau PET. EPAD longitudinal cohort generating high-citation prevention trial data.",
    },
    {
        "agent": "enterprise_kb_agent", "tool": "query_knowledge_base",
        "record_count": 2, "records": [],
        "kb_status": "active",
        "llm_insights": "Internal neurology review identified blood biomarkers as platform priority for CRO partnerships. TREM2 biology flagged as emerging white-space in 2024 portfolio scan.",
    },
]

_DEMO_AGENT_FINDINGS_BY_AREA = {
    "Colorectal Cancer": _DEMO_AGENT_FINDINGS_CRC,
    "Non-Small Cell Lung Cancer": _DEMO_AGENT_FINDINGS_NSCLC,
    "Alzheimer's Disease": _DEMO_AGENT_FINDINGS_ALZ,
}


@router.get("/history")
def get_history(limit: int = 20):
    """Return the most recent completed scans from RDS."""
    rows = db.get_history(limit=limit)
    return {
        "scans": rows,
        "db_available": bool(rows) or bool(os.environ.get("DATABASE_URL")),
    }


@router.get("/weights")
def get_scoring_weights():
    """Return current scoring dimension weights from RDS."""
    weights = db.get_weights()
    if not weights:
        # Return defaults when DB not configured
        weights = {
            "unmet_medical_need": 0.30,
            "disease_burden": 0.20,
            "existing_treatment_gap": 0.20,
            "scientific_evidence": 0.15,
            "research_momentum": 0.10,
            "competitive_landscape": 0.05,
        }
    return {"weights": weights, "source": "rds" if db.get_weights() else "default"}


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
        agent_findings = raw.get("agent_findings", [])
        therapeutic_area = raw.get("therapeutic_area", "Unknown")

        llm_result = _score_with_bedrock(therapeutic_area, agent_findings)
        raw["ranked_opportunities"] = llm_result
        raw["execution_arn"] = execution_arn   # pass through so frontend can send feedback
        result["output"] = raw

        # Persist to RDS (no-op if DATABASE_URL not set)
        db.save_scan(execution_arn, therapeutic_area, llm_result)

    elif status == "FAILED":
        result["error"] = response.get("cause", "Unknown failure")

    return result


def _bedrock_client():
    """
    Return a bedrock-runtime client.
    - Local dev (AWS_PROFILE=saml): uses the named SAML profile.
    - ECS / Lambda: AWS_PROFILE is unset → boto3 uses the task/execution role.
    """
    profile = os.environ.get("AWS_PROFILE", "")
    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    return session.client("bedrock-runtime", region_name=region)


def _score_with_bedrock(therapeutic_area: str, findings: list) -> dict:
    by_agent = {}
    for item in findings:
        if isinstance(item, dict) and item.get("agent"):
            by_agent.setdefault(item["agent"], item)

    prompt = _build_prompt(therapeutic_area, by_agent)

    try:
        client = _bedrock_client()
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
        fmt("europe_pmc_agent", "EUROPE PMC (Citation Quality & Preprints)"),
        fmt("enterprise_kb_agent", "ENTERPRISE KNOWLEDGE BASE (Internal Research & Guidelines)"),
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
