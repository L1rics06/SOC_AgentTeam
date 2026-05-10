from __future__ import annotations

from typing import List

from .agents.base import AgentContext, BaseAgent
from .agents.data_readiness import DataReadinessAgent
from .agents.impact import ImpactAgent
from .agents.intel import IntelAgent
from .agents.report import ReportAgent
from .agents.response import ResponseAgent
from .agents.team_leader import TeamLeaderAgent
from .agents.timeline import TimelineAgent
from .agents.triage import TriageAgent
from .agents.verifier import VerifierAgent
from .models import AgentEnvelope, CaseState, CaseStatus, ReadinessGate, ReadinessReport
from .opensearch_adapter import OpenSearchAdapter
from .store import CaseRepository

try:  # LangGraph is the intended production orchestration layer.
    from langgraph.graph import END, StateGraph  # type: ignore  # noqa: F401
except ImportError:  # pragma: no cover - optional at import time
    END = None  # type: ignore
    StateGraph = None  # type: ignore


class AgentWorkflow:
    def __init__(self, repository: CaseRepository, adapter: OpenSearchAdapter):
        self.repository = repository
        self.adapter = adapter
        self.data_readiness = DataReadinessAgent()
        self.team_leader = TeamLeaderAgent()
        self.triage = TriageAgent()
        self.intel = IntelAgent()
        self.timeline = TimelineAgent()
        self.impact = ImpactAgent()
        self.response = ResponseAgent()
        self.verifier = VerifierAgent()
        self.report = ReportAgent()

    def run(self, case: CaseState) -> CaseState:
        case.status = CaseStatus.running
        self.repository.save_case(case)

        context = AgentContext(case=case, adapter=self.adapter)

        readiness_output = self._record(context, self.data_readiness)
        readiness = ReadinessReport(**readiness_output.metadata["readiness_report"])
        case.readiness = readiness
        context.readiness = readiness

        self._record(context, self.team_leader)

        if readiness.gate == ReadinessGate.block:
            self._finish_with_report(context, status=CaseStatus.blocked)
            return self.repository.save_case(case)

        if readiness.gate == ReadinessGate.triage_only:
            self._record(context, self.triage)
            self._record(context, self.verifier)
            self._finish_with_report(context, status=CaseStatus.completed)
            return self.repository.save_case(case)

        for agent in self._analysis_agents():
            output = self._record(context, agent)
            if output.recommended_actions:
                self.repository.register_actions(case, output.recommended_actions)

        self._record(context, self.verifier)
        pending_approvals = [action for action in case.approvals if action.requires_approval]
        status = CaseStatus.awaiting_approval if pending_approvals else CaseStatus.completed
        self._finish_with_report(context, status=status)
        return self.repository.save_case(case)

    def _analysis_agents(self) -> List[BaseAgent]:
        return [self.triage, self.intel, self.timeline, self.impact, self.response]

    def _record(self, context: AgentContext, agent: BaseAgent) -> AgentEnvelope:
        output = agent.run(context)
        context.outputs.append(output)
        self.repository.append_agent_output(context.case, output)
        return output

    def _finish_with_report(self, context: AgentContext, status: CaseStatus) -> None:
        report_output = self._record(context, self.report)
        context.case.final_report = report_output.metadata.get("final_report")
        context.case.status = status

