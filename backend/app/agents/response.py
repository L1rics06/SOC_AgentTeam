from __future__ import annotations

from ..models import AgentEnvelope, ReadinessGate, RecommendedAction, RiskLevel
from ..utils import extract_entities, nested_get, severity_to_score, short_id
from .base import AgentContext, BaseAgent


class ResponseAgent(BaseAgent):
    name = "response"

    def run(self, context: AgentContext) -> AgentEnvelope:
        readiness = context.readiness
        gate = readiness.gate if readiness else ReadinessGate.block
        alert = context.case.raw_alert
        severity = nested_get(alert, ["severity", "alert.severity", "rule.level", "rule.severity", "event.severity"])
        severity_score = severity_to_score(severity)
        entities = extract_entities(alert)
        actions = []
        flags = ["human_approval_required_for_response"]

        if gate in {ReadinessGate.block, ReadinessGate.triage_only}:
            actions.append(
                RecommendedAction(
                    action_id=short_id("ACT", f"{context.case.case_id}:collect_more_data"),
                    action_type="collect_more_data",
                    title="Collect missing investigation data",
                    description="Request additional logs, asset context, or original OpenSearch document references before containment.",
                    risk=RiskLevel.low,
                    requires_approval=False,
                )
            )
        else:
            actions.append(
                RecommendedAction(
                    action_id=short_id("ACT", f"{context.case.case_id}:preserve_evidence"),
                    action_type="preserve_evidence",
                    title="Preserve related evidence",
                    description="Snapshot matching OpenSearch documents and attach them to the case evidence ledger.",
                    risk=RiskLevel.low,
                    requires_approval=False,
                )
            )
            if severity_score >= 70 and entities["hosts"]:
                actions.append(
                    RecommendedAction(
                        action_id=short_id("ACT", f"{context.case.case_id}:isolate_host"),
                        action_type="isolate_host",
                        title="Isolate affected host",
                        description=f"Prepare host isolation for {entities['hosts'][0]}; execution is blocked until human approval.",
                        risk=RiskLevel.high,
                        requires_approval=True,
                        playbook="wazuh-active-response-isolate-host",
                    )
                )
            if severity_score >= 55 and entities["ips"]:
                actions.append(
                    RecommendedAction(
                        action_id=short_id("ACT", f"{context.case.case_id}:block_ip"),
                        action_type="block_ip",
                        title="Block suspicious source IP",
                        description=f"Prepare a network block for {entities['ips'][0]}; execution requires approval and change tracking.",
                        risk=RiskLevel.medium,
                        requires_approval=True,
                        playbook="network-block-ip",
                    )
                )

        needs_approval = any(action.requires_approval for action in actions)
        return AgentEnvelope(
            agent=self.name,
            case_id=context.case.case_id,
            task_id=short_id("TASK", f"{context.case.case_id}:{self.name}"),
            readiness_gate=gate,
            summary="Response recommendations prepared; no action has been executed.",
            key_findings=[
                "All containment actions are recommendations only in this PoC.",
                "High and medium risk actions are routed to the approval queue.",
            ],
            evidence_refs=context.case.evidence_refs,
            confidence=min((readiness.confidence_cap if readiness else 0.5), 0.8),
            recommended_actions=actions,
            policy_flags=flags,
            needs_human_approval=needs_approval,
            metadata={"action_count": len(actions)},
        )

