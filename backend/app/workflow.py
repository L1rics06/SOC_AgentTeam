from __future__ import annotations

from .agent_team.runner import AgentTeamRunner
from .llm import build_llm_client
from .models import CaseState, CaseStatus
from .opensearch_adapter import OpenSearchAdapter
from .store import CaseRepository


class AgentWorkflow:
    def __init__(self, repository: CaseRepository, adapter: OpenSearchAdapter, llm=None):
        self.repository = repository
        self.adapter = adapter
        self.llm = llm or build_llm_client(repository.settings)
        self.agent_team = AgentTeamRunner(self.llm, adapter, repository)

    def run(self, case: CaseState) -> CaseState:
        case.status = CaseStatus.running
        self.repository.save_case(case)

        if not self.llm.enabled:
            case.status = CaseStatus.failed
            self.adapter.write_audit_event(
                case.case_id,
                {
                    "type": "agent_team_unavailable",
                    "reason": "OpenAI LLM agent team is not configured.",
                    "llm_status": self.llm.status(),
                },
            )
            self.repository.save_case(case)
            raise RuntimeError("LLM agent team is required. Set OPENAI_ENABLED=true and OPENAI_API_KEY.")

        if self.agent_team.run(case):
            return self.repository.save_case(case)

        case.status = CaseStatus.failed
        self.adapter.write_audit_event(
            case.case_id,
            {
                "type": "agent_team_failed",
                "reason": "The LLM-driven agent team run did not produce a valid case result.",
            },
        )
        self.repository.save_case(case)
        raise RuntimeError("LLM agent team failed; inspect audit events and agent traces.")
