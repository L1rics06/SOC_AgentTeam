import {
  Activity,
  AlertTriangle,
  Check,
  CheckCircle2,
  ClipboardList,
  Clock3,
  Play,
  Radio,
  RefreshCw,
  ShieldAlert,
  X
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { createDemoCase, decideApproval, getCase, listCases, runCase } from "./api";
import type { AgentEnvelope, CaseState, CaseStatus, RecommendedAction, TeamMessage } from "./types";

const EXPECTED_AGENT_ORDER = [
  "data_readiness",
  "team_leader",
  "triage",
  "intel",
  "timeline",
  "impact",
  "response",
  "verifier",
  "report"
];

const FAST_POLL_STATUSES = new Set<CaseStatus>(["running"]);
const SLOW_POLL_STATUSES = new Set<CaseStatus>(["awaiting_approval"]);

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    open: "Open",
    running: "Running",
    awaiting_approval: "Needs approval",
    completed: "Completed",
    blocked: "Blocked",
    failed: "Failed"
  };
  return labels[status] || status.replace(/_/g, " ");
}

function formatConfidence(value: number) {
  return `${Math.round(value * 100)}%`;
}

function formatTime(value?: string | Date | null) {
  if (!value) return "--";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(date);
}

function sortCases(items: CaseState[]) {
  return [...items].sort((left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime());
}

function unique(values: string[]) {
  const seen = new Set<string>();
  return values.filter((value) => {
    const normalized = value.trim();
    if (!normalized || seen.has(normalized)) return false;
    seen.add(normalized);
    return true;
  });
}

function isHighRisk(action: RecommendedAction) {
  return action.risk === "high" || action.risk === "critical";
}

function latestOutput(caseState: CaseState) {
  return caseState.agent_outputs[caseState.agent_outputs.length - 1];
}

function completedAgentNames(caseState: CaseState) {
  return unique(caseState.agent_outputs.map((output) => output.agent));
}

function completedAgentCount(caseState: CaseState) {
  return Math.min(completedAgentNames(caseState).length, EXPECTED_AGENT_ORDER.length);
}

function firstReportLine(report?: string) {
  if (!report) return "";
  for (const line of report.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const cleaned = trimmed.replace(/^[-*]\s*/, "").trim();
    if (!cleaned || /^soc agent team report$/i.test(cleaned) || /^case\s+/i.test(cleaned)) continue;
    return cleaned;
  }
  return "";
}

function importantSignalsFromReport(report?: string) {
  if (!report) return [];
  const lines = report.split(/\r?\n/);
  const signals: string[] = [];
  let inSection = false;
  for (const line of lines) {
    const trimmed = line.trim();
    if (/^#{1,3}\s+important signals\b/i.test(trimmed)) {
      inSection = true;
      continue;
    }
    if (inSection && /^#{1,3}\s+/.test(trimmed)) break;
    if (!inSection) continue;
    const cleaned = trimmed.replace(/^[-*]\s*/, "").trim();
    if (cleaned) signals.push(cleaned);
  }
  return signals;
}

function nextAgent(caseState: CaseState) {
  const completed = new Set(completedAgentNames(caseState));
  return EXPECTED_AGENT_ORDER.find((agent) => !completed.has(agent));
}

function buildOutcome(caseState: CaseState) {
  const pending = caseState.approvals.filter((action) => action.status === "pending");
  const severePending = pending.filter(isHighRisk);
  const latest = latestOutput(caseState);
  const reportLead = firstReportLine(caseState.final_report);
  const next = nextAgent(caseState);
  const finishedAgents = completedAgentCount(caseState);
  const progress =
    caseState.status === "completed" || caseState.status === "awaiting_approval"
      ? 100
      : Math.min(96, Math.round((finishedAgents / EXPECTED_AGENT_ORDER.length) * 100));

  let title = "Ready for analysis";
  let nextAction = "Run the Agent Team when you are ready.";
  let tone: CaseStatus = caseState.status;

  if (caseState.status === "running") {
    title = `Analyzing: ${finishedAgents}/${EXPECTED_AGENT_ORDER.length} agents finished`;
    nextAction = next ? `Waiting for ${next} output.` : "Preparing final report.";
  } else if (caseState.status === "awaiting_approval") {
    title = "Human approval required";
    nextAction = pending.length ? `Review ${pending.length} pending action${pending.length > 1 ? "s" : ""}.` : "Approval queue is current.";
  } else if (caseState.status === "completed") {
    title = "Analysis completed";
    nextAction = "No pending approval remains.";
  } else if (caseState.status === "blocked") {
    title = "Analysis blocked";
    nextAction = "Add missing evidence before continuing.";
  } else if (caseState.status === "failed") {
    title = "Analysis failed";
    nextAction = "Check backend configuration and audit events.";
  } else {
    tone = "open";
  }

  const verifier = [...caseState.agent_outputs].reverse().find((output) => output.agent === "verifier");
  const response = [...caseState.agent_outputs].reverse().find((output) => output.agent === "response");
  const impact = [...caseState.agent_outputs].reverse().find((output) => output.agent === "impact");
  const triage = [...caseState.agent_outputs].reverse().find((output) => output.agent === "triage");
  const important = unique([
    ...severePending.map((action) => `${action.risk.toUpperCase()} approval: ${action.title}`),
    ...(verifier?.policy_flags || []).map((flag) => `Policy flag: ${flag}`),
    ...(response?.key_findings || []),
    ...(impact?.key_findings || []),
    ...(triage?.key_findings || []),
    ...(latest?.key_findings || []),
    ...importantSignalsFromReport(caseState.final_report),
    ...(caseState.readiness?.gaps || []).map((gap) => `Data gap: ${gap}`)
  ]).slice(0, 4);

  return {
    title,
    tone,
    progress,
    progressLabel: `${progress}%`,
    summary: reportLead || latest?.summary || "No agent conclusion yet. Live updates will appear as the team produces them.",
    nextAction,
    importantFindings: important.length ? important : ["Important findings will appear here as agents finish their work."]
  };
}

export default function App() {
  const [cases, setCases] = useState<CaseState[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [startingRun, setStartingRun] = useState(false);
  const [approving, setApproving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastSyncedAt, setLastSyncedAt] = useState<Date | null>(null);

  const selected = useMemo(
    () => cases.find((item) => item.case_id === selectedId) || cases[0],
    [cases, selectedId]
  );
  const actionBusy = creating || startingRun || approving;

  function upsertCase(updated: CaseState) {
    setCases((current) => sortCases([updated, ...current.filter((item) => item.case_id !== updated.case_id)]));
  }

  async function syncCase(caseId: string, showErrors = false) {
    try {
      const updated = await getCase(caseId);
      upsertCase(updated);
      setLastSyncedAt(new Date());
      if (showErrors) setError(null);
    } catch (err) {
      if (showErrors) setError(err instanceof Error ? err.message : "Case sync failed");
    }
  }

  async function refresh(options: { silent?: boolean } = {}) {
    const silent = options.silent ?? false;
    if (!silent) {
      setRefreshing(true);
      setError(null);
    }
    try {
      const next = sortCases(await listCases());
      setCases(next);
      setSelectedId((current) => {
        if (current && next.some((item) => item.case_id === current)) return current;
        return next[0]?.case_id || null;
      });
      setLastSyncedAt(new Date());
    } catch (err) {
      if (!silent) setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      if (!silent) setRefreshing(false);
    }
  }

  async function createCase() {
    setCreating(true);
    setError(null);
    try {
      const created = await createDemoCase();
      upsertCase(created);
      setSelectedId(created.case_id);
      setLastSyncedAt(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Create failed");
    } finally {
      setCreating(false);
    }
  }

  async function runSelected() {
    if (!selected) return;
    setStartingRun(true);
    setError(null);
    try {
      const updated = await runCase(selected.case_id);
      upsertCase(updated);
      setSelectedId(updated.case_id);
      setLastSyncedAt(new Date());
      void syncCase(updated.case_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Run failed");
    } finally {
      setStartingRun(false);
    }
  }

  async function decide(action: RecommendedAction, decision: "approved" | "rejected") {
    if (!action.action_id) return;
    setApproving(true);
    setError(null);
    try {
      const updated = await decideApproval(action.action_id, decision);
      upsertCase(updated);
      setLastSyncedAt(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Approval failed");
    } finally {
      setApproving(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    const interval = window.setInterval(() => {
      void refresh({ silent: true });
    }, 10000);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!selected) return;
    const caseId = selected.case_id;
    const intervalMs = FAST_POLL_STATUSES.has(selected.status) ? 1500 : SLOW_POLL_STATUSES.has(selected.status) ? 5000 : 0;
    if (!intervalMs) return;

    let cancelled = false;
    async function pollSelected() {
      if (cancelled) return;
      await syncCase(caseId);
    }

    void pollSelected();
    const interval = window.setInterval(pollSelected, intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [selected?.case_id, selected?.status]);

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
          <button title="Refresh cases" onClick={() => void refresh()} disabled={refreshing || actionBusy}>
            <RefreshCw className={refreshing ? "spin" : ""} size={18} />
          </button>
          <button title="Create demo case" onClick={createCase} disabled={creating || startingRun}>
            <ClipboardList size={18} />
          </button>
          <button title="Run selected case" onClick={runSelected} disabled={!selected || selected.status === "running" || actionBusy}>
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
              <div className="case-row-footer">
                <b className={`pill ${item.status}`}>{statusLabel(item.status)}</b>
                <small>{completedAgentCount(item)}/{EXPECTED_AGENT_ORDER.length} agents</small>
              </div>
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
              <div className="header-status">
                <div className={`live-chip ${selected.status === "running" ? "active" : ""}`}>
                  <Radio size={16} />
                  <span>{selected.status === "running" ? "Live analysis" : "Auto-sync"}</span>
                  <small>Synced {formatTime(lastSyncedAt)}</small>
                </div>
                <div className="score-strip">
                  <Metric label="Status" value={statusLabel(selected.status)} />
                  <Metric label="Readiness" value={selected.readiness ? `${selected.readiness.readiness_score}` : "--"} />
                  <Metric label="Gate" value={selected.readiness?.gate || "--"} />
                  <Metric label="Pending" value={`${selected.approvals.filter((item) => item.status === "pending").length}`} />
                </div>
              </div>
            </header>

            <OutcomePanel caseState={selected} lastSyncedAt={lastSyncedAt} />

            <section className="columns">
              <div className="main-column">
                <SectionTitle title="Agent Progress" />
                <div className="agent-stack">
                  {selected.agent_outputs.length === 0 ? (
                    <div className="empty-panel">No agent output yet</div>
                  ) : (
                    selected.agent_outputs.map((output) => <AgentRow key={output.task_id} output={output} />)
                  )}
                </div>
                <SectionTitle title="Team Mailbox" />
                <div className="mailbox-list">
                  {(selected.team_messages || []).length === 0 ? (
                    <div className="empty-panel">No team messages yet</div>
                  ) : (
                    [...selected.team_messages].reverse().map((message) => <MessageRow message={message} key={message.message_id} />)
                  )}
                </div>
                <SectionTitle title="Final Report" />
                <pre className="report">{selected.final_report || "Final report pending. Watch the live outcome panel above."}</pre>
              </div>
              <div className="side-column">
                <SectionTitle title="Evidence" />
                <div className="evidence-list">
                  {selected.evidence_refs.length === 0 ? (
                    <div className="empty-panel">No evidence references yet</div>
                  ) : (
                    selected.evidence_refs.map((evidence, index) => (
                      <div className="evidence-row" key={`${evidence.source}-${evidence.doc_id}-${index}`}>
                        <strong>{evidence.source}</strong>
                        <span>{evidence.index || "no-index"}</span>
                        <small>{evidence.doc_id || "no-doc-id"}</small>
                        <p>{evidence.reason}</p>
                      </div>
                    ))
                  )}
                </div>
                <SectionTitle title="Approval Queue" />
                <div className="approval-list">
                  {selected.approvals.length === 0 ? (
                    <div className="empty-panel">No approval needed yet</div>
                  ) : (
                    selected.approvals.map((action) => (
                      <div className={`approval-row ${action.status}`} key={action.action_id || action.title}>
                        <div>
                          <b>{action.title}</b>
                          <span className={`risk ${action.risk}`}>{action.risk}</span>
                        </div>
                        <p>{action.description}</p>
                        <small>{action.status}</small>
                        {action.status === "pending" && action.requires_approval && (
                          <div className="approval-actions">
                            <button title="Approve" onClick={() => void decide(action, "approved")} disabled={approving}>
                              <Check size={16} />
                            </button>
                            <button title="Reject" onClick={() => void decide(action, "rejected")} disabled={approving}>
                              <X size={16} />
                            </button>
                          </div>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>
            </section>
          </>
        )}
      </section>
    </main>
  );
}

function OutcomePanel({ caseState, lastSyncedAt }: { caseState: CaseState; lastSyncedAt: Date | null }) {
  const outcome = buildOutcome(caseState);
  return (
    <section className={`outcome-panel ${outcome.tone}`}>
      <div className="outcome-copy">
        <span className="eyebrow">Analysis Outcome</span>
        <h2>{outcome.title}</h2>
        <p>{outcome.summary}</p>
      </div>
      <div className="outcome-progress">
        <div>
          <Activity size={18} />
          <b>{outcome.progressLabel}</b>
        </div>
        <div className="progress-track" aria-label="Analysis progress">
          <span style={{ width: outcome.progressLabel }} />
        </div>
        <small>Last update {formatTime(lastSyncedAt)}</small>
      </div>
      <div className="next-action">
        <Clock3 size={18} />
        <div>
          <span>Next action</span>
          <b>{outcome.nextAction}</b>
        </div>
      </div>
      <div className="important-list">
        <span className="eyebrow">Important Signals</span>
        <ul>
          {outcome.importantFindings.map((finding, index) => (
            <li key={`${finding}-${index}`}>
              {caseState.status === "completed" ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
              <span>{finding}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
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

function MessageRow({ message }: { message: TeamMessage }) {
  return (
    <article className={`message-row ${message.message_type}`}>
      <div>
        <b>{message.from_agent}</b>
        <span>{message.to_agent}</span>
        <small>{message.message_type}</small>
      </div>
      <p>{message.content}</p>
      <time>{formatTime(message.created_at)}</time>
    </article>
  );
}

function AgentRow({ output }: { output: AgentEnvelope }) {
  const highRiskActions = output.recommended_actions.filter(isHighRisk);
  const isImportant =
    output.policy_flags.length > 0 ||
    highRiskActions.length > 0 ||
    output.agent === "verifier" ||
    output.agent === "response";

  return (
    <article className={`agent-row ${isImportant ? "important" : ""}`}>
      <div className="agent-heading">
        <div>
          <b>{output.agent}</b>
          {isImportant && <span className="importance">Important</span>}
        </div>
        <span>{formatConfidence(output.confidence)}</span>
      </div>
      <p>{output.summary}</p>
      <ul>
        {output.key_findings.slice(0, 4).map((finding, index) => (
          <li key={`${finding}-${index}`}>{finding}</li>
        ))}
      </ul>
      {output.policy_flags.length > 0 && (
        <div className="flags">
          {output.policy_flags.map((flag) => (
            <span key={flag}>{flag}</span>
          ))}
        </div>
      )}
      {highRiskActions.length > 0 && (
        <div className="agent-actions-list">
          {highRiskActions.map((action) => (
            <span key={action.action_id || action.title}>{action.title}</span>
          ))}
        </div>
      )}
    </article>
  );
}
