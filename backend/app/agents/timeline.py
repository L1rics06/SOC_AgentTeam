from __future__ import annotations

from ..models import AgentEnvelope, ReadinessGate
from ..utils import extract_entities, nested_get, short_id
from .base import AgentContext, BaseAgent


class TimelineAgent(BaseAgent):
    name = "timeline"

    def run(self, context: AgentContext) -> AgentEnvelope:
        readiness = context.readiness
        entities = extract_entities(context.case.raw_alert)
        timestamp = nested_get(context.case.raw_alert, ["@timestamp", "timestamp", "event.created"])
        timeline = context.adapter.aggregate_timeline(
            {
                "timestamp": timestamp,
                "event": nested_get(context.case.raw_alert, ["event.action", "rule.description", "message"]),
                "hosts": entities["hosts"],
                "users": entities["users"],
                "ips": entities["ips"],
            }
        )
        findings = [
            f"Built a timeline with {len(timeline)} bucket(s).",
            "Timeline remains evidence-bound to the original alert and related OpenSearch query.",
        ]
        return AgentEnvelope(
            agent=self.name,
            case_id=context.case.case_id,
            task_id=short_id("TASK", f"{context.case.case_id}:{self.name}"),
            readiness_gate=readiness.gate if readiness else ReadinessGate.block,
            summary="Timeline reconstruction completed.",
            key_findings=findings,
            evidence_refs=context.case.evidence_refs,
            confidence=min((readiness.confidence_cap if readiness else 0.5), 0.82),
            recommended_actions=[],
            policy_flags=[],
            needs_human_approval=False,
            metadata={"timeline": timeline},
        )

