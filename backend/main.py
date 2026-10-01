"""
TheraScout FastAPI backend — the "FastAPI backend" box in the Application
& Orchestration layer of the architecture diagram.

This is a thin layer: it does not run any agent logic itself. It starts
and polls the Step Functions execution that does the real work.
"""
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import scan
import db

app = FastAPI(
    title="TheraScout API",
    description="AI-Powered Therapeutic Opportunity Intelligence",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten before any real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scan.router, prefix="/scan", tags=["scan"])

# Apply schema on startup (no-op if DATABASE_URL not set)
db.init_schema()


@app.get("/health")
def health():
    import os
    return {
        "status": "ok",
        "db_configured": bool(os.environ.get("DATABASE_URL")),
    }
