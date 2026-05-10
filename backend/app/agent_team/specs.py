from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class AgentSpec:
    name: str
    role: str
    goal: str
    required_skills: List[str]
    allowed_tools: List[str]
    output_notes: str = ""
    peer_agents: List[str] = field(default_factory=list)


COMMON_TOOLS = [
    "list_skills",
    "load_skill",
    "read_mailbox",
    "send_message",
]

OPENSEARCH_TOOLS = [
    "opensearch_search_events",
    "opensearch_get_document",
    "opensearch_aggregate_timeline",
    "opensearch_search_findings",
    "opensearch_vector_search",
]


AGENT_SPECS: Dict[str, AgentSpec] = {
    "data_readiness": AgentSpec(
        name="data_readiness",
        role="SOC Data Readiness Agent",
        goal="Decide whether the incoming alert and evidence are sufficient for reliable investigation.",
        required_skills=["evidence_contract", "opensearch_investigation"],
        allowed_tools=COMMON_TOOLS + ["opensearch_get_document"],
        output_notes="metadata must include readiness_report.",
    ),
    "team_leader": AgentSpec(
        name="team_leader",
        role="SOC Team Leader Agent",
        goal="Break the case into expert tasks, coordinate peer agents, and keep the investigation evidence-bound.",
        required_skills=["team_leadership", "evidence_contract"],
        allowed_tools=COMMON_TOOLS,
        output_notes="metadata should include tasks as a list of agent names to run next.",
    ),
    "triage": AgentSpec(
        name="triage",
        role="SOC Triage Agent",
        goal="Classify severity, priority, likely alert class, and whether investigation should continue.",
        required_skills=["triage"],
        allowed_tools=COMMON_TOOLS + OPENSEARCH_TOOLS,
    ),
    "intel": AgentSpec(
        name="intel",
        role="Threat Intelligence Agent",
        goal="Enrich indicators, historical case context, playbook hints, and knowledge-base matches.",
        required_skills=["threat_intel", "opensearch_investigation"],
        allowed_tools=COMMON_TOOLS + OPENSEARCH_TOOLS,
    ),
    "timeline": AgentSpec(
        name="timeline",
        role="Timeline Reconstruction Agent",
        goal="Reconstruct event order and ask for missing logs when the chain is incomplete.",
        required_skills=["timeline"],
        allowed_tools=COMMON_TOOLS + OPENSEARCH_TOOLS,
    ),
    "impact": AgentSpec(
        name="impact",
        role="Impact Assessment Agent",
        goal="Estimate affected users, hosts, business systems, and blast radius.",
        required_skills=["impact"],
        allowed_tools=COMMON_TOOLS + OPENSEARCH_TOOLS,
    ),
    "response": AgentSpec(
        name="response",
        role="Response Planning Agent",
        goal="Recommend containment and evidence-preservation actions without executing them.",
        required_skills=["response_policy"],
        allowed_tools=COMMON_TOOLS + OPENSEARCH_TOOLS,
    ),
    "verifier": AgentSpec(
        name="verifier",
        role="Independent Verification Agent",
        goal="Check whether the team conclusions are supported by evidence and policy.",
        required_skills=["verification", "evidence_contract"],
        allowed_tools=COMMON_TOOLS + OPENSEARCH_TOOLS,
    ),
    "report": AgentSpec(
        name="report",
        role="SOC Report Agent",
        goal="Synthesize the verified team result into a concise analyst-facing case report.",
        required_skills=["reporting"],
        allowed_tools=COMMON_TOOLS,
        output_notes="metadata must include final_report as markdown.",
    ),
}

