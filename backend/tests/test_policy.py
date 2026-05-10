from app.agents.base import AgentContext
from app.agents.data_readiness import DataReadinessAgent
from app.agents.response import ResponseAgent
from app.config import Settings
from app.models import CaseState, EvidenceRef, ReadinessReport, RiskLevel
from app.opensearch_adapter import InMemoryOpenSearchAdapter


def test_high_risk_response_requires_approval():
    settings = Settings(demo_mode=True)
    adapter = InMemoryOpenSearchAdapter(settings)
    case = CaseState(
        case_id="CASE-POLICY",
        title="Policy case",
        source_type="opensearch_alert",
        source="wazuh",
        raw_alert={
            "@timestamp": "2026-05-10T08:30:00Z",
            "severity": "high",
            "rule": {"level": 12, "description": "Malware detected"},
            "host": {"name": "win-finance-07"},
            "source": {"ip": "203.0.113.77"},
        },
        evidence_refs=[
            EvidenceRef(source="wazuh", index="wazuh-alerts-*", doc_id="doc-3", reason="Original alert")
        ],
    )
    readiness_output = DataReadinessAgent().run(AgentContext(case=case, adapter=adapter))
    readiness = ReadinessReport(**readiness_output.metadata["readiness_report"])

    output = ResponseAgent().run(AgentContext(case=case, adapter=adapter, readiness=readiness))

    high_risk = [action for action in output.recommended_actions if action.risk == RiskLevel.high]
    assert high_risk
    assert all(action.requires_approval for action in high_risk)
    assert output.needs_human_approval

