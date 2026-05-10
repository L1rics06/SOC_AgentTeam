from __future__ import annotations

from ..models import AgentEnvelope, ReadinessGate
from ..utils import nested_get, severity_to_score, short_id
from .base import AgentContext, BaseAgent


class TriageAgent(BaseAgent):
    name = "triage"

    def run(self, context: AgentContext) -> AgentEnvelope:
        alert = context.case.raw_alert
        readiness = context.readiness
        severity = nested_get(alert, ["severity", "alert.severity", "rule.level", "rule.severity", "event.severity"])
        severity_score = severity_to_score(severity)
        if severity_score >= 85:
            priority = "P1"
        elif severity_score >= 65:
            priority = "P2"
        elif severity_score >= 40:
            priority = "P3"
        else:
            priority = "P4"

        if readiness and readiness.gate in {ReadinessGate.block, ReadinessGate.triage_only}:
            priority = "P3" if priority in {"P1", "P2"} else priority

        message = nested_get(alert, ["rule.description", "message", "event.action", "alert.title"], "Unclassified alert")
        findings = [
            f"Initial priority is {priority}.",
            f"Observed signal: {message}.",
        ]
        if readiness and readiness.gaps:
            findings.append("Readiness gaps constrain the confidence of this triage.")

        return AgentEnvelope(
            agent=self.name,
            case_id=context.case.case_id,
            task_id=short_id("TASK", f"{context.case.case_id}:{self.name}"),
            readiness_gate=readiness.gate if readiness else ReadinessGate.block,
            summary=f"{priority} triage based on severity score {severity_score}.",
            key_findings=findings,
            evidence_refs=context.case.evidence_refs,
            confidence=min((readiness.confidence_cap if readiness else 0.5), 0.85),
            recommended_actions=[],
            policy_flags=[],
            needs_human_approval=False,
            metadata={"priority": priority, "severity_score": severity_score},
        )

