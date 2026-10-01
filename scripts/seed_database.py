"""
seed_database.py — pre-populate RDS with demo scan history and researcher
feedback so the feedback loop is visually demonstrable on day one.

What this creates:
  - 3 completed scans (Colorectal Cancer, Lung Cancer, Breast Cancer)
  - 5 ranked opportunities per scan (15 total)
  - 20 researcher feedback decisions with a realistic approval pattern:
      * Researchers approve opportunities with high unmet need + disease burden
      * Researchers reject opportunities that are crowded with low evidence
      * Researchers flag mixed-signal opportunities for review
  - After seeding, clicking "Retrain weights" shifts weights visibly:
      unmet_medical_need: 30% → ~33%
      disease_burden:     20% → ~22%
      competitive_landscape: 5% → ~3%

Usage:
    python scripts/seed_database.py
    # Requires DATABASE_URL in .env or environment
    # pip install psycopg2-binary python-dotenv

Requirements:
    pip install psycopg2-binary python-dotenv
"""
import os
import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Load .env from project root
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_conn():
    if not DATABASE_URL:
        print("[ERROR] DATABASE_URL not set.")
        print("  Add DATABASE_URL=postgresql://localhost/therascout to .env")
        print("  Then: createdb therascout  (if running locally)")
        sys.exit(1)
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    except Exception as e:
        print(f"[ERROR] Cannot connect to database: {e}")
        sys.exit(1)


# ── Demo scan data ─────────────────────────────────────────────────────────────

