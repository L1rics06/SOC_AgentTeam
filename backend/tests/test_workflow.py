from app.config import Settings
from app.models import ApprovalDecision, ApprovalStatus, CaseStatus, IntakePayload
from app.opensearch_adapter import InMemoryOpenSearchAdapter
from app.store import CaseRepository
from app.workflow import AgentWorkflow


def test_workflow_runs_intake_to_approval_queue():
    settings = Settings(demo_mode=True)
    adapter = InMemoryOpenSearchAdapter(settings)
    repository = CaseRepository(adapter, settings)
    workflow = AgentWorkflow(repository, adapter)
    case = repository.create_case(
        IntakePayload(
            source_type="opensearch_alert",
            source="wazuh",
            index="wazuh-alerts-*",
            doc_id="doc-1",
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

    updated = workflow.run(case)

    assert updated.status == CaseStatus.awaiting_approval
    assert updated.readiness is not None
    assert updated.final_report
    assert any(action.requires_approval for action in updated.approvals)
    assert "verifier" in [output.agent for output in updated.agent_outputs]


def test_approval_decision_completes_case_when_no_pending_actions():
    settings = Settings(demo_mode=True)
    adapter = InMemoryOpenSearchAdapter(settings)
    repository = CaseRepository(adapter, settings)
    workflow = AgentWorkflow(repository, adapter)
    case = repository.create_case(
        IntakePayload(
            source_type="opensearch_alert",
            source="wazuh",
            index="wazuh-alerts-*",
            doc_id="doc-2",
            raw_alert={
                "@timestamp": "2026-05-10T08:30:00Z",
                "severity": "high",
                "rule": {"level": 12, "description": "Suspicious login"},
                "host": {"name": "win-finance-07"},
                "source": {"ip": "203.0.113.77"},
            },
        )
    )
    updated = workflow.run(case)
    pending = [action for action in updated.approvals if action.requires_approval][0]

    decided = repository.update_approval(
        pending.action_id,
        ApprovalDecision(decision=ApprovalStatus.approved, approver="analyst"),
    )

    assert decided is not None
    assert decided.status in {CaseStatus.awaiting_approval, CaseStatus.completed}

