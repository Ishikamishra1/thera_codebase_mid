"""
query_knowledge_base — Enterprise Knowledge Base Agent tool.

Uses Amazon Bedrock's retrieve_and_generate API to do RAG over the
TheraScout Knowledge Base — documents uploaded to S3 under the kb/ prefix
(internal research reports, clinical guidelines, drug pipeline docs, etc.).

If KNOWLEDGE_BASE_ID is not set (KB not yet deployed or synced), returns
an empty result with a clear note so the pipeline still succeeds.

Setup required before this agent returns real results:
  1. Deploy TheraScout-KnowledgeBase CDK stack
  2. Create the OpenSearch index (see knowledge_base_stack.py docstring)
  3. Upload documents: aws s3 cp <file> s3://<bucket>/kb/
  4. Sync: aws bedrock-agent start-ingestion-job --knowledge-base-id <KB_ID> --data-source-id <DS_ID>
  5. Set KNOWLEDGE_BASE_ID env var (auto-injected by CDK after deploy)
"""
import json
import os
import boto3
from botocore.exceptions import ClientError

_REGION = os.environ.get("AWS_REGION", "us-east-1")
_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")
_KB_ID = os.environ.get("KNOWLEDGE_BASE_ID", "")

# Full model ARN required by retrieve_and_generate
_MODEL_ARN = f"arn:aws:bedrock:{_REGION}::foundation-model/{_MODEL_ID}"


def lambda_handler(event, context):
    therapeutic_area = event.get("therapeutic_area", "Colorectal Cancer")

    if not _KB_ID:
        return {
            "agent": "enterprise_kb_agent",
            "tool": "query_knowledge_base",
            "therapeutic_area": therapeutic_area,
            "record_count": 0,
            "records": [],
            "llm_insights": (
                "Knowledge Base not configured yet. Upload documents to S3 kb/ prefix "
                "and run an ingestion job to enable enterprise RAG retrieval."
            ),
            "kb_status": "not_configured",
        }

    try:
        client = boto3.client("bedrock-agent-runtime", region_name=_REGION)

        # Query 1 — unmet needs and treatment gaps
        unmet_need_result = _retrieve_and_generate(
            client,
            f"What are the key unmet medical needs, treatment gaps, and underserved "
            f"patient populations for {therapeutic_area}?",
        )

        # Query 2 — emerging research directions
        emerging_result = _retrieve_and_generate(
            client,
            f"What emerging research directions, novel drug targets, or innovative "
            f"treatment approaches show the most promise for {therapeutic_area}?",
        )

        records = [
            {
                "query": "unmet_needs",
                "answer": unmet_need_result["answer"],
                "citations": unmet_need_result["citations"],
            },
            {
                "query": "emerging_directions",
                "answer": emerging_result["answer"],
                "citations": emerging_result["citations"],
            },
        ]

        combined_insight = (
            f"Unmet needs: {unmet_need_result['answer']}\n\n"
            f"Emerging directions: {emerging_result['answer']}"
        )

        return {
            "agent": "enterprise_kb_agent",
            "tool": "query_knowledge_base",
            "therapeutic_area": therapeutic_area,
            "record_count": len(records),
            "records": records,
            "llm_insights": combined_insight,
            "kb_status": "active",
        }

    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        return {
            "agent": "enterprise_kb_agent",
            "tool": "query_knowledge_base",
            "therapeutic_area": therapeutic_area,
            "record_count": 0,
            "records": [],
            "llm_insights": "",
            "kb_status": "error",
            "error": f"{error_code}: {exc.response['Error']['Message']}",
        }


def _retrieve_and_generate(client, query: str) -> dict:
    resp = client.retrieve_and_generate(
        input={"text": query},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": _KB_ID,
                "modelArn": _MODEL_ARN,
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {"numberOfResults": 5}
                },
            },
        },
    )

    answer = resp.get("output", {}).get("text", "")

    # Extract source document citations
    citations = []
    for citation in resp.get("citations", []):
        for ref in citation.get("retrievedReferences", []):
            loc = ref.get("location", {}).get("s3Location", {})
            citations.append({
                "source": loc.get("uri", ""),
                "excerpt": ref.get("content", {}).get("text", "")[:200],
            })

    return {"answer": answer, "citations": citations}
