"""顶层工作流：负责运行 Agent Team 并处理成功/失败状态。"""

from __future__ import annotations

from .agent_team.runner import AgentTeamRunner
from .llm import build_llm_client
from .models import CaseState, CaseStatus
from .opensearch_adapter import OpenSearchAdapter
from .store import CaseRepository


class AgentWorkflow:
    """把仓储、OpenSearch 适配器和 LLM Agent Team 串成一次分析流程。"""

    def __init__(self, repository: CaseRepository, adapter: OpenSearchAdapter, llm=None):
        self.repository = repository
        self.adapter = adapter
        self.llm = llm or build_llm_client(repository.settings)
        self.agent_team = AgentTeamRunner(self.llm, adapter, repository)

    def run(self, case: CaseState, *, reset: bool = True) -> CaseState:
        """运行一次 Case 分析，并把状态变化持久化到仓储和审计日志。"""
        if reset:
            case = self.repository.start_case_run(case)
        else:
            case.status = CaseStatus.running
            self.repository.save_case(case)

        if not self.llm.enabled:
            # 当前实现强依赖 LLM；未配置时显式失败，避免给出伪分析结果。
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

        # Agent Team 返回 False 表示没有拿到可用输出，也要留下审计线索。
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
