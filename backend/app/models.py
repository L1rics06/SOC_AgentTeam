from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .utils import utc_now


class ReadinessGate(str, Enum):
    allow = "allow"
    limited = "limited"
    triage_only = "triage_only"
    block = "block"


class CaseStatus(str, Enum):
    open = "open"
    running = "running"
    awaiting_approval = "awaiting_approval"
    completed = "completed"
    blocked = "blocked"
    failed = "failed"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ApprovalStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class EvidenceRef(BaseModel):
    source: str
    doc_id: Optional[str] = None
    index: Optional[str] = None
    timestamp: Optional[str] = None
    reason: str
    query: Optional[Dict[str, Any]] = None
    fields: Dict[str, Any] = Field(default_factory=dict)


class RecommendedAction(BaseModel):
    action_id: Optional[str] = None
    action_type: str
    title: str
    description: str
    risk: RiskLevel = RiskLevel.medium
    requires_approval: bool = True
    playbook: Optional[str] = None
    status: ApprovalStatus = ApprovalStatus.pending


class AgentEnvelope(BaseModel):
    agent: str
    case_id: str
    task_id: str
    readiness_gate: ReadinessGate
    summary: str
    key_findings: List[str] = Field(default_factory=list)
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    recommended_actions: List[RecommendedAction] = Field(default_factory=list)
    policy_flags: List[str] = Field(default_factory=list)
    needs_human_approval: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Any = Field(default_factory=utc_now)


class ReadinessReport(BaseModel):
    readiness_score: int = Field(ge=0, le=100)
    gate: ReadinessGate
    gaps: List[str] = Field(default_factory=list)
    allowed_actions: List[str] = Field(default_factory=list)
    confidence_cap: float = Field(default=0.8, ge=0.0, le=1.0)
    normalized_fields: Dict[str, Any] = Field(default_factory=dict)


class IntakePayload(BaseModel):
    source_type: str = "generic_alert"
    source: str = "manual"
    index: Optional[str] = None
    doc_id: Optional[str] = None
    raw_alert: Dict[str, Any]
    labels: List[str] = Field(default_factory=list)
    received_at: Any = Field(default_factory=utc_now)


class ApprovalDecision(BaseModel):
    decision: ApprovalStatus
    approver: str
    comment: Optional[str] = None


class CaseState(BaseModel):
    case_id: str
    title: str
    source_type: str
    source: str
    status: CaseStatus = CaseStatus.open
    raw_alert: Dict[str, Any]
    labels: List[str] = Field(default_factory=list)
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    readiness: Optional[ReadinessReport] = None
    agent_outputs: List[AgentEnvelope] = Field(default_factory=list)
    approvals: List[RecommendedAction] = Field(default_factory=list)
    final_report: Optional[str] = None
    created_at: Any = Field(default_factory=utc_now)
    updated_at: Any = Field(default_factory=utc_now)


class CaseListResponse(BaseModel):
    cases: List[CaseState]

