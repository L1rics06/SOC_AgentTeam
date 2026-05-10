from __future__ import annotations

from ..models import AgentEnvelope, ReadinessGate
from ..utils import short_id
from .base import AgentContext, BaseAgent


class TeamLeaderAgent(BaseAgent):
    name = "team_leader"

    def run(self, context: AgentContext) -> AgentEnvelope:
        readiness = context.readiness
        gate = readiness.gate if readiness else ReadinessGate.block
        if gate == ReadinessGate.allow:
            tasks = ["triage", "intel", "timeline", "impact", "response", "verifier", "report"]
        elif gate == ReadinessGate.limited:
            tasks = ["triage", "intel", "timeline", "impact", "response_requires_approval", "verifier", "report"]
        elif gate == ReadinessGate.triage_only:
            tasks = ["triage", "verifier", "report"]
        else:
            tasks = ["request_more_data", "report"]

        return AgentEnvelope(
            agent=self.name,
            case_id=context.case.case_id,
            task_id=short_id("TASK", f"{context.case.case_id}:{self.name}"),
            readiness_gate=gate,
            summary=f"Dispatch plan selected for gate {gate.value}.",
            key_findings=[f"Planned task: {task}" for task in tasks],
            evidence_refs=context.case.evidence_refs,
            confidence=(readiness.confidence_cap if readiness else 0.3),
            recommended_actions=[],
            policy_flags=["route_high_risk_actions_to_approval"],
            needs_human_approval=False,
            metadata={"tasks": tasks},
        )

