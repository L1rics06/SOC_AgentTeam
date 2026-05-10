from app.agents.base import AgentContext
from app.agents.data_readiness import DataReadinessAgent
from app.config import Settings
from app.models import CaseState, EvidenceRef, ReadinessGate
from app.opensearch_adapter import InMemoryOpenSearchAdapter


def make_case(raw_alert, doc_id="doc-1"):
    return CaseState(
        case_id="CASE-TEST",
        title="Test case",
        source_type="opensearch_alert",
        source="wazuh",
        raw_alert=raw_alert,
        evidence_refs=[
            EvidenceRef(source="wazuh", index="wazuh-alerts-*", doc_id=doc_id, reason="Original alert")
        ],
    )


def test_readiness_allows_complete_alert():
    settings = Settings(demo_mode=True)
    adapter = InMemoryOpenSearchAdapter(settings)
    case = make_case(
        {
            "@timestamp": "2026-05-10T08:30:00Z",
            "severity": "high",
            "rule": {"description": "Suspicious login"},
            "host": {"name": "win-finance-07"},
            "source": {"ip": "203.0.113.77"},
        }
    )

    output = DataReadinessAgent().run(AgentContext(case=case, adapter=adapter))

    report = output.metadata["readiness_report"]
    assert report["gate"] == ReadinessGate.allow
    assert report["readiness_score"] >= 80
    assert not output.policy_flags


def test_readiness_blocks_sparse_alert():
    settings = Settings(demo_mode=True)
    adapter = InMemoryOpenSearchAdapter(settings)
    case = make_case({"message": "unknown event"}, doc_id=None)

    output = DataReadinessAgent().run(AgentContext(case=case, adapter=adapter))

    report = output.metadata["readiness_report"]
    assert report["gate"] in {ReadinessGate.block, ReadinessGate.triage_only}
    assert report["gaps"]