DEMO_SCANS = [
    {
        "execution_arn": "arn:aws:states:us-east-1:446205069645:execution:TheraScoutOpportunityScan:demo-colorectal-001",
        "therapeutic_area": "Colorectal Cancer",
        "days_ago": 14,
        "scoring_method": "bedrock_llm",
        "bedrock_model": "amazon.nova-pro-v1:0",
        "opportunities": [
            {
                "rank": 1, "name": "Targeted therapies for KRAS-mutant metastatic CRC",
                "score": 91.0, "rationale": "KRAS mutations in ~45% of CRC patients represent a large underserved population. Recent KRAS G12C inhibitor approvals in lung cancer validate the mechanism. First-line metastatic CRC has poor outcomes with current chemotherapy backbones.",
                "key_evidence": ["KRAS G12C inhibitor sotorasib shows 36% ORR in NSCLC", "~45% of mCRC patients carry KRAS mutations", "mCRC median OS <24 months on standard chemo"],
                "dimension_scores": {"unmet_medical_need": 95, "disease_burden": 88, "existing_treatment_gap": 92, "scientific_evidence": 82, "research_momentum": 78, "competitive_landscape": 45},
            },
            {
                "rank": 2, "name": "Immunotherapy combinations for MSI-H/dMMR CRC",
                "score": 87.0, "rationale": "MSI-H CRC responds exceptionally to PD-1 blockade but represents only 15% of cases. Combination strategies to expand eligibility to MSS tumors represent a major unmet need.",
                "key_evidence": ["Pembrolizumab approved first-line for MSI-H mCRC", "MSS CRC (~85%) shows minimal response to checkpoint inhibitors", "Phase III KEYNOTE-177: 16.5 months PFS vs 8.2 months"],
                "dimension_scores": {"unmet_medical_need": 90, "disease_burden": 85, "existing_treatment_gap": 88, "scientific_evidence": 79, "research_momentum": 82, "competitive_landscape": 52},
            },
            {
                "rank": 3, "name": "Liquid biopsy for early-stage CRC detection",
                "score": 84.0, "rationale": "Colonoscopy compliance is low globally. Liquid biopsy ctDNA tests could transform population-level screening and dramatically improve Stage I detection rates where 5-year survival exceeds 90%.",
                "key_evidence": ["5-year survival Stage I CRC: 91% vs Stage IV: 14%", "Colonoscopy screening compliance <40% in most populations", "Guardant Shield ctDNA test: 83% sensitivity for Stage I-II CRC"],
                "dimension_scores": {"unmet_medical_need": 88, "disease_burden": 90, "existing_treatment_gap": 85, "scientific_evidence": 72, "research_momentum": 88, "competitive_landscape": 38},
            },
            {
                "rank": 4, "name": "ADC therapies targeting CEACAM5 in CRC",
                "score": 76.0, "rationale": "Antibody-drug conjugates targeting CEA-related cell adhesion molecule show early promise with tolerable safety profiles. However, early Phase II data is limited and competitive ADC space is crowded.",
                "key_evidence": ["Tusamitamab ravtansine Phase II: 22% ORR in third-line mCRC", "CEACAM5 overexpressed in ~90% of CRC tumors", "Multiple ADC programs targeting same antigen in development"],
                "dimension_scores": {"unmet_medical_need": 75, "disease_burden": 80, "existing_treatment_gap": 70, "scientific_evidence": 55, "research_momentum": 65, "competitive_landscape": 30},
            },
            {
                "rank": 5, "name": "Microbiome modulation to enhance CRC immunotherapy response",
                "score": 61.0, "rationale": "Emerging evidence links gut microbiome composition to checkpoint inhibitor response. However, clinical evidence is early-stage and mechanism of action poorly understood.",
                "key_evidence": ["Fusobacterium nucleatum correlated with CRC progression", "Microbiome diversity associated with anti-PD1 response", "Phase I FMT trials ongoing but results preliminary"],
                "dimension_scores": {"unmet_medical_need": 70, "disease_burden": 75, "existing_treatment_gap": 65, "scientific_evidence": 35, "research_momentum": 55, "competitive_landscape": 70},
            },
        ],
    },
    {
        "execution_arn": "arn:aws:states:us-east-1:446205069645:execution:TheraScoutOpportunityScan:demo-lung-001",
        "therapeutic_area": "Non-Small Cell Lung Cancer",
        "days_ago": 7,
        "scoring_method": "bedrock_llm",
        "bedrock_model": "amazon.nova-pro-v1:0",
        "opportunities": [
            {
                "rank": 1, "name": "EGFR exon 20 insertion targeted therapy",
                "score": 89.0, "rationale": "EGFR exon 20 insertions (~2-3% of NSCLC) were historically undruggable. Amivantamab approval validates the target but duration of response remains limited and combination strategies are needed.",
                "key_evidence": ["EGFR ex20ins: ~2-3% of NSCLC, ~25,000 new US cases/year", "Amivantamab approved 2021: 40% ORR but median DOR 11.1 months", "No approved second-line options post-amivantamab"],
                "dimension_scores": {"unmet_medical_need": 92, "disease_burden": 82, "existing_treatment_gap": 90, "scientific_evidence": 75, "research_momentum": 80, "competitive_landscape": 55},
            },
            {
                "rank": 2, "name": "STK11/KEAP1 co-mutation overcoming immunotherapy resistance",
                "score": 85.0, "rationale": "STK11 and KEAP1 co-mutations predict resistance to checkpoint inhibitors in KRAS-mutant NSCLC — a patient population with very limited options.",
                "key_evidence": ["STK11 mutations in ~15-20% of NSCLC", "Primary ICI resistance in STK11-mutant tumors", "No approved therapy specifically for this subgroup"],
                "dimension_scores": {"unmet_medical_need": 90, "disease_burden": 80, "existing_treatment_gap": 88, "scientific_evidence": 68, "research_momentum": 72, "competitive_landscape": 60},
            },
            {
                "rank": 3, "name": "HER3-targeted ADC for EGFR-mutant NSCLC post-osimertinib",
                "score": 82.0, "rationale": "After osimertinib progression, treatment options are limited. HER3 is overexpressed in EGFR-mutant tumors and patritumab deruxtecan shows promising early data.",
                "key_evidence": ["Patritumab deruxtecan: 39% ORR in EGFR-mutant post-osimertinib", "HER3 expressed in ~80% EGFR-mutant NSCLC", "Phase III HERTHENA-Lung02 recruiting"],
                "dimension_scores": {"unmet_medical_need": 85, "disease_burden": 85, "existing_treatment_gap": 82, "scientific_evidence": 70, "research_momentum": 78, "competitive_landscape": 48},
            },
            {
                "rank": 4, "name": "VEGFR/FGFR dual inhibition for squamous NSCLC",
                "score": 70.0, "rationale": "Squamous NSCLC lacks targetable driver mutations in most patients. Antiangiogenic combinations have shown modest benefit but no strong Phase III validation.",
                "key_evidence": ["Squamous NSCLC: ~25-30% of all NSCLC", "Limited targeted options vs adenocarcinoma", "Docetaxel + ramucirumab 2nd-line: modest OS benefit"],
                "dimension_scores": {"unmet_medical_need": 78, "disease_burden": 82, "existing_treatment_gap": 72, "scientific_evidence": 50, "research_momentum": 58, "competitive_landscape": 42},
            },
            {
                "rank": 5, "name": "AI-guided radiotherapy optimization for oligometastatic NSCLC",
                "score": 58.0, "rationale": "AI-driven SBRT planning could improve outcomes in oligometastatic disease. Evidence base is thin and requires multidisciplinary infrastructure.",
                "key_evidence": ["Oligometastatic NSCLC ~20-30% of Stage IV", "SBRT shows OS signal in randomized trials", "AI contouring tools immature for broad clinical adoption"],
                "dimension_scores": {"unmet_medical_need": 65, "disease_burden": 78, "existing_treatment_gap": 60, "scientific_evidence": 40, "research_momentum": 50, "competitive_landscape": 55},
            },
        ],
    },
    {
        "execution_arn": "arn:aws:states:us-east-1:446205069645:execution:TheraScoutOpportunityScan:demo-breast-001",
        "therapeutic_area": "Breast Cancer",
        "days_ago": 3,
        "scoring_method": "bedrock_llm",
        "bedrock_model": "amazon.nova-pro-v1:0",
        "opportunities": [
            {
                "rank": 1, "name": "PI3K/AKT pathway inhibition for HR+/HER2- breast cancer",
                "score": 88.0, "rationale": "PIK3CA mutations (~40% of HR+/HER2- metastatic breast cancer) are actionable. Alpelisib approval validates the target but toxicity limits use. Next-gen selective inhibitors needed.",
                "key_evidence": ["PIK3CA mutated in ~40% HR+/HER2- mBC", "Alpelisib + fulvestrant: median PFS 11.0 vs 5.7 months", "Grade 3/4 hyperglycemia in 36% limits real-world uptake"],
                "dimension_scores": {"unmet_medical_need": 88, "disease_burden": 90, "existing_treatment_gap": 85, "scientific_evidence": 80, "research_momentum": 82, "competitive_landscape": 40},
            },
            {
                "rank": 2, "name": "TROP2-targeted ADC for triple-negative breast cancer",
                "score": 86.0, "rationale": "TNBC has the worst prognosis of breast cancer subtypes. Sacituzumab govitecan approval validates TROP2 ADC but PD-L1 negative patients show limited benefit.",
                "key_evidence": ["TNBC: 15-20% of breast cancer, worst 5-year survival", "Sacituzumab govitecan: 35% ORR in heavily pretreated TNBC", "PD-L1 negative TNBC: significant unmet need remains"],
                "dimension_scores": {"unmet_medical_need": 92, "disease_burden": 85, "existing_treatment_gap": 88, "scientific_evidence": 78, "research_momentum": 80, "competitive_landscape": 35},
            },
            {
                "rank": 3, "name": "CDK4/6 inhibitor combinations to overcome endocrine resistance",
                "score": 80.0, "rationale": "CDK4/6 inhibitors are standard in HR+ mBC but virtually all patients develop resistance. Understanding and overcoming resistance mechanisms is a major research priority.",
                "key_evidence": ["CDK4/6i resistance develops in ~100% at median 24-28 months", "RB1 loss identified as key resistance mechanism", "Multiple next-gen approaches in Phase II"],
                "dimension_scores": {"unmet_medical_need": 85, "disease_burden": 88, "existing_treatment_gap": 80, "scientific_evidence": 72, "research_momentum": 75, "competitive_landscape": 38},
            },
            {
                "rank": 4, "name": "Neoadjuvant immunotherapy for early TNBC",
                "score": 73.0, "rationale": "Pembrolizumab + chemo is approved neoadjuvant but pCR rate still only ~65%. Identifying predictive biomarkers for responders vs non-responders is unresolved.",
                "key_evidence": ["KEYNOTE-522: pCR 64.8% with pembro vs 51.2% chemo alone", "No validated biomarker beyond PD-L1 CPS", "Immune-related AEs add treatment complexity"],
                "dimension_scores": {"unmet_medical_need": 75, "disease_burden": 80, "existing_treatment_gap": 72, "scientific_evidence": 65, "research_momentum": 70, "competitive_landscape": 45},
            },
            {
                "rank": 5, "name": "Gut microbiome-based prediction of CDK4/6i toxicity",
                "score": 52.0, "rationale": "Emerging research suggests gut microbiome composition correlates with CDK4/6 inhibitor GI toxicity. Very early-stage with limited clinical evidence.",
                "key_evidence": ["Pilot studies show microbiome diversity correlates with palbociclib GI AEs", "No prospective validation data", "Mechanism incompletely characterized"],
                "dimension_scores": {"unmet_medical_need": 60, "disease_burden": 70, "existing_treatment_gap": 55, "scientific_evidence": 28, "research_momentum": 45, "competitive_landscape": 65},
            },
        ],
    },
]

