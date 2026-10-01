"""
Database module — PostgreSQL via psycopg2.

All functions are no-ops when DATABASE_URL is not set, so the backend
works fully without a database (local dev without RDS).

For local development:
  1. Install PostgreSQL locally
  2. createdb therascout
  3. Set DATABASE_URL=postgresql://localhost/therascout in .env
  4. The schema is applied automatically on first startup.

For production (ECS in VPC → RDS):
  Set DATABASE_URL=postgresql://<user>:<password>@<rds-endpoint>:5432/therascout
  The RDS secret is in Secrets Manager; retrieve it with:
    aws secretsmanager get-secret-value --secret-id therascout_admin
"""
import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_conn = None  # module-level single connection (fine for single-process FastAPI)


def _get_conn():
    """Return a live psycopg2 connection, reconnecting if needed."""
    global _conn
    if not _DATABASE_URL:
        return None
    try:
        import psycopg2
        if _conn is None or _conn.closed:
            _conn = psycopg2.connect(_DATABASE_URL)
            _conn.autocommit = False
        # Test the connection is alive
        _conn.cursor().execute("SELECT 1")
        return _conn
    except Exception as exc:
        log.warning("DB connection failed: %s", exc)
        _conn = None
        return None


def init_schema():
    """Apply schema.sql on startup (idempotent CREATE IF NOT EXISTS)."""
    conn = _get_conn()
    if conn is None:
        if _DATABASE_URL:
            log.warning("DATABASE_URL set but connection failed — skipping schema init")
        return

    schema_path = Path(__file__).parent.parent / "data" / "schema.sql"
    if not schema_path.exists():
        log.warning("schema.sql not found at %s", schema_path)
        return

    try:
        with conn.cursor() as cur:
            cur.execute(schema_path.read_text())
        conn.commit()
        log.info("Schema initialized from %s", schema_path)
    except Exception as exc:
        conn.rollback()
        log.error("Schema init failed: %s", exc)


def save_scan(execution_arn: str, therapeutic_area: str, ranked_opportunities: dict) -> int | None:
    """Persist a completed scan and its opportunities. Returns the scan row id."""
    conn = _get_conn()
    if conn is None:
        return None

    opportunities = ranked_opportunities.get("opportunities", [])
    top = opportunities[0] if opportunities else {}

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scans
                    (execution_arn, therapeutic_area, completed_at,
                     scoring_method, bedrock_model,
                     opportunity_count, top_opportunity_name, top_opportunity_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (execution_arn) DO UPDATE SET
                    completed_at          = EXCLUDED.completed_at,
                    scoring_method        = EXCLUDED.scoring_method,
                    opportunity_count     = EXCLUDED.opportunity_count,
                    top_opportunity_name  = EXCLUDED.top_opportunity_name,
                    top_opportunity_score = EXCLUDED.top_opportunity_score
                RETURNING id
                """,
                (
                    execution_arn,
                    therapeutic_area,
                    datetime.now(timezone.utc),
                    ranked_opportunities.get("method", "unknown"),
                    ranked_opportunities.get("model", ""),
                    len(opportunities),
                    top.get("name", ""),
                    top.get("score"),
                ),
            )
            scan_id = cur.fetchone()[0]

            # Delete old opportunity rows before re-inserting (upsert pattern)
            cur.execute("DELETE FROM opportunities WHERE scan_id = %s", (scan_id,))

            for i, opp in enumerate(opportunities, start=1):
                cur.execute(
                    """
                    INSERT INTO opportunities
                        (scan_id, rank, name, score, rationale, key_evidence, dimension_scores)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        scan_id,
                        i,
                        opp.get("name", ""),
                        opp.get("score", 0),
                        opp.get("rationale", ""),
                        json.dumps(opp.get("key_evidence", [])),
                        json.dumps(opp.get("dimension_scores", {})),
                    ),
                )

        conn.commit()
        log.info("Saved scan %s (id=%s, %d opportunities)", execution_arn[-12:], scan_id, len(opportunities))
        return scan_id

    except Exception as exc:
        conn.rollback()
        log.error("save_scan failed: %s", exc)
        return None


def get_history(limit: int = 20) -> list:
    """Return recent scans, most recent first."""
    conn = _get_conn()
    if conn is None:
        return []

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT execution_arn, therapeutic_area, completed_at,
                       scoring_method, opportunity_count,
                       top_opportunity_name, top_opportunity_score
                FROM scans
                WHERE completed_at IS NOT NULL
                ORDER BY completed_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]

        # Make datetimes JSON-serialisable
        for row in rows:
            if row.get("completed_at"):
                row["completed_at"] = row["completed_at"].isoformat()
        return rows

    except Exception as exc:
        log.error("get_history failed: %s", exc)
        return []


def get_weights() -> dict:
    """Return current scoring weights from the database."""
    conn = _get_conn()
    if conn is None:
        return {}

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT dimension, weight FROM scoring_weights ORDER BY weight DESC")
            return {row[0]: row[1] for row in cur.fetchall()}
    except Exception as exc:
        log.error("get_weights failed: %s", exc)
        return {}
