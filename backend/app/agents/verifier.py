from __future__ import annotations

from ..models import AgentEnvelope, ReadinessGate, RiskLevel
from ..utils import short_id
from .base import AgentContext, BaseAgent


class VerifierAgent(BaseAgent):
    name = "verifier"

    def run(self, context: AgentContext) -> AgentEnvelope:
        readiness = context.readiness
        gate = readiness.gate if readiness else ReadinessGate.block
        findings = []
        flags = []

        for output in context.outputs:
            if output.key_findings and not output.evidence_refs:
                flags.append(f"{output.agent}:missing_evidence_refs")
            for action in output.recommended_actions:
                if action.risk in {RiskLevel.medium, RiskLevel.high, RiskLevel.critical} and not action.requires_approval:
                    flags.append(f"{output.agent}:{action.action_type}:approval_not_required")
                if action.risk in {RiskLevel.high, RiskLevel.critical}:
                    findings.append(f"High risk action requires approval: {action.title}.")

        if readiness and readiness.gaps:
            findings.append("Readiness gaps are explicitly recorded and must be visible in the report.")
        if not flags:
            findings.append("Verifier found no evidence-chain or approval-policy violations.")

        return AgentEnvelope(
            agent=self.name,
            case_id=context.case.case_id,
            task_id=short_id("TASK", f"{context.case.case_id}:{self.name}"),
            readiness_gate=gate,
            summary="Verification completed.",
            key_findings=findings,
            evidence_refs=context.case.evidence_refs,
            confidence=min((readiness.confidence_cap if readiness else 0.5), 0.88),
            recommended_actions=[],
            policy_flags=flags,
            needs_human_approval=bool(flags),
            metadata={"verified_agents": [output.agent for output in context.outputs]},
        )