# ── Feedback decisions (designed to shift weights visibly on retrain) ──────────
# Pattern: approve = high unmet need + high burden, reject = low evidence + crowded

FEEDBACK_DECISIONS = [
    # Colorectal Cancer scan
    {"scan_idx": 0, "rank": 1, "decision": "approved",      "comment": "Strong mechanistic rationale. KRAS target validated in lung — clear path to CRC."},
    {"scan_idx": 0, "rank": 2, "decision": "approved",      "comment": "MSS combination strategy is the right question. Prioritize."},
    {"scan_idx": 0, "rank": 3, "decision": "approved",      "comment": "Liquid biopsy is a platform play. High impact on early detection."},
    {"scan_idx": 0, "rank": 4, "decision": "needs_review",  "comment": "ADC data promising but crowded space. Need competitive differentiation analysis."},
    {"scan_idx": 0, "rank": 5, "decision": "rejected",      "comment": "Microbiome evidence too early. Mechanism unclear. Revisit in 2 years."},

    # Lung Cancer scan
    {"scan_idx": 1, "rank": 1, "decision": "approved",      "comment": "Rare mutation, high unmet need, validated mechanism. Fast-follow strategy feasible."},
    {"scan_idx": 1, "rank": 2, "decision": "approved",      "comment": "STK11 co-mutation is an underserved population. Strong rationale for combination approach."},
    {"scan_idx": 1, "rank": 3, "decision": "approved",      "comment": "Post-osimertinib space is real. HER3 ADC data compelling."},
    {"scan_idx": 1, "rank": 4, "decision": "needs_review",  "comment": "Squamous unmet need is real but mechanism needs strengthening."},
    {"scan_idx": 1, "rank": 5, "decision": "rejected",      "comment": "AI radiotherapy is infrastructure, not a drug. Out of scope for R&D prioritization."},

    # Breast Cancer scan
    {"scan_idx": 2, "rank": 1, "decision": "approved",      "comment": "Next-gen PI3K selectivity improvement is a clear unmet need. Toxicity is the bottleneck."},
    {"scan_idx": 2, "rank": 2, "decision": "approved",      "comment": "TNBC unmet need is extremely high. TROP2 second-gen ADC with better TI is priority."},
    {"scan_idx": 2, "rank": 3, "decision": "approved",      "comment": "CDK4/6 resistance is inevitable. Mechanism-based combination is the right investment."},
    {"scan_idx": 2, "rank": 4, "decision": "needs_review",  "comment": "Biomarker question important but evidence base needs more maturity."},
    {"scan_idx": 2, "rank": 5, "decision": "rejected",      "comment": "Microbiome-toxicity link is speculative. Not actionable with current evidence."},
]


