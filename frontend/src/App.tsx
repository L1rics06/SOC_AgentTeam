import { Check, ClipboardList, Play, RefreshCw, ShieldAlert, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { createDemoCase, decideApproval, listCases, runCase } from "./api";
import type { AgentEnvelope, CaseState, RecommendedAction } from "./types";

function statusLabel(status: string) {
  return status.replace("_", " ");
}

function formatConfidence(value: number) {
  return `${Math.round(value * 100)}%`;
}

export default function App() {
  const [cases, setCases] = useState<CaseState[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = useMemo(
    () => cases.find((item) => item.case_id === selectedId) || cases[0],
    [cases, selectedId]
  );

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const next = await listCases();
      setCases(next);
      if (!selectedId && next[0]) setSelectedId(next[0].case_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  async function createCase() {
    setLoading(true);
    setError(null);
    try {
      const created = await createDemoCase();
      setCases((current) => [created, ...current.filter((item) => item.case_id !== created.case_id)]);
      setSelectedId(created.case_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    } finally {
      setLoading(false);
    }
  }

  async function runSelected() {
    if (!selected) return;
    setLoading(true);
    setError(null);
    try {
      const updated = await runCase(selected.case_id);
      setCases((current) => current.map((item) => (item.case_id === updated.case_id ? updated : item)));
      setSelectedId(updated.case_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Run failed");
    } finally {
      setLoading(false);
    }
  }

  async function decide(action: RecommendedAction, decision: "approved" | "rejected") {
    if (!action.action_id) return;
    setLoading(true);
    setError(null);
    try {
      const updated = await decideApproval(action.action_id, decision);
      setCases((current) => current.map((item) => (item.case_id === updated.case_id ? updated : item)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Approval failed");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <ShieldAlert size={24} />
          <div>
            <strong>AI+SOC</strong>
            <span>Agent Team</span>
          </div>
        </div>
        <div className="toolbar">
          <button title="Refresh cases" onClick={refresh} disabled={loading}>
            <RefreshCw size={18} />
          </button>
          <button title="Create demo case" onClick={createCase} disabled={loading}>
            <ClipboardList size={18} />
          </button>
          <button title="Run selected case" onClick={runSelected} disabled={!selected || loading}>
            <Play size={18} />
          </button>
        </div>
        <div className="case-list">
          {cases.map((item) => (
            <button
              key={item.case_id}
              className={`case-row ${item.case_id === selected?.case_id ? "active" : ""}`}
              onClick={() => setSelectedId(item.case_id)}
            >
              <span>{item.title}</span>
              <small>{item.case_id}</small>
              <b className={`pill ${item.status}`}>{statusLabel(item.status)}</b>
            </button>
          ))}
        </div>
      </aside>

      <section className="workspace">
        {error && <div className="error">{error}</div>}
        {!selected ? (
          <div className="empty-state">No cases</div>
        ) : (
          <>
            <header className="case-header">
              <div>
                <p>{selected.case_id}</p>
                <h1>{selected.title}</h1>
              </div>
              <div className="score-strip">
                <Metric label="Status" value={statusLabel(selected.status)} />
                <Metric label="Readiness" value={selected.readiness ? `${selected.readiness.readiness_score}` : "--"} />
                <Metric label="Gate" value={selected.readiness?.gate || "--"} />
                <Metric label="Approvals" value={`${selected.approvals.filter((item) => item.status === "pending").length}`} />
              </div>
            </header>

            <section className="columns">
              <div className="main-column">
                <SectionTitle title="Agent Trace" />
                <div className="agent-stack">
                  {selected.agent_outputs.map((output) => (
                    <AgentRow key={output.task_id} output={output} />
                  ))}
                </div>
                <SectionTitle title="Team Mailbox" />
                <div className="mailbox-list">
                  {(selected.team_messages || []).length === 0 ? (
                    <div className="empty-panel">No team messages yet</div>
                  ) : (
                    selected.team_messages.map((message) => (
                      <article className="message-row" key={message.message_id}>
                        <div>
                          <b>{message.from_agent}</b>
                          <span>{message.to_agent}</span>
                          <small>{message.message_type}</small>
                        </div>
                        <p>{message.content}</p>
                      </article>
                    ))
                  )}
                </div>
                <SectionTitle title="Final Report" />
                <pre className="report">{selected.final_report || "Report pending"}</pre>
              </div>
              <div className="side-column">
                <SectionTitle title="Evidence" />
                <div className="evidence-list">
                  {selected.evidence_refs.map((evidence, index) => (
                    <div className="evidence-row" key={`${evidence.source}-${evidence.doc_id}-${index}`}>
                      <strong>{evidence.source}</strong>
                      <span>{evidence.index || "no-index"}</span>
                      <small>{evidence.doc_id || "no-doc-id"}</small>
                      <p>{evidence.reason}</p>
                    </div>
                  ))}
                </div>
                <SectionTitle title="Approval Queue" />
                <div className="approval-list">
                  {selected.approvals.map((action) => (
                    <div className="approval-row" key={action.action_id || action.title}>
                      <div>
                        <b>{action.title}</b>
                        <span className={`risk ${action.risk}`}>{action.risk}</span>
                      </div>
                      <p>{action.description}</p>
                      <small>{action.status}</small>
                      {action.status === "pending" && action.requires_approval && (
                        <div className="approval-actions">
                          <button title="Approve" onClick={() => decide(action, "approved")} disabled={loading}>
                            <Check size={16} />
                          </button>
                          <button title="Reject" onClick={() => decide(action, "rejected")} disabled={loading}>
                            <X size={16} />
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </section>
          </>
        )}
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}

function SectionTitle({ title }: { title: string }) {
  return <h2 className="section-title">{title}</h2>;
}

function AgentRow({ output }: { output: AgentEnvelope }) {
  return (
    <article className="agent-row">
      <div className="agent-heading">
        <b>{output.agent}</b>
        <span>{formatConfidence(output.confidence)}</span>
      </div>
      <p>{output.summary}</p>
      <ul>
        {output.key_findings.slice(0, 4).map((finding) => (
          <li key={finding}>{finding}</li>
        ))}
      </ul>
      {output.policy_flags.length > 0 && (
        <div className="flags">
          {output.policy_flags.map((flag) => (
            <span key={flag}>{flag}</span>
          ))}
        </div>
      )}
    </article>
  );
}
