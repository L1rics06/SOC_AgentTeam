"""FastAPI 入口：装配配置、存储、工作流，并暴露 SOC Case API。"""

from __future__ import annotations

from typing import Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .models import ApprovalDecision, CaseListResponse, CaseState, CaseStatus, IntakePayload
from .opensearch_adapter import build_opensearch_adapter
from .store import CaseRepository
from .workflow import AgentWorkflow


settings = get_settings()
adapter = build_opensearch_adapter(settings)
repository = CaseRepository(adapter=adapter, settings=settings)
workflow = AgentWorkflow(repository=repository, adapter=adapter)

# 全局应用实例在模块加载时完成依赖装配，供 uvicorn 直接加载。
app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> Dict[str, Any]:
    """返回服务、Demo 模式和 LLM 配置状态，便于前端或探针检查。"""
    llm_status = workflow.llm.status()
    return {
        "status": "ok",
        "demo_mode": settings.demo_mode,
        "analysis_mode": "multi_agent_llm_team" if llm_status["enabled"] else "llm_agent_team_unconfigured",
        "openai_enabled": llm_status["enabled"],
        "openai_configured": llm_status["configured"],
        "openai_model": llm_status["model"],
        "llm_required": True,
    }


@app.get(f"{settings.api_prefix}/cases", response_model=CaseListResponse)
def list_cases() -> CaseListResponse:
    """列出当前进程内已创建的 Case。"""
    return CaseListResponse(cases=repository.list_cases())


@app.post(f"{settings.api_prefix}/cases/intake", response_model=CaseState)
def intake(payload: IntakePayload) -> CaseState:
    """仅接收入站告警并创建 Case，不触发 Agent 分析。"""
    return repository.create_case(payload)


@app.post(f"{settings.api_prefix}/analyze", response_model=CaseState)
def analyze(payload: IntakePayload) -> CaseState:
    """创建 Case 后立即运行完整 Agent Team 分析。"""
    case = repository.create_case(payload)
    try:
        return workflow.run(case)
    except Exception as exc:
        case.status = CaseStatus.failed
        repository.save_case(case)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post(f"{settings.api_prefix}/opensearch/webhook", response_model=CaseState)
def opensearch_webhook(payload: Dict[str, Any]) -> CaseState:
    """把 OpenSearch Alerting/Security Analytics Webhook 转换为统一 IntakePayload。"""
    alert = payload.get("alert") or payload.get("finding") or payload.get("ctx") or payload
    source = payload.get("source") or "opensearch_alerting"
    index = payload.get("index") or payload.get("_index")
    doc_id = payload.get("doc_id") or payload.get("_id")
    intake_payload = IntakePayload(
        source_type="opensearch_webhook",
        source=source,
        index=index,
        doc_id=doc_id,
        raw_alert=alert,
        labels=["opensearch-webhook"],
    )
    return repository.create_case(intake_payload)


@app.post(f"{settings.api_prefix}/cases/{{case_id}}/run", response_model=CaseState)
def run_case(case_id: str) -> CaseState:
    """对已存在 Case 手动触发一次 Agent Team 分析。"""
    case = repository.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        return workflow.run(case)
    except Exception as exc:
        case.status = CaseStatus.failed
        repository.save_case(case)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get(f"{settings.api_prefix}/cases/{{case_id}}", response_model=CaseState)
def get_case(case_id: str) -> CaseState:
    """查询单个 Case 的当前状态。"""
    case = repository.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@app.post(f"{settings.api_prefix}/approvals/{{action_id}}/decision", response_model=CaseState)
def decide_approval(action_id: str, decision: ApprovalDecision) -> CaseState:
    """记录人工审批结论，并同步更新 Case 状态。"""
    case = repository.update_approval(action_id, decision)
    if not case:
        raise HTTPException(status_code=404, detail="Action not found")
    return case