# ── Database operations ────────────────────────────────────────────────────────

def apply_schema(conn):
    schema_path = Path(__file__).parent.parent / "data" / "schema.sql"
    if not schema_path.exists():
        print(f"[WARN] schema.sql not found at {schema_path} — skipping schema init")
        return
    with conn.cursor() as cur:
        cur.execute(schema_path.read_text())
    conn.commit()
    print("[OK] Schema applied")


def insert_scan(conn, scan: dict) -> int:
    completed = datetime.now(timezone.utc) - timedelta(days=scan["days_ago"])
    opps = scan["opportunities"]
    top = opps[0]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO scans
                (execution_arn, therapeutic_area, completed_at,
                 scoring_method, bedrock_model, opportunity_count,
                 top_opportunity_name, top_opportunity_score)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (execution_arn) DO UPDATE SET
                completed_at          = EXCLUDED.completed_at,
                opportunity_count     = EXCLUDED.opportunity_count,
                top_opportunity_name  = EXCLUDED.top_opportunity_name,
                top_opportunity_score = EXCLUDED.top_opportunity_score
            RETURNING id
            """,
            (
                scan["execution_arn"], scan["therapeutic_area"], completed,
                scan["scoring_method"], scan["bedrock_model"],
                len(opps), top["name"], top["score"],
            ),
        )
        scan_id = cur.fetchone()[0]
    conn.commit()
    return scan_id


def insert_opportunities(conn, scan_id: int, opps: list) -> dict:
    """Returns {rank: opportunity_id}"""
    rank_to_id = {}
    with conn.cursor() as cur:
        cur.execute("DELETE FROM opportunities WHERE scan_id = %s", (scan_id,))
        for opp in opps:
            cur.execute(
                """
                INSERT INTO opportunities
                    (scan_id, rank, name, score, rationale, key_evidence, dimension_scores)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    scan_id, opp["rank"], opp["name"], opp["score"],
                    opp["rationale"],
                    json.dumps(opp["key_evidence"]),
                    json.dumps(opp["dimension_scores"]),
                ),
            )
            rank_to_id[opp["rank"]] = cur.fetchone()[0]
    conn.commit()
    return rank_to_id


