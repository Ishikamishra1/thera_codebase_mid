"""
query_competition — Competition Agent tool.

Analyzes the competitive landscape for a therapeutic area by querying
ClinicalTrials.gov for active/recruiting trials grouped by sponsor.
Surfaces which pharma/biotech companies are active, how crowded the space
is, and where white-space opportunities exist.
"""
import json
import os
import urllib.request
import urllib.parse
import urllib.error
import boto3

CTGOV_BASE = "https://clinicaltrials.gov/api/v2/studies"
_REGION = os.environ.get("AWS_REGION", "us-east-1")
_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")


def lambda_handler(event, context):
    therapeutic_area = event.get("therapeutic_area", "Colorectal Cancer")

    try:
        studies = _fetch_studies(therapeutic_area, max_results=50)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        return {
            "agent": "competition_agent",
            "tool": "query_competition",
            "therapeutic_area": therapeutic_area,
            "error": str(exc),
            "records": [],
        }

    records = _extract_competitive_landscape(studies)
    llm_insights = _analyze_with_bedrock(therapeutic_area, records)

    return {
        "agent": "competition_agent",
        "tool": "query_competition",
        "therapeutic_area": therapeutic_area,
        "record_count": len(records),
        "records": records,
        "llm_insights": llm_insights,
    }


def _fetch_studies(therapeutic_area: str, max_results: int) -> list:
    params = urllib.parse.urlencode({
        "query.cond": therapeutic_area,
        "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING,NOT_YET_RECRUITING",
        "fields": "NCTId,BriefTitle,OverallStatus,Phase,LeadSponsorName,LeadSponsorClass,StartDate,PrimaryCompletionDate,EnrollmentCount",
        "pageSize": max_results,
        "format": "json",
    })
    req = urllib.request.Request(f"{CTGOV_BASE}?{params}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("studies", [])


def _extract_competitive_landscape(studies: list) -> list:
    sponsor_map = {}
    for s in studies:
        proto = s.get("protocolSection", {})
        id_mod = proto.get("identificationModule", {})
        status_mod = proto.get("statusModule", {})
        sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
        design_mod = proto.get("designModule", {})

        sponsor = sponsor_mod.get("leadSponsor", {}).get("name", "Unknown")
        sponsor_class = sponsor_mod.get("leadSponsor", {}).get("class", "")
        phase = (design_mod.get("phases") or ["Unknown"])[0]
        status = status_mod.get("overallStatus", "")
        nct_id = id_mod.get("nctId", "")
        title = id_mod.get("briefTitle", "")
        enrollment = design_mod.get("enrollmentInfo", {}).get("count", 0)

        if sponsor not in sponsor_map:
            sponsor_map[sponsor] = {
                "sponsor": sponsor,
                "sponsor_class": sponsor_class,  # INDUSTRY / NIH / OTHER_GOV / INDIVIDUAL / NETWORK
                "trial_count": 0,
                "phases": [],
                "total_enrollment": 0,
                "trials": [],
            }
        sponsor_map[sponsor]["trial_count"] += 1
        sponsor_map[sponsor]["phases"].append(phase)
        sponsor_map[sponsor]["total_enrollment"] += (enrollment or 0)
        if len(sponsor_map[sponsor]["trials"]) < 3:
            sponsor_map[sponsor]["trials"].append({"nct_id": nct_id, "title": title[:120], "status": status})

    # Sort by trial count descending
    return sorted(sponsor_map.values(), key=lambda x: x["trial_count"], reverse=True)


def _analyze_with_bedrock(therapeutic_area: str, records: list) -> str:
    if not records:
        return ""
    try:
        industry_players = [r for r in records if r.get("sponsor_class") == "INDUSTRY"]
        top_sponsors = records[:8]

        summary_lines = []
        for r in top_sponsors:
            phases = list(set(r["phases"]))
            summary_lines.append(
                f"  {r['sponsor']} ({r['sponsor_class']}): "
                f"{r['trial_count']} trial(s), phases: {', '.join(phases)}, "
                f"enrollment: {r['total_enrollment']}"
            )

        prompt = (
            f"You are a pharmaceutical competitive intelligence analyst. "
            f"Analyze the competitive landscape for '{therapeutic_area}' based on active clinical trials:\n\n"
            f"Total active sponsors: {len(records)}\n"
            f"Industry (pharma/biotech) players: {len(industry_players)}\n"
            f"Top sponsors by trial activity:\n"
            + "\n".join(summary_lines)
            + "\n\nIn 2-3 sentences: How crowded is this therapeutic space? "
            "Which disease stages or patient segments appear underserved by current trials? "
            "Where do genuine white-space opportunities exist for a new entrant? "
            "Be specific and strategically focused."
        )
        return _invoke_bedrock(prompt)
    except Exception:
        return ""


def _invoke_bedrock(prompt: str) -> str:
    client = boto3.client("bedrock-runtime", region_name=_REGION)
    response = client.converse(
        modelId=_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 512},
    )
    return response["output"]["message"]["content"][0]["text"]
