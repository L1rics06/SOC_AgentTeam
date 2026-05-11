"""Agent Team Runner：编排多个 LLM Agent 协作完成 SOC Case 分析。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..models import AgentEnvelope, CaseState, CaseStatus, ReadinessGate, ReadinessReport, RecommendedAction, RiskLevel
from ..opensearch_adapter import OpenSearchAdapter
from ..store import CaseRepository
from ..utils import extract_entities, model_to_dict, nested_get, redact_sensitive, short_id
from .mailbox import TeamMailbox
from .skills import SkillRegistry
from .specs import AGENT_SPECS, AgentSpec
from .tools import ToolRegistry


class AgentTeamRunner:
    """执行 Data Readiness、Team Leader、专家 Agent、Verifier 和 Report 的完整链路。"""

    def __init__(self, llm: Any, adapter: OpenSearchAdapter, repository: CaseRepository):
        self.llm = llm
        self.adapter = adapter
        self.repository = repository
        self.skills = SkillRegistry()

    def run(self, case: CaseState) -> bool:
        """运行多 Agent 调查流程；成功时直接修改传入的 CaseState。"""
        client = self.llm._get_client()
        if client is None:
            return False

        mailbox = TeamMailbox(case)
        mailbox.send("system", "all", "case_opened", f"Case {case.case_id} opened: {case.title}")

        # 先做数据可用性判断，后续 Agent 的任务范围和置信度都受它约束。
        readiness_output = self._run_agent(client, AGENT_SPECS["data_readiness"], case, mailbox, None, [])
        if readiness_output is None:
            return False
        self._append_output(case, readiness_output)

        readiness = self._readiness_from_output(readiness_output)
        if readiness is None:
            self.adapter.write_audit_event(
                case.case_id,
                {
                    "type": "invalid_agent_output",
                    "agent": "data_readiness",
                    "reason": "metadata.readiness_report is required for LLM agent-team execution.",
                },
            )
            case.status = CaseStatus.failed
            return False
        case.readiness = readiness

        team_leader_output = self._run_agent(client, AGENT_SPECS["team_leader"], case, mailbox, readiness, [readiness_output])
        if team_leader_output is None:
            return False
        self._append_output(case, team_leader_output)

        tasks = self._planned_tasks(team_leader_output, readiness)
        # Team Leader 负责派工；Mailbox 记录这些任务，供各专家 Agent 读取。
        for task in tasks:
            mailbox.send(
                "team_leader",
                task,
                "task",
                f"Run {task} analysis for case {case.case_id}; cite evidence and send back material findings.",
            )
        mailbox.send("team_leader", "verifier", "task", "Verify the expert outputs, evidence chain, and approval policy.")
        mailbox.send("team_leader", "report", "task", "Prepare the final report after verification.")

        outputs = [readiness_output, team_leader_output]
        for name in tasks:
            # 专家 Agent 逐个运行，前序输出会作为上下文传入后续 Agent。
            output = self._run_agent(client, AGENT_SPECS[name], case, mailbox, readiness, outputs)
            if output is None:
                return False
            outputs.append(output)
            self._append_output(case, output)
            if output.recommended_actions:
                self.repository.register_actions(case, output.recommended_actions)

        for name in ["verifier", "report"]:
            # Verifier 做证据与策略校验，Report 在最后汇总成 analyst-facing 报告。
            output = self._run_agent(client, AGENT_SPECS[name], case, mailbox, readiness, outputs)
            if output is None:
                return False
            outputs.append(output)
            self._append_output(case, output)

        case.final_report = self._final_report(outputs, case)
        pending = [action for action in case.approvals if action.requires_approval and action.status.value == "pending"]
        if readiness.gate == ReadinessGate.block:
            case.status = CaseStatus.blocked
        else:
            case.status = CaseStatus.awaiting_approval if pending else CaseStatus.completed

        self.adapter.write_audit_event(
            case.case_id,
            {
                "type": "agent_team_completed",
                "mode": "multi_agent_llm_team",
                "model": self.llm.model,
                "messages": len(case.team_messages),
                "agents": [output.agent for output in outputs],
            },
        )
        return True

    def _run_agent(
        self,
        client: Any,
        spec: AgentSpec,
        case: CaseState,
        mailbox: TeamMailbox,
        readiness: Optional[ReadinessReport],
        prior_outputs: List[AgentEnvelope],
    ) -> Optional[AgentEnvelope]:
        """调用一次 LLM Agent，并处理最多 6 轮工具调用。"""
        tool_registry = ToolRegistry(spec.name, case, self.adapter, mailbox, self.skills)
        tools = tool_registry.definitions(spec.allowed_tools)
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt(spec)},
            {"role": "user", "content": self._user_prompt(spec, case, mailbox, readiness, prior_outputs)},
        ]

        try:
            for _ in range(6):
                response = client.chat.completions.create(
                    model=self.llm.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    response_format={"type": "json_object"},
                    max_tokens=self.llm.settings.openai_max_output_tokens,
                )
                message = response.choices[0].message
                tool_calls = list(message.tool_calls or [])
                if not tool_calls:
                    return self._envelope_from_content(spec, case, readiness, message.content or "{}", tool_registry.calls)

                # Chat Completions 需要把 assistant 的 tool_calls 和每个 tool 结果依次写回消息历史。
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.function.name,
                                    "arguments": call.function.arguments,
                                },
                            }
                            for call in tool_calls
                        ],
                    }
                )
                for call in tool_calls:
                    result = tool_registry.execute(call.function.name, call.function.arguments)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.function.name,
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    )
        except Exception as exc:
            self.adapter.write_audit_event(case.case_id, {"type": "agent_run_failed", "agent": spec.name, "error": str(exc)})
            return None
        return None

    def _system_prompt(self, spec: AgentSpec) -> str:
        """生成 Agent 的系统提示，包括角色、规则和预加载技能。"""
        preloaded = self.skills.preload(spec.required_skills)
        skill_block = "\n\n".join([f"## Skill: {name}\n{content}" for name, content in preloaded.items()])
        return (
            f"You are {spec.role} in an LLM-driven SOC Agent Team.\n"
            f"Agent name: {spec.name}\n"
            f"Goal: {spec.goal}\n"
            "You are not a scripted parser. Use your SOC judgement, communicate with peers, and call tools when useful.\n"
            "Rules:\n"
            "- Stay evidence-bound. Never invent logs, assets, users, threat-intel hits, or approvals.\n"
            "- Use the mailbox to coordinate important assumptions, requests, and conclusions.\n"
            "- Use OpenSearch tools when more evidence or context would materially improve the answer.\n"
            "- Do not execute containment. Recommend actions only.\n"
            "- Medium, high, and critical risk actions must require human approval.\n"
            "- Final answer must be strict JSON with keys: summary, key_findings, confidence, recommended_actions, "
            "policy_flags, needs_human_approval, metadata.\n"
            f"{spec.output_notes}\n\n"
            f"Preloaded skills:\n{skill_block}"
        )

    def _user_prompt(
        self,
        spec: AgentSpec,
        case: CaseState,
        mailbox: TeamMailbox,
        readiness: Optional[ReadinessReport],
        prior_outputs: List[AgentEnvelope],
    ) -> str:
        """生成 Agent 的用户提示，把 Case、Mailbox、前序输出压成 JSON。"""
        entities = extract_entities(case.raw_alert)
        payload = {
            "task": self._task_for(spec, readiness),
            "case": {
                "case_id": case.case_id,
                "title": case.title,
                "source_type": case.source_type,
                "source": case.source,
                "labels": case.labels,
                "raw_alert": redact_sensitive(case.raw_alert),
                "evidence_refs": [model_to_dict(item) for item in case.evidence_refs],
                "entities": entities,
                "message": nested_get(case.raw_alert, ["rule.description", "message", "event.action", "alert.title"]),
            },
            "readiness": model_to_dict(readiness) if readiness else None,
            "mailbox": mailbox.read(spec.name),
            "prior_agent_outputs": [
                {
                    "agent": output.agent,
                    "summary": output.summary,
                    "key_findings": output.key_findings,
                    "policy_flags": output.policy_flags,
                    "recommended_actions": [model_to_dict(action) for action in output.recommended_actions],
                }
                for output in prior_outputs
            ],
            "available_peer_agents": list(AGENT_SPECS.keys()),
            "required_skills": spec.required_skills,
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

    @staticmethod
    def _task_for(spec: AgentSpec, readiness: Optional[ReadinessReport]) -> str:
        """根据 Agent 名称和 Readiness Gate 生成本轮任务说明。"""
        if spec.name == "data_readiness":
            return (
                "Assess data quality. Return metadata.readiness_report with readiness_score, gate, gaps, "
                "allowed_actions, confidence_cap, normalized_fields."
            )
        if spec.name == "team_leader":
            return "Create an investigation plan and send task messages to the expert agents."
        if spec.name == "response":
            return "Recommend evidence-preserving or containment actions. Do not execute anything."
        if spec.name == "verifier":
            return "Independently verify evidence support, policy compliance, and approval requirements."
        if spec.name == "report":
            return "Write the final case report in metadata.final_report markdown using verified outputs."
        gate = readiness.gate.value if readiness else "unknown"
        return f"Perform your expert SOC analysis for readiness gate {gate}."

    def _envelope_from_content(
        self,
        spec: AgentSpec,
        case: CaseState,
        readiness: Optional[ReadinessReport],
        content: str,
        tool_calls: List[Dict[str, Any]],
    ) -> AgentEnvelope:
        """把 LLM JSON 输出规范化为 AgentEnvelope，并补充运行元数据。"""
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            payload = {"summary": content, "key_findings": [], "metadata": {"raw_response": content}}
        if not isinstance(payload, dict):
            payload = {"summary": str(payload), "metadata": {}}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        # 元数据统一记录运行时、模型、技能和工具调用，方便审计与排障。
        metadata = {
            **metadata,
            "agent_runtime": "multi_agent_llm_team",
            "llm": {"used": True, "provider": "openai", "model": self.llm.model},
            "skills": spec.required_skills,
            "tools": tool_calls,
        }
        gate = readiness.gate if readiness else ReadinessGate.triage_only
        confidence = self._bounded_float(payload.get("confidence"), readiness.confidence_cap if readiness else 0.5)
        if readiness:
            confidence = min(confidence, readiness.confidence_cap)
        actions = self._recommended_actions(case.case_id, spec.name, payload.get("recommended_actions"))
        return AgentEnvelope(
            agent=spec.name,
            case_id=case.case_id,
            task_id=short_id("TASK", f"{case.case_id}:{spec.name}:llm-team"),
            readiness_gate=gate,
            summary=self._text(payload.get("summary"), f"{spec.name} completed."),
            key_findings=self._string_list(payload.get("key_findings")) or ["No role-specific finding returned."],
            evidence_refs=list(case.evidence_refs),
            confidence=confidence,
            recommended_actions=actions,
            policy_flags=self._string_list(payload.get("policy_flags")),
            needs_human_approval=bool(payload.get("needs_human_approval")) or any(action.requires_approval for action in actions),
            metadata=metadata,
        )

    def _readiness_from_output(self, output: AgentEnvelope) -> Optional[ReadinessReport]:
        """从 Data Readiness Agent 的 metadata 中解析 ReadinessReport。"""
        raw = output.metadata.get("readiness_report")
        if not isinstance(raw, dict):
            return None
        try:
            return ReadinessReport(**raw)
        except Exception:
            return None

    def _planned_tasks(self, output: AgentEnvelope, readiness: ReadinessReport) -> List[str]:
        """合并 Team Leader 建议任务和 Gate 要求，得到实际专家 Agent 队列。"""
        raw_tasks = output.metadata.get("tasks")
        if isinstance(raw_tasks, list):
            tasks = [str(task) for task in raw_tasks if str(task) in AGENT_SPECS]
        else:
            tasks = []
        if readiness.gate == ReadinessGate.block:
            return []
        if readiness.gate == ReadinessGate.triage_only:
            required = ["triage"]
        else:
            required = ["triage", "intel", "timeline", "impact", "response"]
        ordered = []
        for task in tasks + required:
            if task in required and task not in ordered:
                ordered.append(task)
        return ordered

    def _append_output(self, case: CaseState, output: AgentEnvelope) -> None:
        """通过仓储追加 Agent 输出，保持 trace 写入一致。"""
        self.repository.append_agent_output(case, output)

    def _final_report(self, outputs: List[AgentEnvelope], case: CaseState) -> str:
        """优先使用 Report Agent 生成的 final_report，否则降级拼接摘要。"""
        for output in reversed(outputs):
            report = output.metadata.get("final_report")
            if isinstance(report, str) and report.strip():
                return report
        lines = [f"# Case {case.case_id}: {case.title}", "", "## Agent Team Findings"]
        for output in outputs:
            lines.append(f"- {output.agent}: {output.summary}")
        return "\n".join(lines)

    def _recommended_actions(self, case_id: str, agent: str, raw_actions: Any) -> List[RecommendedAction]:
        """清洗 LLM 返回的推荐动作，并强制中高风险动作需要审批。"""
        if not isinstance(raw_actions, list):
            return []
        actions: List[RecommendedAction] = []
        for raw in raw_actions[:8]:
            if not isinstance(raw, dict):
                continue
            risk = self._risk(raw.get("risk"))
            requires_approval = bool(raw.get("requires_approval", True))
            if risk in {RiskLevel.medium, RiskLevel.high, RiskLevel.critical}:
                requires_approval = True
            title = self._text(raw.get("title"), "Review SOC action")
            action_type = self._text(raw.get("action_type"), "review_action").lower().replace(" ", "_")
            actions.append(
                RecommendedAction(
                    action_id=short_id("ACT", f"{case_id}:{agent}:{action_type}:{title}"),
                    action_type=action_type,
                    title=title,
                    description=self._text(raw.get("description"), "Review this recommendation before execution."),
                    risk=risk,
                    requires_approval=requires_approval,
                    playbook=raw.get("playbook") if isinstance(raw.get("playbook"), str) else None,
                )
            )
        return actions

    @staticmethod
    def _risk(value: Any) -> RiskLevel:
        """把 LLM 返回的风险字符串转成枚举，异常时默认 medium。"""
        try:
            return RiskLevel(str(value).lower())
        except Exception:
            return RiskLevel.medium

    @staticmethod
    def _bounded_float(value: Any, default: float) -> float:
        """把置信度限制在 0 到 1 之间。"""
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
        return max(0.0, min(1.0, default))

    @staticmethod
    def _text(value: Any, default: str) -> str:
        """读取非空字符串并限制长度，避免超长 LLM 输出污染状态。"""
        if isinstance(value, str) and value.strip():
            return value.strip()[:1200]
        return default

    @staticmethod
    def _string_list(value: Any) -> List[str]:
        """把列表字段规范化成最多 10 条短字符串。"""
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:700] for item in value if str(item).strip()][:10]
