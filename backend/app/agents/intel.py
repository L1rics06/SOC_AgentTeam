from __future__ import annotations

from ..models import AgentEnvelope, EvidenceRef, ReadinessGate
from ..utils import extract_iocs, short_id
from .base import AgentContext, BaseAgent


class IntelAgent(BaseAgent):
    name = "intel"

    def run(self, context: AgentContext) -> AgentEnvelope:
        readiness = context.readiness
        iocs = extract_iocs(context.case.raw_alert)
        query_text = " ".join(iocs["ips"] + iocs["domains"] + iocs["hashes"] + context.case.labels)
        contexts = context.adapter.vector_search_context(query_text or context.case.title, size=3)

        findings = []
        if any(iocs.values()):
            findings.append(f"Extracted IOCs: {iocs}.")
        else:
            findings.append("No explicit IOC was extracted from the alert payload.")
        if contexts:
            findings.append(f"Matched {len(contexts)} internal knowledge items.")
        else:
            findings.append("No internal threat-intel or playbook context matched.")

        evidence = list(context.case.evidence_refs)
        for idx, item in enumerate(contexts):
            source = item.get("_source", item)
            evidence.append(
                EvidenceRef(
                    source="soc-knowledge-v1",
                    doc_id=str(source.get("title", idx)),
                    reason="Internal knowledge item matched the extracted context.",
                    fields={"title": source.get("title"), "score": item.get("_score")},
                )
            )

        return AgentEnvelope(
            agent=self.name,
            case_id=context.case.case_id,
            task_id=short_id("TASK", f"{context.case.case_id}:{self.name}"),
            readiness_gate=readiness.gate if readiness else ReadinessGate.block,
            summary="Threat intelligence enrichment completed.",
            key_findings=findings,
            evidence_refs=evidence,
            confidence=min((readiness.confidence_cap if readiness else 0.5), 0.8),
            recommended_actions=[],
            policy_flags=[],
            needs_human_approval=False,
            metadata={"iocs": iocs, "context_count": len(contexts)},
        )

