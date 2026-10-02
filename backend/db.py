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


def save_feedback(execution_arn: str, opportunity_rank: int,
                  decision: str, comment: str = "", submitted_by: str = "researcher") -> bool:
    """Save a researcher's approve/reject/needs_review decision for one opportunity."""
    conn = _get_conn()
    if conn is None:
        return False

    try:
        with conn.cursor() as cur:
            # Resolve scan_id and opportunity_id from arn + rank
            cur.execute("SELECT id FROM scans WHERE execution_arn = %s", (execution_arn,))
            row = cur.fetchone()
            if not row:
                log.warning("save_feedback: scan not found for arn %s", execution_arn[-12:])
                return False
            scan_id = row[0]

            cur.execute(
                "SELECT id FROM opportunities WHERE scan_id = %s AND rank = %s",
                (scan_id, opportunity_rank),
            )
            row = cur.fetchone()
            if not row:
                log.warning("save_feedback: opportunity rank %s not found in scan %s", opportunity_rank, scan_id)
                return False
            opportunity_id = row[0]

            cur.execute(
                """
                INSERT INTO researcher_feedback
                    (opportunity_id, scan_id, decision, comment, submitted_by)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (opportunity_id, scan_id, decision, comment, submitted_by),
            )
        conn.commit()
        log.info("Feedback saved: scan=%s rank=%s decision=%s", scan_id, opportunity_rank, decision)
        return True
    except Exception as exc:
        conn.rollback()
        log.error("save_feedback failed: %s", exc)
        return False


def get_demo_scan(therapeutic_area: str = "Colorectal Cancer") -> dict | None:
    """
    Return a full pre-seeded scan (with opportunities) for demo/fallback use.
    Falls back to any scan if the requested area is not found.
    """
    conn = _get_conn()
    if conn is None:
        return None

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, execution_arn, therapeutic_area, completed_at,
                       scoring_method, bedrock_model, opportunity_count,
                       top_opportunity_name, top_opportunity_score
                FROM scans
                WHERE completed_at IS NOT NULL
                ORDER BY
                    CASE WHEN LOWER(therapeutic_area) = LOWER(%s) THEN 0 ELSE 1 END,
                    completed_at DESC
                LIMIT 1
                """,
                (therapeutic_area,),
            )
            row = cur.fetchone()
            if not row:
                return None

            cols = ["id", "execution_arn", "therapeutic_area", "completed_at",
                    "scoring_method", "bedrock_model", "opportunity_count",
                    "top_opportunity_name", "top_opportunity_score"]
            scan = dict(zip(cols, row))
            scan_id = scan.pop("id")

            cur.execute(
                """
                SELECT rank, name, score, rationale, key_evidence, dimension_scores
                FROM opportunities
                WHERE scan_id = %s
                ORDER BY rank
                """,
                (scan_id,),
            )
            opp_cols = ["rank", "name", "score", "rationale", "key_evidence", "dimension_scores"]
            opportunities = []
            for opp_row in cur.fetchall():
                opp = dict(zip(opp_cols, opp_row))
                if isinstance(opp["key_evidence"], str):
                    opp["key_evidence"] = json.loads(opp["key_evidence"])
                if isinstance(opp["dimension_scores"], str):
                    opp["dimension_scores"] = json.loads(opp["dimension_scores"])
                opportunities.append(opp)

        if scan.get("completed_at"):
            scan["completed_at"] = scan["completed_at"].isoformat()

        return {
            "scan": scan,
            "ranked_opportunities": {"opportunities": opportunities, "method": scan.get("scoring_method", "demo")},
            "execution_arn": scan["execution_arn"],
            "therapeutic_area": scan["therapeutic_area"],
        }

    except Exception as exc:
        log.error("get_demo_scan failed: %s", exc)
        return None


def get_feedback_stats() -> dict:
    """
    Return per-dimension average scores for approved vs rejected opportunities.
    Used by retrain_weights() to compute adjustment direction.
    """
    conn = _get_conn()
    if conn is None:
        return {}

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT rf.decision, o.dimension_scores
                FROM researcher_feedback rf
                JOIN opportunities o ON rf.opportunity_id = o.id
                WHERE rf.decision IN ('approved', 'rejected')
                """
            )
            rows = cur.fetchall()

        if not rows:
            return {}

        dimensions = [
            "unmet_medical_need", "disease_burden", "existing_treatment_gap",
            "scientific_evidence", "research_momentum", "competitive_landscape",
        ]
        stats: dict = {d: {"approved": [], "rejected": []} for d in dimensions}

        for decision, dim_scores in rows:
            if isinstance(dim_scores, str):
                dim_scores = json.loads(dim_scores)
            for dim in dimensions:
                val = dim_scores.get(dim)
                if val is not None:
                    stats[dim][decision].append(float(val))

        return stats
    except Exception as exc:
        log.error("get_feedback_stats failed: %s", exc)
        return {}


def retrain_weights(learning_rate: float = 0.05) -> dict:
    """
    Adjust scoring weights based on researcher feedback patterns.

    Logic:
      For each dimension, compute:
        avg_approved_score - avg_rejected_score → positive means this dimension
        correlates with researcher approval → increase its weight.
      Apply a small learning-rate nudge, then re-normalize weights to sum to 1.0.

    Returns the new weights dict (also persisted to scoring_weights table).
    """
    conn = _get_conn()
    if conn is None:
        return {}

    stats = get_feedback_stats()
    if not stats:
        return {}

    current = get_weights()
    if not current:
        return {}

    new_weights = dict(current)

    for dim, counts in stats.items():
        approved = counts.get("approved", [])
        rejected = counts.get("rejected", [])
        if not approved and not rejected:
            continue

        avg_approved = sum(approved) / len(approved) if approved else 50.0
        avg_rejected = sum(rejected) / len(rejected) if rejected else 50.0

        # Positive delta → dimension predicted approval → bump weight up
        delta = (avg_approved - avg_rejected) / 100.0   # normalize 0–1 range
        new_weights[dim] = max(0.01, new_weights.get(dim, 0.10) + learning_rate * delta)

    # Re-normalize so weights sum to exactly 1.0
    total = sum(new_weights.values())
    new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}

    # Persist back to DB
    try:
        with conn.cursor() as cur:
            for dim, weight in new_weights.items():
                cur.execute(
                    """
                    INSERT INTO scoring_weights (dimension, weight, updated_at, updated_by)
                    VALUES (%s, %s, NOW(), 'retrain')
                    ON CONFLICT (dimension) DO UPDATE SET
                        weight     = EXCLUDED.weight,
                        updated_at = EXCLUDED.updated_at,
                        updated_by = EXCLUDED.updated_by
                    """,
                    (dim, weight),
                )
        conn.commit()
        log.info("Weights retrained: %s", new_weights)
    except Exception as exc:
        conn.rollback()
        log.error("retrain_weights persist failed: %s", exc)

    return new_weights
