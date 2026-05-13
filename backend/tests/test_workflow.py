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
    max_output_tokens = 1200
    settings = SimpleNamespace(openai_max_output_tokens=1200, llm_retry_attempts=3, llm_retry_delay_seconds=0)

    def __init__(self, missing_readiness=False, missing_report=False, missing_findings=False):
        self.missing_readiness = missing_readiness
        self.missing_report = missing_report
        self.missing_findings = missing_findings
        self.client = FakeOpenAIClient(self)

    def _get_client(self):
        return self.client

    def status(self):
        return {"enabled": self.enabled, "configured": True, "model": self.model, "provider": "fake"}

    def create_chat_completion(self, *, messages, tools=None, response_format=None):
        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools or [],
            tool_choice="auto",
            response_format=response_format,
            max_tokens=self.max_output_tokens,
        )


class DisabledLLM(FakeLLM):
    enabled = False

    def status(self):
        return {"enabled": False, "configured": False, "model": self.model, "provider": "fake"}


class FlakyLLM(FakeLLM):
    def __init__(self):
        super().__init__()
        self.failures_remaining = 1

    def create_chat_completion(self, *, messages, tools=None, response_format=None):
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("temporary provider error")
        return super().create_chat_completion(messages=messages, tools=tools, response_format=response_format)


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
        if self.llm.missing_findings:
            base["key_findings"] = []
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
        if agent == "report" and not self.llm.missing_report:
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


def test_missing_readiness_report_recovers_with_data_gate():
    workflow, repository = make_workflow(llm=FakeLLM(missing_readiness=True))
    case = make_case(repository, doc_id="doc-bad-readiness")

    updated = workflow.run(case)

    assert updated.status == CaseStatus.awaiting_approval
    assert updated.readiness is not None
    assert updated.readiness.gate.value == "allow"
    assert any(item["event"]["type"] == "readiness_report_recovered" for item in repository.adapter.audit)


def test_empty_llm_findings_fall_back_to_role_specific_alert_findings():
    workflow, repository = make_workflow(llm=FakeLLM(missing_findings=True))
    case = make_case(repository, doc_id="doc-empty-findings")
    case.raw_alert["rule"]["description"] = "Multiple failed logins followed by success from unusual source"
    case.raw_alert["event"] = {"action": "authentication_success_after_failures", "category": "authentication"}

    updated = workflow.run(case)

    all_findings = [finding for output in updated.agent_outputs for finding in output.key_findings]
    assert "No role-specific finding returned." not in all_findings
    triage = next(output for output in updated.agent_outputs if output.agent == "triage")
    timeline = next(output for output in updated.agent_outputs if output.agent == "timeline")
    response = next(output for output in updated.agent_outputs if output.agent == "response")
    assert any("Multiple failed logins followed by success from unusual source" in item for item in triage.key_findings)
    assert any("Known sequence" in item and "Multiple failed logins followed by success" in item for item in timeline.key_findings)
    assert any("zhang.wei" in item and "win-finance-07" in item for item in response.key_findings)


def test_rerun_starts_from_clean_agent_outputs():
    workflow, repository = make_workflow()
    case = make_case(repository, doc_id="doc-rerun")

    first = workflow.run(case)
    second = workflow.run(first)

    assert [output.agent for output in second.agent_outputs] == [
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
    assert len(second.agent_outputs) == 9
    assert any(
        item["event"]["type"] == "case_run_started" and item["event"]["cleared"]["agent_outputs"] == 9
        for item in repository.adapter.audit
    )


def test_final_report_fallback_includes_important_signals():
    workflow, repository = make_workflow(llm=FakeLLM(missing_report=True))
    case = make_case(repository, doc_id="doc-missing-report")

    updated = workflow.run(case)

    assert "## Important Signals" in updated.final_report
    assert "HIGH approval required: Review host isolation" in updated.final_report
    assert "## Agent Team Findings" not in updated.final_report


def test_transient_llm_failure_is_retried():
    workflow, repository = make_workflow(llm=FlakyLLM())
    case = make_case(repository, doc_id="doc-flaky")

    updated = workflow.run(case)

    assert updated.status == CaseStatus.awaiting_approval
    assert any(item["event"]["type"] == "agent_llm_retry" for item in repository.adapter.audit)
