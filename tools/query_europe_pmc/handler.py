"""
query_europe_pmc — Europe PMC Research Agent tool.

Queries the Europe PMC REST API for peer-reviewed articles, preprints,
clinical guidelines, and patents. Complements the PubMed agent by covering:
  - European research not always indexed in PubMed
  - Preprints (bioRxiv / medRxiv) for cutting-edge findings
  - Citation counts as a proxy for evidence quality and impact
  - Open-access full-text availability

API: https://www.ebi.ac.uk/europepmc/webservices/rest/search
"""
import json
import os
import urllib.request
import urllib.parse
import urllib.error
import boto3

EPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_REGION = os.environ.get("AWS_REGION", "us-east-1")
_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")


def lambda_handler(event, context):
    therapeutic_area = event.get("therapeutic_area", "Colorectal Cancer")
    max_results = event.get("max_results", 25)

    try:
        records = _search(therapeutic_area, max_results)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        return {
            "agent": "europe_pmc_agent",
            "tool": "query_europe_pmc",
            "therapeutic_area": therapeutic_area,
            "error": str(exc),
            "records": [],
        }

    llm_insights = _analyze_with_bedrock(therapeutic_area, records)

    return {
        "agent": "europe_pmc_agent",
        "tool": "query_europe_pmc",
        "therapeutic_area": therapeutic_area,
        "record_count": len(records),
        "records": records,
        "llm_insights": llm_insights,
    }


def _search(therapeutic_area: str, max_results: int) -> list:
    params = urllib.parse.urlencode({
        "query": therapeutic_area,
        "format": "json",
        "resultType": "lite",
        "pageSize": max_results,
        "sort": "CITED desc",   # most-cited first — strongest evidence quality signal
    })
    req = urllib.request.Request(
        f"{EPMC_BASE}?{params}",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    records = []
    for item in body.get("resultList", {}).get("result", []):
        records.append({
            "epmc_id": item.get("id"),
            "source": item.get("source"),         # MED=PubMed, PPR=preprint, PAT=patent, etc.
            "title": item.get("title", ""),
            "authors": item.get("authorString", ""),
            "journal": item.get("journalTitle", ""),
            "pub_year": item.get("pubYear"),
            "cited_by_count": item.get("citedByCount", 0),
            "is_open_access": item.get("isOpenAccess") == "Y",
            "pub_type": item.get("pubTypeList", {}).get("pubType", []),
        })
    return records


def _analyze_with_bedrock(therapeutic_area: str, records: list) -> str:
    if not records:
        return ""
    try:
        preprints = [r for r in records if r.get("source") == "PPR"]
        patents = [r for r in records if r.get("source") == "PAT"]
        journal_articles = [r for r in records if r.get("source") == "MED"]
        highly_cited = sorted(records, key=lambda x: x.get("cited_by_count", 0), reverse=True)[:5]

        top_titles = [r.get("title", "") for r in highly_cited if r.get("title")]
        citation_counts = [r.get("cited_by_count", 0) for r in highly_cited]

        prompt = (
            f"You are a pharmaceutical research evidence quality analyst. "
            f"Analyze Europe PMC literature data for '{therapeutic_area}':\n\n"
            f"Total records found: {len(records)}\n"
            f"Journal articles: {len(journal_articles)}\n"
            f"Preprints (cutting-edge, not yet peer-reviewed): {len(preprints)}\n"
            f"Patents (IP landscape): {len(patents)}\n"
            f"Top 5 most-cited papers:\n"
            + "\n".join(
                f"  - \"{t}\" (cited {c} times)"
                for t, c in zip(top_titles, citation_counts)
            )
            + "\n\nIn 2-3 sentences: What does the citation landscape tell us about "
            "the maturity and evidence strength in this area? "
            "Are there emerging preprint findings that suggest new directions? "
            "What does the patent activity reveal about commercial interest? "
            "Be specific and evidence-focused."
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
