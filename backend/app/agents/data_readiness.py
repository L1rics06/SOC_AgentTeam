from __future__ import annotations

from typing import Any, Dict, List

from ..models import AgentEnvelope, EvidenceRef, ReadinessGate, ReadinessReport
from ..utils import extract_entities, first_non_empty, model_to_dict, nested_get, severity_to_score, short_id
from .base import AgentContext, BaseAgent


class DataReadinessAgent(BaseAgent):
    name = "data_readiness"

    def run(self, context: AgentContext) -> AgentEnvelope:
        alert = context.case.raw_alert
        gaps: List[str] = []
        score = 20

        timestamp = first_non_empty(
            [
                nested_get(alert, ["@timestamp", "timestamp", "event.created", "event.start"]),
                context.case.evidence_refs[0].timestamp if context.case.evidence_refs else None,
            ]
        )
        severity = nested_get(alert, ["severity", "alert.severity", "rule.level", "rule.severity", "event.severity"])
        entities = extract_entities(alert)
        message = nested_get(alert, ["message", "rule.description", "event.action", "alert.title"])

        if timestamp:
            score += 15
        else:
            gaps.append("Missing timestamp field such as @timestamp or event.created.")

        if severity_to_score(severity) > 0:
            score += 15
        else:
            gaps.append("Missing severity or rule level.")

        if message:
            score += 10
        else:
            gaps.append("Missing event message, rule description, or action.")

        if entities["hosts"] or entities["users"] or entities["ips"]:
            score += 15
        else:
            gaps.append("Missing core entity context such as host, user, or IP.")

        if context.case.evidence_refs and (context.case.evidence_refs[0].doc_id or context.case.evidence_refs[0].index):
            score += 15
        else:
            gaps.append("Missing OpenSearch index or document ID for traceability.")

        if context.case.source_type in {"opensearch_alert", "opensearch_finding", "opensearch_webhook"}:
            score += 10

        score = max(0, min(100, score))
        if score >= 80:
            gate = ReadinessGate.allow
            allowed_actions = ["full_analysis", "recommend_response", "write_report"]
            confidence_cap = 0.9
        elif score >= 60:
            gate = ReadinessGate.limited
            allowed_actions = ["analysis", "recommend_response_requires_approval", "write_report"]
            confidence_cap = 0.75
        elif score >= 40:
            gate = ReadinessGate.triage_only
            allowed_actions = ["triage_only", "request_more_data"]
            confidence_cap = 0.55
        else:
            gate = ReadinessGate.block
            allowed_actions = ["request_more_data", "manual_review"]
            confidence_cap = 0.35

        normalized: Dict[str, Any] = {
            "timestamp": timestamp,
            "severity": severity,
            "severity_score": severity_to_score(severity),
            "message": message,
            "entities": entities,
        }
        report = ReadinessReport(
            readiness_score=score,
            gate=gate,
            gaps=gaps,
            allowed_actions=allowed_actions,
            confidence_cap=confidence_cap,
            normalized_fields=normalized,
        )
        evidence = context.case.evidence_refs or [
            EvidenceRef(source=context.case.source, reason="Case payload was used for readiness scoring.")
        ]
        return AgentEnvelope(
            agent=self.name,
            case_id=context.case.case_id,
            task_id=short_id("TASK", f"{context.case.case_id}:{self.name}"),
            readiness_gate=gate,
            summary=f"Readiness score is {score}; gate is {gate.value}.",
            key_findings=gaps or ["Required evidence, time, severity, and entity fields are present enough for analysis."],
            evidence_refs=evidence,
            confidence=confidence_cap,
            recommended_actions=[],
            policy_flags=[] if score >= 60 else ["insufficient_data"],
            needs_human_approval=score < 60,
            metadata={"readiness_report": model_to_dict(report)},
        )
