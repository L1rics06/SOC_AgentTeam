export type CaseStatus = "open" | "running" | "awaiting_approval" | "completed" | "blocked" | "failed";
export type ReadinessGate = "allow" | "limited" | "triage_only" | "block";
export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface EvidenceRef {
  source: string;
  doc_id?: string;
  index?: string;
  timestamp?: string;
  reason: string;
  fields?: Record<string, unknown>;
}

export interface RecommendedAction {
  action_id?: string;
  action_type: string;
  title: string;
  description: string;
  risk: "low" | "medium" | "high" | "critical";
  requires_approval: boolean;
  playbook?: string;
  status: ApprovalStatus;
}

export interface AgentEnvelope {
  agent: string;
  case_id: string;
  task_id: string;
  readiness_gate: ReadinessGate;
  summary: string;
  key_findings: string[];
  evidence_refs: EvidenceRef[];
  confidence: number;
  recommended_actions: RecommendedAction[];
  policy_flags: string[];
  needs_human_approval: boolean;
  metadata: Record<string, unknown>;
}

export interface ReadinessReport {
  readiness_score: number;
  gate: ReadinessGate;
  gaps: string[];
  allowed_actions: string[];
  confidence_cap: number;
  normalized_fields: Record<string, unknown>;
}

export interface CaseState {
  case_id: string;
  title: string;
  source_type: string;
  source: string;
  status: CaseStatus;
  raw_alert: Record<string, unknown>;
  labels: string[];
  evidence_refs: EvidenceRef[];
  readiness?: ReadinessReport;
  agent_outputs: AgentEnvelope[];
  approvals: RecommendedAction[];
  final_report?: string;
  created_at: string;
  updated_at: string;
}

