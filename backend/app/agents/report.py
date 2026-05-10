from __future__ import annotations

from ..models import AgentEnvelope, ReadinessGate
from ..utils import short_id
from .base import AgentContext, BaseAgent


class ReportAgent(BaseAgent):
    name = "report"

    def run(self, context: AgentContext) -> AgentEnvelope:
        readiness = context.readiness
        gate = readiness.gate if readiness else ReadinessGate.block
        lines = [
            f"# Case {context.case.case_id}: {context.case.title}",
            "",
            "## Conclusion",
            f"Readiness gate: {gate.value}. The case was analyzed by the AI+SOC Agent Team PoC.",
            "",
            "## Evidence",
        ]
        for evidence in context.case.evidence_refs:
            lines.append(f"- {evidence.source} {evidence.index or ''} {evidence.doc_id or ''}: {evidence.reason}".strip())

        lines.extend(["", "## Agent Findings"])
        for output in context.outputs:
            lines.append(f"- {output.agent}: {output.summary}")

        if readiness and readiness.gaps:
            lines.extend(["", "## Limitations"])
            for gap in readiness.gaps:
                lines.append(f"- {gap}")

        pending_actions = []
        for output in context.outputs:
            pending_actions.extend([action for action in output.recommended_actions if action.requires_approval])
        lines.extend(["", "## Recommended Actions"])
        if pending_actions:
            for action in pending_actions:
                lines.append(f"- [{action.risk.value}] {action.title}: {action.description}")
        else:
            lines.append("- No high risk response action is ready for approval.")

        report = "\n".join(lines)
        return AgentEnvelope(
            agent=self.name,
            case_id=context.case.case_id,
            task_id=short_id("TASK", f"{context.case.case_id}:{self.name}"),
            readiness_gate=gate,
            summary="Final case report generated.",
            key_findings=["Report includes conclusion, evidence, limitations, and recommended actions."],
            evidence_refs=context.case.evidence_refs,
            confidence=min((readiness.confidence_cap if readiness else 0.5), 0.9),
            recommended_actions=[],
            policy_flags=[],
            needs_human_approval=bool(pending_actions),
            metadata={"final_report": report},
        )

