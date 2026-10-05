# TheraScout

**Agentic AI platform for pharmaceutical R&D opportunity prioritization.**

TheraScout takes a therapeutic area (e.g., Colorectal Cancer) and deploys **eight specialized AI agents** in parallel to gather data from GLOBOCAN, PubMed, ClinicalTrials.gov, openFDA, Europe PMC, and an enterprise RAG knowledge base. Amazon Nova Pro (via Bedrock) synthesizes the findings and ranks the **Top 5 therapeutic research opportunities** using a six-dimension weighted scoring model.

---

## Architecture

```
User selects therapeutic area
        │
        ▼
FastAPI Backend (scan.py)
        │  starts execution
        ▼
AWS Step Functions ── parallel ──┬── Disease Agent         (GLOBOCAN / WHO data)
                                 ├── Treatment Agent      (openFDA drug labels)
                                 ├── Research Agent       (PubMed publications)
                                 ├── Clinical Trial Agent (ClinicalTrials.gov)
                                 ├── Competition Agent    (trial sponsor analysis)
                                 ├── Trends Agent         (PubMed velocity / momentum)
                                 ├── Europe PMC Agent     (European research corpus)
                                 └── Enterprise KB Agent  (Bedrock RAG knowledge base)
                                              │
                                              ▼
                              Amazon Nova Pro (Bedrock Converse API)
                              Scores & ranks Top 5 opportunities
                                              │
                                              ▼
                              React Frontend — card-based results UI
```

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.10+ | Backend + CDK |
| Node.js | 18+ | Frontend (Vite + React) |
| AWS CDK | 2.x | `npm install -g aws-cdk` |
| saml2aws | 2.36.x | For Cognizant SSO login |
| AWS CLI | 2.x | Used by CDK and boto3 |

---

## AWS Setup (one-time)

### 1. Refresh your SAML token

Your AWS credentials expire every ~6 hours. Before running the backend or deploying, log in:

```powershell
cd "C:\Users\2469312\OneDrive - Cognizant\Desktop\build a thon\saml2aws_2.36.19_windows_amd64"
.\saml2aws.exe login
```

This writes temporary credentials to the `saml` profile in `~/.aws/credentials`.

### 2. Full deploy (infrastructure + Fargate backend)

```powershell
# First time only (bootstraps CDK):
.\deploy.ps1 -AccountId 446205069645 -Bootstrap

# Subsequent deploys:
.\deploy.ps1 -AccountId 446205069645
```

`deploy.ps1` will:
1. Bundle Lambda dependencies
2. Deploy all CDK stacks (Data → KB → Agents → Orchestration → **Fargate backend**)
3. Build the Docker image and push it to ECR automatically
4. Patch `.env` and `frontend/.env` with the live ALB URL

After deploy the script prints:
```
Live backend:  http://<alb-dns>.us-east-1.elb.amazonaws.com
Swagger UI:    http://<alb-dns>.us-east-1.elb.amazonaws.com/docs
```

Set `VITE_API_BASE` in `frontend/.env` to the printed URL, then `npm run build`.

---

## Running Locally

### Step 1 — Environment files

```bash
# Root .env (backend + AWS)
cp .env.example .env
# Edit .env: set STATE_MACHINE_ARN from cdk_outputs.json after deploying

# Frontend .env
cp frontend/.env.example frontend/.env
# Default VITE_API_BASE=http://localhost:8000 works for local dev
```

### Step 2 — Backend

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --reload
```

Backend runs at `http://localhost:8000`.  
Swagger docs: `http://localhost:8000/docs`

> **Token expired?** Run `saml2aws login` (Step 1 above) and restart the backend — no `--reload` needed, restart fully.

### Step 3 — Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend runs at `http://localhost:5173`.

---

## Scoring Model

Each opportunity is scored across six dimensions:

| Dimension | Weight |
|---|---|
| Unmet Medical Need | 30% |
| Disease Burden | 20% |
| Existing Treatment Gap | 20% |
| Scientific Evidence | 15% |
| Research Momentum | 10% |
| Competitive Landscape | 5% |

Scoring is performed by **Amazon Nova Pro** (`amazon.nova-pro-v1:0`) via the Bedrock Converse API from the FastAPI backend (SAML credentials). A deterministic fallback runs if Bedrock is unavailable.

---

## Database Setup (one-time — seed demo feedback data)

Pre-populate the database with 3 historical scans, 15 opportunities, and 20 researcher
feedback decisions so the "Retrain scoring weights" button shows a visible weight change
on day one of the demo:

```bash
# Install script dependencies (if not already done)
pip install -r scripts/requirements.txt

# DATABASE_URL must be set in .env
# Local: DATABASE_URL=postgresql://localhost/therascout
# AWS RDS: DATABASE_URL=<value from SecretsManager after deploy>

python scripts/seed_database.py
```

After running, open the frontend → "View past scans" → click **"Retrain scoring weights from feedback"**.
You will see weights shift: `unmet_medical_need` rises to ~33%, `competitive_landscape` drops to ~3%.

---

## Knowledge Base Setup (RAG — one-time after deploy)

```bash
# Install script dependencies
pip install -r scripts/requirements.txt

# Step 1 — create the OpenSearch knn_vector index
python scripts/create_opensearch_index.py

# Step 2 — seed with real research documents + trigger ingestion
# (get values from cdk_outputs.json after cdk deploy --all)
python scripts/seed_knowledge_base.py \
  --bucket <TheraScout-Data bucket name> \
  --kb-id  <KnowledgeBaseId from cdk_outputs> \
  --ds-id  <DataSourceId from cdk_outputs>

# Step 3 — add to .env and restart backend
# KNOWLEDGE_BASE_ID=<KnowledgeBaseId>
```

The seeder uploads **21 documents** across 5 cancer types — disease burden,
live PubMed abstracts, trial pipeline, treatment landscape, and scoring
methodology — then triggers Bedrock ingestion automatically (takes ~2-5 min).

---

## Data Sources

| Agent | Source |
|---|---|
| Disease Agent | IARC GLOBOCAN 2022 (WHO cancer burden data) |
| Treatment Agent | openFDA drug label API |
| Research Agent | PubMed / NCBI Entrez API |
| Clinical Trial Agent | ClinicalTrials.gov API v2 |
| Competition Agent | ClinicalTrials.gov — trial sponsor aggregation |
| Trends Agent | PubMed — publication velocity across 1/2/3/5-year windows |
| Europe PMC Agent | Europe PMC full-text research corpus |
| Enterprise KB Agent | Amazon Bedrock RAG — internal research knowledge base |

---

## Project Structure

```
TheraScout_Project/
├── backend/              # FastAPI app
│   ├── main.py
│   └── routers/
│       └── scan.py       # Step Functions + Bedrock scoring
├── frontend/             # React + Vite
│   └── src/App.jsx       # Card-based results UI
├── tools/                # Lambda handler functions
│   ├── query_globocan_data/
│   ├── query_pubmed/
│   ├── query_clinicaltrials/
│   ├── query_openfda/
│   ├── score_opportunities_fallback/
│   └── compose_pdf_report/
├── infra/                # AWS CDK stacks
│   └── stacks/
│       ├── data_stack.py
│       └── agent_stack.py
├── .env.example          # Copy to .env — never commit .env
└── README.md
```

---

## Disclaimer

TheraScout output is a **decision-support draft**, not a clinical prediction, regulatory guidance, or guarantee of drug success. A human researcher must review and validate all results before any resource is committed.
