from __future__ import annotations

from threading import RLock
from typing import Dict, List, Optional

from .config import Settings
from .models import (
    AgentEnvelope,
    ApprovalDecision,
    ApprovalStatus,
    CaseState,
    CaseStatus,
    EvidenceRef,
    IntakePayload,
    RecommendedAction,
)
from .opensearch_adapter import OpenSearchAdapter
from .utils import nested_get, redact_sensitive, short_id, utc_now


class CaseRepository:
    def __init__(self, adapter: OpenSearchAdapter, settings: Settings):
        self.adapter = adapter
        self.settings = settings
        self._lock = RLock()
        self._cases: Dict[str, CaseState] = {}

    def create_case(self, payload: IntakePayload) -> CaseState:
        with self._lock:
            raw_alert = redact_sensitive(payload.raw_alert)
            title = nested_get(
                raw_alert,
                [
                    "rule.description",
                    "alert.title",
                    "event.action",
                    "message",
                    "monitor.name",
                    "trigger.name",
                ],
                "SOC alert",
            )
            seed = f"{payload.source}:{payload.index}:{payload.doc_id}:{raw_alert}"
            case_id = short_id("CASE", seed)
            evidence = EvidenceRef(
                source=payload.source,
                index=payload.index,
                doc_id=payload.doc_id,
                timestamp=str(nested_get(raw_alert, ["@timestamp", "timestamp", "event.created"], "")) or None,
                reason="Original alert or finding used to open the case.",
            )
            case = CaseState(
                case_id=case_id,
                title=str(title)[:180],
                source_type=payload.source_type,
                source=payload.source,
                raw_alert=raw_alert,
                labels=payload.labels,
                evidence_refs=[evidence],
            )
            self._cases[case_id] = case
            self.adapter.write_case_state(case_id, case)
            self.adapter.write_audit_event(case_id, {"type": "case_created", "source": payload.source})
            return case

    def list_cases(self) -> List[CaseState]:
        with self._lock:
            return sorted(self._cases.values(), key=lambda case: case.created_at, reverse=True)

    def get_case(self, case_id: str) -> Optional[CaseState]:
        with self._lock:
            return self._cases.get(case_id)

    def save_case(self, case: CaseState) -> CaseState:
        with self._lock:
            case.updated_at = utc_now()
            self._cases[case.case_id] = case
            self.adapter.write_case_state(case.case_id, case)
            return case

    def append_agent_output(self, case: CaseState, output: AgentEnvelope) -> None:
        case.agent_outputs.append(output)
        self.adapter.write_agent_trace(case.case_id, output)

    def register_actions(self, case: CaseState, actions: List[RecommendedAction]) -> None:
        known = {action.action_id for action in case.approvals}
        for action in actions:
            if not action.requires_approval:
                continue
            if not action.action_id:
                action.action_id = short_id("ACT", f"{case.case_id}:{action.action_type}:{action.title}")
            if action.action_id not in known:
                case.approvals.append(action)
                known.add(action.action_id)

    def update_approval(self, action_id: str, decision: ApprovalDecision) -> Optional[CaseState]:
        with self._lock:
            for case in self._cases.values():
                target = None
                for action in case.approvals:
                    if action.action_id == action_id:
                        target = action
                        break
                if target is None:
                    continue
                target.status = decision.decision
                for output in case.agent_outputs:
                    for action in output.recommended_actions:
                        if action.action_id == action_id:
                            action.status = decision.decision
                case.status = self._resolve_status_after_approval(case)
                case.updated_at = utc_now()
                self.adapter.write_audit_event(
                    case.case_id,
                    {
                        "type": "approval_decision",
                        "action_id": action_id,
                        "decision": decision.decision,
                        "approver": decision.approver,
                        "comment": decision.comment,
                    },
                )
                self.adapter.write_case_state(case.case_id, case)
                return case
        return None

    @staticmethod
    def _resolve_status_after_approval(case: CaseState) -> CaseStatus:
        pending = [action for action in case.approvals if action.status == ApprovalStatus.pending]
        if pending:
            return CaseStatus.awaiting_approval
        return CaseStatus.completed
