import { useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

const COLORS = {
  bg: "#0f1923",
  card: "#1a2632",
  accent: "#00b4d8",
  accent2: "#48cae4",
  text: "#e0f0f8",
  muted: "#7fa8bc",
  border: "#2a3f52",
  success: "#22c55e",
  warn: "#f59e0b",
  danger: "#ef4444",
};

function ScoreBar({ score }) {
  const pct = Math.min(100, Math.max(0, score));
  const color = pct >= 70 ? COLORS.success : pct >= 45 ? COLORS.warn : COLORS.danger;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{
        flex: 1, height: 8, background: "#2a3f52", borderRadius: 4, overflow: "hidden",
      }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 4, transition: "width 0.6s ease" }} />
      </div>
      <span style={{ minWidth: 36, fontWeight: 700, color, fontSize: 14 }}>{Math.round(pct)}</span>
    </div>
  );
}

function DimScores({ dims }) {
  if (!dims) return null;
  const labels = {
    unmet_medical_need: "Unmet need",
    disease_burden: "Disease burden",
    existing_treatment_gap: "Treatment gap",
    scientific_evidence: "Scientific evidence",
    research_momentum: "Research momentum",
    competitive_landscape: "Competitive space",
  };
  return (
    <div style={{ marginTop: 12, display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 16px" }}>
      {Object.entries(dims).map(([k, v]) => (
        <div key={k}>
          <div style={{ fontSize: 11, color: COLORS.muted, marginBottom: 2 }}>{labels[k] || k}</div>
          <ScoreBar score={v} />
        </div>
      ))}
    </div>
  );
}

function AskAI({ opp, therapeuticArea }) {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);

  async function ask() {
    const q = question.trim();
    if (!q || loading) return;
    setLoading(true);
    setHistory(h => [...h, { role: "user", text: q }]);
    setQuestion("");
    try {
      const res = await fetch(`${API_BASE}/scan/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ opportunity: opp, question: q, therapeutic_area: therapeuticArea }),
      });
      const data = await res.json();
      setHistory(h => [...h, { role: "ai", text: data.answer || data.detail || "No response." }]);
    } catch (e) {
      setHistory(h => [...h, { role: "ai", text: "Error: " + e.message }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ marginTop: 12 }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          background: open ? COLORS.accent : "none",
          border: `1px solid ${COLORS.accent}`,
          color: open ? "#0f1923" : COLORS.accent,
          cursor: "pointer", borderRadius: 6, padding: "5px 12px",
          fontSize: 12, fontWeight: 600, transition: "all 0.2s",
        }}
      >
        Ask AI Why?
      </button>

      {open && (
        <div style={{
          marginTop: 10, background: "#0f1923",
          border: `1px solid ${COLORS.border}`, borderRadius: 8, padding: 14,
        }}>
          {history.length === 0 && (
            <p style={{ fontSize: 12, color: COLORS.muted, margin: "0 0 10px" }}>
              Ask anything about this opportunity — why it was ranked here, what drives a specific score, what to investigate next.
            </p>
          )}

          {history.map((msg, i) => (
            <div key={i} style={{
              marginBottom: 10,
              display: "flex",
              flexDirection: "column",
              alignItems: msg.role === "user" ? "flex-end" : "flex-start",
            }}>
              <div style={{
                maxWidth: "88%", padding: "8px 12px", borderRadius: 8, fontSize: 13,
                background: msg.role === "user" ? COLORS.accent : COLORS.card,
                color: msg.role === "user" ? "#0f1923" : COLORS.text,
                lineHeight: 1.6,
              }}>
                {msg.text}
              </div>
            </div>
          ))}

          {loading && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, color: COLORS.muted, fontSize: 12 }}>
              <div style={{
                width: 10, height: 10, borderRadius: "50%",
                border: `2px solid ${COLORS.muted}`, borderTopColor: "transparent",
                animation: "spin 1s linear infinite",
              }} />
              Thinking…
            </div>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
            <input
              value={question}
              onChange={e => setQuestion(e.target.value)}
              onKeyDown={e => e.key === "Enter" && ask()}
              placeholder="e.g. Why is the competitive landscape score low?"
              style={{
                flex: 1, background: COLORS.card, border: `1px solid ${COLORS.border}`,
                color: COLORS.text, borderRadius: 6, padding: "8px 12px",
                fontSize: 13, outline: "none",
              }}
              disabled={loading}
            />
            <button
              onClick={ask}
              disabled={loading || !question.trim()}
              style={{
                background: COLORS.accent, color: "#0f1923", border: "none",
                borderRadius: 6, padding: "8px 16px", fontSize: 13,
                fontWeight: 700, cursor: "pointer",
                opacity: (loading || !question.trim()) ? 0.5 : 1,
              }}
            >
              Send
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function OpportunityCard({ opp, rank, therapeuticArea }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div style={{
      background: COLORS.card,
      border: `1px solid ${COLORS.border}`,
      borderRadius: 10,
      padding: "16px 20px",
      marginBottom: 12,
    }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 14 }}>
        <div style={{
          minWidth: 36, height: 36, borderRadius: "50%",
          background: COLORS.accent, display: "flex", alignItems: "center",
          justifyContent: "center", fontWeight: 800, fontSize: 16, color: "#0f1923",
        }}>
          {rank}
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 700, fontSize: 15, color: COLORS.text, marginBottom: 6 }}>
            {opp.name}
          </div>
          <ScoreBar score={opp.score} />
          <p style={{ marginTop: 10, fontSize: 13, color: COLORS.muted, lineHeight: 1.6 }}>
            {opp.rationale}
          </p>
          {opp.key_evidence?.length > 0 && (
            <button
              onClick={() => setExpanded(!expanded)}
              style={{
                marginTop: 8, background: "none", border: `1px solid ${COLORS.border}`,
                color: COLORS.accent, cursor: "pointer", borderRadius: 6, padding: "4px 10px",
                fontSize: 12,
              }}
            >
              {expanded ? "▲ Hide evidence" : "▼ Show evidence"}
            </button>
          )}
          {expanded && (
            <ul style={{ marginTop: 8, paddingLeft: 18, color: COLORS.muted, fontSize: 13 }}>
              {opp.key_evidence.map((e, i) => <li key={i} style={{ marginBottom: 4 }}>{e}</li>)}
            </ul>
          )}
          {opp.dimension_scores && <DimScores dims={opp.dimension_scores} />}
          <AskAI opp={opp} therapeuticArea={therapeuticArea} />
        </div>
      </div>
    </div>
  );
}

function AgentSummary({ findings }) {
  if (!findings?.length) return null;
  const seen = {};
  for (const f of findings) {
    const agent = f.agent || "unknown";
    if (!seen[agent]) seen[agent] = { records: 0, error: null };
    seen[agent].records += f.record_count || (f.records?.length) || 0;
    if (f.error) seen[agent].error = f.error;
  }
  const icons = {
    disease_agent: "🧬", treatment_agent: "💊",
    research_agent: "📄", clinical_trial_agent: "🔬",
    competition_agent: "🏁", trend_agent: "📈",
    europe_pmc_agent: "🇪🇺", enterprise_kb_agent: "🗂️",
  };
  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 20 }}>
      {Object.entries(seen).map(([agent, info]) => (
        <div key={agent} style={{
          background: COLORS.card, border: `1px solid ${info.error ? COLORS.danger : COLORS.border}`,
          borderRadius: 8, padding: "8px 14px", fontSize: 13,
        }}>
          <span style={{ marginRight: 6 }}>{icons[agent] || "📊"}</span>
          <span style={{ color: COLORS.text }}>{agent.replace("_agent", "").replace("_", " ")}</span>
          {info.error
            ? <span style={{ color: COLORS.danger, marginLeft: 8 }}>error</span>
            : <span style={{ color: COLORS.success, marginLeft: 8 }}>{info.records} records</span>
          }
        </div>
      ))}
    </div>
  );
}

export default function App() {
  const [therapeuticArea, setTherapeuticArea] = useState("Colorectal Cancer");
  const [status, setStatus] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function startScan() {
    setStatus("STARTING");
    setResult(null);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ therapeutic_area: therapeuticArea }),
      });
      const data = await res.json();
      const arn = data.execution_arn || data.executionArn || "";
      const id = arn.split(":").pop();
      setStatus("RUNNING");
      poll(id, arn);
    } catch (e) {
      setError("Failed to start scan: " + e.message);
      setStatus("FAILED");
    }
  }

  async function poll(id, arn) {
    try {
      const res = await fetch(`${API_BASE}/scan/${id}`);
      const data = await res.json();
      setStatus(data.status);

      if (data.status === "RUNNING" || data.status === "STARTING") {
        setTimeout(() => poll(id, arn), 4000);
      } else if (data.status === "SUCCEEDED") {
        setResult(data.output);
      } else if (data.status === "FAILED") {
        setError(data.error || "Pipeline failed");
      }
    } catch (e) {
      setError("Polling error: " + e.message);
      setStatus("FAILED");
    }
  }

  const opportunities = result?.ranked_opportunities?.opportunities
    || result?.opportunities
    || [];
  const report = result?.report;
  const agentFindings = result?.agent_findings || [];

  const statusColor = {
    STARTING: COLORS.muted, RUNNING: COLORS.warn,
    SUCCEEDED: COLORS.success, FAILED: COLORS.danger,
  }[status] || COLORS.muted;

  return (
    <div style={{ minHeight: "100vh", background: COLORS.bg, color: COLORS.text, padding: "0 16px" }}>
      <div style={{ maxWidth: 760, margin: "0 auto", paddingTop: 48 }}>

        {/* Header */}
        <div style={{ marginBottom: 40 }}>
          <h1 style={{ margin: 0, fontSize: 32, fontWeight: 800, color: COLORS.accent }}>
            TheraScout
          </h1>
          <p style={{ margin: "6px 0 0", color: COLORS.muted, fontSize: 15 }}>
            AI-Powered Therapeutic Opportunity Intelligence
          </p>
        </div>

        {/* Input */}
        <div style={{
          background: COLORS.card, border: `1px solid ${COLORS.border}`,
          borderRadius: 12, padding: 24, marginBottom: 24,
        }}>
          <label style={{ display: "block", marginBottom: 8, color: COLORS.muted, fontSize: 13 }}>
            Therapeutic Area
          </label>
          <div style={{ display: "flex", gap: 10 }}>
            <input
              value={therapeuticArea}
              onChange={(e) => setTherapeuticArea(e.target.value)}
              style={{
                flex: 1, background: "#0f1923", border: `1px solid ${COLORS.border}`,
                color: COLORS.text, borderRadius: 8, padding: "10px 14px", fontSize: 15,
                outline: "none",
              }}
              placeholder="e.g. Colorectal Cancer"
              onKeyDown={(e) => e.key === "Enter" && startScan()}
            />
            <button
              onClick={startScan}
              disabled={status === "RUNNING" || status === "STARTING"}
              style={{
                background: COLORS.accent, color: "#0f1923",
                border: "none", borderRadius: 8, padding: "10px 20px",
                fontWeight: 700, fontSize: 14, cursor: "pointer",
                opacity: (status === "RUNNING" || status === "STARTING") ? 0.6 : 1,
              }}
            >
              {status === "RUNNING" || status === "STARTING" ? "Scanning…" : "Discover Opportunities"}
            </button>
          </div>
        </div>

        {/* Status */}
        {status && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 20 }}>
            {(status === "RUNNING" || status === "STARTING") && (
              <div style={{
                width: 12, height: 12, borderRadius: "50%",
                border: `2px solid ${COLORS.warn}`, borderTopColor: "transparent",
                animation: "spin 1s linear infinite",
              }} />
            )}
            <span style={{ color: statusColor, fontWeight: 600 }}>
              {status === "RUNNING" ? "Pipeline running — fetching data from PubMed, ClinicalTrials, OpenFDA…" : status}
            </span>
          </div>
        )}

        {error && (
          <div style={{
            background: "#2a1515", border: `1px solid ${COLORS.danger}`,
            borderRadius: 8, padding: 16, marginBottom: 20, color: COLORS.danger,
          }}>
            {error}
          </div>
        )}

        {/* Results */}
        {result && (
          <>
            <AgentSummary findings={agentFindings} />

            {opportunities.length > 0 ? (
              <>
                <h2 style={{ margin: "0 0 16px", fontSize: 20, color: COLORS.accent2 }}>
                  Top {opportunities.length} Therapeutic Opportunities
                </h2>
                {opportunities.map((opp, i) => (
                  <OpportunityCard key={i} opp={opp} rank={i + 1} therapeuticArea={therapeuticArea} />
                ))}
              </>
            ) : (
              <div style={{
                background: COLORS.card, border: `1px solid ${COLORS.border}`,
                borderRadius: 10, padding: 20, color: COLORS.muted,
              }}>
                No ranked opportunities returned. Check the pipeline logs.
              </div>
            )}

            {report && (
              <div style={{
                marginTop: 20, background: COLORS.card,
                border: `1px solid ${COLORS.border}`, borderRadius: 10, padding: 16,
                fontSize: 13, color: COLORS.muted,
              }}>
                <strong style={{ color: COLORS.text }}>Report: </strong>
                {report.report_s3_key
                  ? <span style={{ color: COLORS.success }}>Saved to S3 ({report.report_s3_key})</span>
                  : <span>PDF generated ({report.opportunity_count} opportunities) — S3 upload blocked by org policy</span>
                }
              </div>
            )}

            <p style={{
              marginTop: 16, fontSize: 12, color: COLORS.muted,
              fontStyle: "italic", borderTop: `1px solid ${COLORS.border}`, paddingTop: 16,
            }}>
              This is a decision-support prioritization draft, not a guarantee of drug success,
              a clinical prediction, or a regulatory prediction. A human researcher must review
              and validate before any resource is committed.
            </p>
          </>
        )}
      </div>

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        * { box-sizing: border-box; }
        body { margin: 0; }
        input:focus { border-color: ${COLORS.accent} !important; }
      `}</style>
    </div>
  );
}
