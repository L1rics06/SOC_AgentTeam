import type { ApprovalStatus, CaseState } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {})
    }
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || response.statusText);
  }
  return response.json() as Promise<T>;
}

export async function listCases(): Promise<CaseState[]> {
  const data = await request<{ cases: CaseState[] }>("/api/v1/cases");
  return data.cases;
}

export async function getCase(caseId: string): Promise<CaseState> {
  return request<CaseState>(`/api/v1/cases/${caseId}`);
}

export async function createDemoCase(): Promise<CaseState> {
  return request<CaseState>("/api/v1/cases/intake", {
    method: "POST",
    body: JSON.stringify({
      source_type: "opensearch_alert",
      source: "wazuh",
      index: "wazuh-alerts-4.x-2026.05.10",
      doc_id: `demo-${Date.now()}`,
      labels: ["demo", "high-risk-login"],
      raw_alert: {
        "@timestamp": new Date().toISOString(),
        severity: "high",
        rule: {
          level: 12,
          description: "Multiple failed logins followed by success from unusual source"
        },
        event: {
          action: "authentication_success_after_failures",
          category: "authentication"
        },
        host: { name: "win-finance-07" },
        user: { name: "zhang.wei" },
        source: { ip: "203.0.113.77" },
        message: "Suspicious login chain detected for privileged finance workstation"
      }
    })
  });
}

export async function runCase(caseId: string): Promise<CaseState> {
  return request<CaseState>(`/api/v1/cases/${caseId}/run`, { method: "POST" });
}

export async function decideApproval(
  actionId: string,
  decision: ApprovalStatus,
  approver = "soc-analyst"
): Promise<CaseState> {
  return request<CaseState>(`/api/v1/approvals/${actionId}/decision`, {
    method: "POST",
    body: JSON.stringify({ decision, approver })
  });
}