def insert_feedback(conn, opp_id: int, scan_id: int, decision: str, comment: str):
    with conn.cursor() as cur:
        # Avoid duplicates
        cur.execute(
            "SELECT id FROM researcher_feedback WHERE opportunity_id=%s AND decision=%s",
            (opp_id, decision),
        )
        if cur.fetchone():
            return
        cur.execute(
            """
            INSERT INTO researcher_feedback
                (opportunity_id, scan_id, decision, comment, submitted_by)
            VALUES (%s,%s,%s,%s,%s)
            """,
            (opp_id, scan_id, decision, comment, "demo_seed"),
        )
    conn.commit()


def print_weight_preview(conn):
    """Show what weights look like before and after a retrain."""
    with conn.cursor() as cur:
        cur.execute("SELECT dimension, weight FROM scoring_weights ORDER BY weight DESC")
        current = {r[0]: r[1] for r in cur.fetchall()}

    print("\n  Current weights (before retrain):")
    for dim, w in sorted(current.items(), key=lambda x: -x[1]):
        bar = "█" * int(w * 60)
        print(f"    {dim:<30} {w*100:5.1f}%  {bar}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to database...")
    conn = get_conn()

    print("Applying schema...")
    apply_schema(conn)

    scan_ids = []
    rank_to_opp_ids = []

    for i, scan in enumerate(DEMO_SCANS):
        print(f"\nInserting scan: {scan['therapeutic_area']} ({scan['days_ago']} days ago)")
        scan_id = insert_scan(conn, scan)
        rank_map = insert_opportunities(conn, scan_id, scan["opportunities"])
        scan_ids.append(scan_id)
        rank_to_opp_ids.append(rank_map)
        print(f"  scan_id={scan_id}, {len(scan['opportunities'])} opportunities")

    print(f"\nInserting {len(FEEDBACK_DECISIONS)} researcher feedback decisions...")
    for fb in FEEDBACK_DECISIONS:
        scan_id   = scan_ids[fb["scan_idx"]]
        rank_map  = rank_to_opp_ids[fb["scan_idx"]]
        opp_id    = rank_map.get(fb["rank"])
        if not opp_id:
            print(f"  [warn] rank {fb['rank']} not found in scan {scan_id}")
            continue
        insert_feedback(conn, opp_id, scan_id, fb["decision"], fb["comment"])
        icon = {"approved": "✓", "rejected": "✗", "needs_review": "⚠"}[fb["decision"]]
        area = DEMO_SCANS[fb["scan_idx"]]["therapeutic_area"].split()[0]
        opp_name = DEMO_SCANS[fb["scan_idx"]]["opportunities"][fb["rank"]-1]["name"][:50]
        print(f"  {icon} [{area}] Rank {fb['rank']}: {opp_name}...")

    print_weight_preview(conn)
    conn.close()

    print("\n" + "="*60)
    print("Database seeded successfully!")
    print("="*60)
    print("\nDemo instructions:")
    print("  1. Start the backend: cd backend && python -m uvicorn main:app --reload")
    print("  2. Open the frontend and scroll to 'View past scans'")
    print("  3. You will see 3 historical scans with scores")
    print("  4. Click 'Retrain scoring weights from feedback'")
    print("  5. Weights shift visibly — e.g. unmet_medical_need rises to ~33%")
    print("     competitive_landscape drops to ~3%")


if __name__ == "__main__":
    main()
