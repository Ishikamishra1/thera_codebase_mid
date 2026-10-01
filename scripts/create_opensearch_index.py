"""
create_opensearch_index.py — one-time setup for the TheraScout vector index.

Creates a knn_vector index in the OpenSearch Serverless collection so Bedrock
Knowledge Base can store and retrieve embeddings.

Usage:
    python scripts/create_opensearch_index.py

Requirements:
    pip install boto3 requests requests-aws4auth opensearch-py
    AWS_PROFILE=saml (or any profile with aoss:APIAccessAll)
"""
import sys
import json
import boto3
from botocore.exceptions import ClientError

COLLECTION_NAME = "therascout-vectors"
INDEX_NAME = "therascout-index"
REGION = "us-east-1"
EMBEDDING_DIM = 1536  # Amazon Titan Embed Text v1


def get_collection_endpoint(profile: str = "saml") -> str:
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    client = session.client("opensearchserverless", region_name=REGION)
    resp = client.batch_get_collection(names=[COLLECTION_NAME])
    collections = resp.get("collectionDetails", [])
    if not collections:
        print(f"[ERROR] Collection '{COLLECTION_NAME}' not found. Deploy TheraScout-Data stack first.")
        sys.exit(1)
    endpoint = collections[0].get("collectionEndpoint", "")
    if not endpoint:
        status = collections[0].get("status", "UNKNOWN")
        print(f"[ERROR] Collection endpoint not ready. Status: {status}. Wait for ACTIVE then retry.")
        sys.exit(1)
    return endpoint


def create_index(endpoint: str, profile: str = "saml"):
    try:
        from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
    except ImportError:
        print("[ERROR] opensearch-py not installed. Run: pip install opensearch-py requests-aws4auth")
        sys.exit(1)

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    credentials = session.get_credentials()

    auth = AWSV4SignerAuth(credentials, REGION, "aoss")

    host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")

    client = OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        pool_maxsize=20,
    )

    # Check if index already exists
    if client.indices.exists(index=INDEX_NAME):
        print(f"[OK] Index '{INDEX_NAME}' already exists — nothing to do.")
        return

    index_body = {
        "settings": {
            "index": {
                "knn": True,
                "knn.algo_param.ef_search": 512,
            }
        },
        "mappings": {
            "properties": {
                "embedding": {
                    "type": "knn_vector",
                    "dimension": EMBEDDING_DIM,
                    "method": {
                        "name": "hnsw",
                        "space_type": "l2",
                        "engine": "faiss",
                        "parameters": {"ef_construction": 512, "m": 16},
                    },
                },
                "text": {"type": "text"},
                "metadata": {"type": "text"},
            }
        },
    }

    resp = client.indices.create(index=INDEX_NAME, body=index_body)
    print(f"[OK] Index '{INDEX_NAME}' created: {resp}")


if __name__ == "__main__":
    import os
    profile = os.environ.get("AWS_PROFILE", "saml")
    print(f"Using AWS profile: {profile}")
    print(f"Fetching endpoint for collection '{COLLECTION_NAME}' ...")
    endpoint = get_collection_endpoint(profile)
    print(f"Collection endpoint: {endpoint}")
    print(f"Creating index '{INDEX_NAME}' ...")
    create_index(endpoint, profile)
    print("\nDone. You can now run seed_knowledge_base.py to upload documents.")
