"""Pydantic 数据模型：定义告警、Case、Agent 输出和审批对象。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .utils import utc_now


class ReadinessGate(str, Enum):
    """数据可用性门禁，决定 Agent Team 能做到哪一步。"""

    allow = "allow"
    limited = "limited"
    triage_only = "triage_only"
    block = "block"


class CaseStatus(str, Enum):
    """Case 生命周期状态。"""

    open = "open"
    running = "running"
    awaiting_approval = "awaiting_approval"
    completed = "completed"
    blocked = "blocked"
    failed = "failed"


class RiskLevel(str, Enum):
    """推荐动作的风险等级。"""

    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class ApprovalStatus(str, Enum):
    """人工审批状态。"""

    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class EvidenceRef(BaseModel):
    """引用一条证据来源，确保结论能追溯到日志、文档或查询。"""

    source: str
    doc_id: Optional[str] = None
    index: Optional[str] = None
    timestamp: Optional[str] = None
    reason: str
    query: Optional[Dict[str, Any]] = None
    fields: Dict[str, Any] = Field(default_factory=dict)


class RecommendedAction(BaseModel):
    """Agent 给出的处置建议；中高风险动作默认需要人工审批。"""

    action_id: Optional[str] = None
    action_type: str
    title: str
    description: str
    risk: RiskLevel = RiskLevel.medium
    requires_approval: bool = True
    playbook: Optional[str] = None
    status: ApprovalStatus = ApprovalStatus.pending


class AgentEnvelope(BaseModel):
    """每个 Agent 的标准化输出信封，便于记录、验证和汇总。"""

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


class TeamMessage(BaseModel):
    """Agent 之间通过 Mailbox 传递的结构化消息。"""

    message_id: str
    from_agent: str
    to_agent: str
    message_type: str = "note"
    content: str
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    created_at: Any = Field(default_factory=utc_now)


class ReadinessReport(BaseModel):
    """Data Readiness Agent 输出的数据质量和行动范围评估。"""

    readiness_score: int = Field(ge=0, le=100)
    gate: ReadinessGate
    gaps: List[str] = Field(default_factory=list)
    allowed_actions: List[str] = Field(default_factory=list)
    confidence_cap: float = Field(default=0.8, ge=0.0, le=1.0)
    normalized_fields: Dict[str, Any] = Field(default_factory=dict)


class IntakePayload(BaseModel):
    """外部告警进入系统时的统一载荷格式。"""

    source_type: str = "generic_alert"
    source: str = "manual"
    index: Optional[str] = None
    doc_id: Optional[str] = None
    raw_alert: Dict[str, Any]
    labels: List[str] = Field(default_factory=list)
    received_at: Any = Field(default_factory=utc_now)


class ApprovalDecision(BaseModel):
    """审批接口接收的人工决策。"""

    decision: ApprovalStatus
    approver: str
    comment: Optional[str] = None


class CaseState(BaseModel):
    """SOC Case 的完整状态快照，前端和存储层都围绕它交互。"""

    case_id: str
    title: str
    source_type: str
    source: str
    status: CaseStatus = CaseStatus.open
    raw_alert: Dict[str, Any]
    labels: List[str] = Field(default_factory=list)
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    readiness: Optional[ReadinessReport] = None
    team_messages: List[TeamMessage] = Field(default_factory=list)
    agent_outputs: List[AgentEnvelope] = Field(default_factory=list)
    approvals: List[RecommendedAction] = Field(default_factory=list)
    final_report: Optional[str] = None
    created_at: Any = Field(default_factory=utc_now)
    updated_at: Any = Field(default_factory=utc_now)


class CaseListResponse(BaseModel):
    """Case 列表接口响应。"""

    cases: List[CaseState]
