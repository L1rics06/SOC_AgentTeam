import json
import re
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.models import ApprovalDecision, ApprovalStatus, CaseStatus, IntakePayload
from app.opensearch_adapter import InMemoryOpenSearchAdapter
from app.store import CaseRepository
from app.workflow import AgentWorkflow


class FakeLLM:
    enabled = True
    model = "fake-soc-agent-model"
    settings = SimpleNamespace(openai_max_output_tokens=1200)

    def __init__(self, missing_readiness=False):
        self.missing_readiness = missing_readiness
        self.client = FakeOpenAIClient(self)

    def _get_client(self):
        return self.client

    def status(self):
        return {"enabled": self.enabled, "configured": True, "model": self.model, "provider": "fake"}


class DisabledLLM(FakeLLM):
    enabled = False

    def status(self):
        return {"enabled": False, "configured": False, "model": self.model, "provider": "fake"}


class FakeOpenAIClient:
    def __init__(self, llm):
        self.llm = llm
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, tools, tool_choice, response_format, max_tokens):
        system = messages[0]["content"]
        agent = re.search(r"Agent name: ([a-z_]+)", system).group(1)
        payload = self._payload(agent)
        message = SimpleNamespace(content=json.dumps(payload), tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def _payload(self, agent):
        base = {
            "summary": f"{agent} completed its SOC task.",
            "key_findings": [f"{agent} stayed evidence-bound."],
            "confidence": 0.74,
            "recommended_actions": [],
            "policy_flags": [],
            "needs_human_approval": False,
            "metadata": {},
        }
        if agent == "data_readiness" and not self.llm.missing_readiness:
            base["metadata"]["readiness_report"] = {
                "readiness_score": 86,
                "gate": "allow",
                "gaps": [],
                "allowed_actions": ["run_full_agent_team", "recommend_approval_gated_response"],
                "confidence_cap": 0.82,
                "normalized_fields": {
                    "timestamp": "2026-05-10T08:30:00Z",
                    "severity": "high",
                    "entities": {"hosts": ["win-finance-07"], "users": ["zhang.wei"], "ips": ["203.0.113.77"]},
                },
            }
        if agent == "team_leader":
            base["metadata"]["tasks"] = ["triage", "intel", "timeline", "impact", "response"]
        if agent == "response":
            base["summary"] = "Recommend approval-gated containment and evidence preservation."
            base["recommended_actions"] = [
                {
                    "action_type": "isolate_host",
                    "title": "Review host isolation",
                    "description": "Analyst should review evidence before isolating win-finance-07.",
                    "risk": "high",
                    "requires_approval": False,
                    "playbook": "host-isolation",
                }
            ]
        if agent == "verifier":
            base["policy_flags"] = ["high_risk_action_requires_human_approval"]
            base["needs_human_approval"] = True
        if agent == "report":
            base["metadata"]["final_report"] = "# SOC Agent Team Report\n\nVerified LLM agent-team output."
        return base


def make_workflow(llm=None):
    settings = Settings(demo_mode=True)
    adapter = InMemoryOpenSearchAdapter(settings)
    repository = CaseRepository(adapter, settings)
    return AgentWorkflow(repository, adapter, llm=llm or FakeLLM()), repository


def make_case(repository, doc_id="doc-1"):
    return repository.create_case(
        IntakePayload(
            source_type="opensearch_alert",
            source="wazuh",
            index="wazuh-alerts-*",
            doc_id=doc_id,
            raw_alert={
                "@timestamp": "2026-05-10T08:30:00Z",
                "severity": "high",
                "rule": {"level": 12, "description": "Suspicious login"},
                "host": {"name": "win-finance-07"},
                "user": {"name": "zhang.wei"},
                "source": {"ip": "203.0.113.77"},
            },
        )
    )


def test_workflow_runs_intake_to_approval_queue():
    workflow, repository = make_workflow()
    case = make_case(repository)

    updated = workflow.run(case)

    assert updated.status == CaseStatus.awaiting_approval
    assert updated.readiness is not None
    assert updated.final_report
    assert [output.agent for output in updated.agent_outputs] == [
        "data_readiness",
        "team_leader",
        "triage",
        "intel",
        "timeline",
        "impact",
        "response",
        "verifier",
        "report",
    ]
    assert any(message.from_agent == "team_leader" for message in updated.team_messages)
    assert all(output.metadata["agent_runtime"] == "multi_agent_llm_team" for output in updated.agent_outputs)


def test_high_risk_llm_action_is_forced_to_human_approval():
    workflow, repository = make_workflow()
    case = make_case(repository)

    updated = workflow.run(case)

    response_output = next(output for output in updated.agent_outputs if output.agent == "response")
    high_risk = [action for action in response_output.recommended_actions if action.risk.value == "high"]
    assert high_risk
    assert all(action.requires_approval for action in high_risk)
    assert updated.approvals[0].requires_approval


def test_approval_decision_completes_case_when_no_pending_actions():
    workflow, repository = make_workflow()
    case = make_case(repository, doc_id="doc-2")
    updated = workflow.run(case)
    pending = [action for action in updated.approvals if action.requires_approval][0]

    decided = repository.update_approval(
        pending.action_id,
        ApprovalDecision(decision=ApprovalStatus.approved, approver="analyst"),
    )

    assert decided is not None
    assert decided.status in {CaseStatus.awaiting_approval, CaseStatus.completed}


def test_workflow_fails_when_llm_team_is_not_configured():
    workflow, repository = make_workflow(llm=DisabledLLM())
    case = make_case(repository, doc_id="doc-disabled")

    with pytest.raises(RuntimeError, match="LLM agent team is required"):
        workflow.run(case)

    assert repository.get_case(case.case_id).status == CaseStatus.failed


def test_missing_readiness_report_fails_without_local_analysis_path():
    workflow, repository = make_workflow(llm=FakeLLM(missing_readiness=True))
    case = make_case(repository, doc_id="doc-bad-readiness")

    with pytest.raises(RuntimeError, match="LLM agent team failed"):
        workflow.run(case)

    assert repository.get_case(case.case_id).status == CaseStatus.failed
