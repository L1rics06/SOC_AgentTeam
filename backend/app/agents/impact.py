from __future__ import annotations

from ..models import AgentEnvelope, ReadinessGate
from ..utils import extract_entities, nested_get, severity_to_score, short_id
from .base import AgentContext, BaseAgent


class ImpactAgent(BaseAgent):
    name = "impact"

    def run(self, context: AgentContext) -> AgentEnvelope:
        readiness = context.readiness
        alert = context.case.raw_alert
        entities = extract_entities(alert)
        severity = nested_get(alert, ["severity", "alert.severity", "rule.level", "rule.severity", "event.severity"])
        severity_score = severity_to_score(severity)
        asset_count = len(entities["hosts"]) + len(entities["users"]) + len(entities["ips"])

        if severity_score >= 85 or asset_count >= 4:
            business_risk = "high"
        elif severity_score >= 55 or asset_count >= 2:
            business_risk = "medium"
        else:
            business_risk = "low"

        findings = [
            f"Estimated business risk is {business_risk}.",
            f"Observed entities: {entities}.",
        ]
        if asset_count == 0:
            findings.append("Impact analysis is constrained because no assets or identities were present.")

        return AgentEnvelope(
            agent=self.name,
            case_id=context.case.case_id,
            task_id=short_id("TASK", f"{context.case.case_id}:{self.name}"),
            readiness_gate=readiness.gate if readiness else ReadinessGate.block,
            summary=f"Impact assessment is {business_risk}.",
            key_findings=findings,
            evidence_refs=context.case.evidence_refs,
            confidence=min((readiness.confidence_cap if readiness else 0.5), 0.78),
            recommended_actions=[],
            policy_flags=[],
            needs_human_approval=False,
            metadata={"business_risk": business_risk, "entities": entities},
        )

