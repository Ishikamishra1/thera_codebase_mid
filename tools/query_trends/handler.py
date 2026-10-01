"""
query_trends — Trend Agent tool.

Analyzes research momentum for a therapeutic area by querying PubMed across
multiple year windows. Surfaces whether publication volume is rising or falling,
which sub-topics are accelerating, and what the research velocity signal means
for R&D investment timing.
"""
import json
import os
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
import boto3

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_REGION = os.environ.get("AWS_REGION", "us-east-1")
_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")

# Year windows to measure publication velocity
_YEAR_WINDOWS = [1, 2, 3, 5]


def lambda_handler(event, context):
    therapeutic_area = event.get("therapeutic_area", "Colorectal Cancer")

    try:
        trend_data = _fetch_publication_trends(therapeutic_area)
        recent_titles = _fetch_recent_titles(therapeutic_area, max_results=20)
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        return {
            "agent": "trend_agent",
            "tool": "query_trends",
            "therapeutic_area": therapeutic_area,
            "error": str(exc),
            "records": [],
        }

    records = _build_records(therapeutic_area, trend_data, recent_titles)
    llm_insights = _analyze_with_bedrock(therapeutic_area, trend_data, recent_titles)

    return {
        "agent": "trend_agent",
        "tool": "query_trends",
        "therapeutic_area": therapeutic_area,
        "record_count": len(records),
        "records": records,
        "llm_insights": llm_insights,
    }


def _fetch_publication_trends(therapeutic_area: str) -> dict:
    current_year = datetime.now().year
    counts = {}
    for years_back in _YEAR_WINDOWS:
        start_year = current_year - years_back
        date_range = f"{start_year}/01/01:{current_year}/12/31[pdat]"
        params = urllib.parse.urlencode({
            "db": "pubmed",
            "term": f"{therapeutic_area} AND {date_range}",
            "rettype": "count",
            "retmode": "json",
        })
        with urllib.request.urlopen(f"{EUTILS_BASE}/esearch.fcgi?{params}", timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        counts[f"last_{years_back}_year{'s' if years_back > 1 else ''}"] = int(
            body.get("esearchresult", {}).get("count", 0)
        )
    return counts


def _fetch_recent_titles(therapeutic_area: str, max_results: int) -> list:
    current_year = datetime.now().year
    date_range = f"{current_year - 1}/01/01:{current_year}/12/31[pdat]"
    params = urllib.parse.urlencode({
        "db": "pubmed",
        "term": f"{therapeutic_area} AND {date_range}",
        "retmax": max_results,
        "retmode": "json",
        "sort": "date",
    })
    with urllib.request.urlopen(f"{EUTILS_BASE}/esearch.fcgi?{params}", timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    pmids = body.get("esearchresult", {}).get("idlist", [])

    if not pmids:
        return []

    params2 = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(pmids), "retmode": "json"})
    with urllib.request.urlopen(f"{EUTILS_BASE}/esummary.fcgi?{params2}", timeout=15) as resp:
        body2 = json.loads(resp.read().decode("utf-8"))
    result = body2.get("result", {})
    return [
        {"pmid": pid, "title": result.get(pid, {}).get("title", ""), "pub_date": result.get(pid, {}).get("pubdate", "")}
        for pid in result.get("uids", [])
    ]


def _build_records(therapeutic_area: str, trend_data: dict, recent_titles: list) -> list:
    current_year = datetime.now().year

    # Compute year-over-year acceleration
    last_1 = trend_data.get("last_1_year", 0)
    last_2 = trend_data.get("last_2_years", 0)
    prev_year_count = last_2 - last_1
    yoy_change = round(((last_1 - prev_year_count) / max(prev_year_count, 1)) * 100, 1)

    records = [
        {
            "metric": "publication_counts",
            "data": trend_data,
            "year_over_year_change_pct": yoy_change,
            "momentum": "accelerating" if yoy_change > 10 else ("stable" if yoy_change > -10 else "declining"),
        }
    ]
    records += [{"type": "recent_paper", **t} for t in recent_titles[:10]]
    return records


def _analyze_with_bedrock(therapeutic_area: str, trend_data: dict, recent_titles: list) -> str:
    if not trend_data:
        return ""
    try:
        last_1 = trend_data.get("last_1_year", 0)
        last_2 = trend_data.get("last_2_years", 0)
        last_5 = trend_data.get("last_5_years", 0)
        prev_year = last_2 - last_1
        yoy = round(((last_1 - prev_year) / max(prev_year, 1)) * 100, 1)

        titles = [t.get("title", "") for t in recent_titles[:10] if t.get("title")]

        prompt = (
            f"You are a pharmaceutical research trend analyst. "
            f"Analyze research momentum for '{therapeutic_area}':\n\n"
            f"Publications last 1 year: {last_1}\n"
            f"Publications last 2 years: {last_2}\n"
            f"Publications last 5 years: {last_5}\n"
            f"Year-over-year publication change: {yoy:+.1f}%\n"
            f"Recent paper titles:\n"
            + "\n".join(f"- {t}" for t in titles)
            + "\n\nIn 2-3 sentences: Is research in this area accelerating or plateauing? "
            "What sub-topics or treatment modalities are gaining momentum? "
            "What does this trend signal for the optimal R&D investment timing — enter now or wait? "
            "Be specific and forward-looking."
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
