"""Agent Team Runner：编排多个 LLM Agent 协作完成 SOC Case 分析。"""

from __future__ import annotations

import json
import time
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
        self.repository.save_case(case)

        # 先做数据可用性判断，后续 Agent 的任务范围和置信度都受它约束。
        readiness_output = self._run_agent(client, AGENT_SPECS["data_readiness"], case, mailbox, None, [])
        if readiness_output is None:
            return False

        readiness = self._readiness_from_output(readiness_output, case)
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
        readiness_output.metadata["readiness_report"] = model_to_dict(readiness)
        self._append_output(case, readiness_output)
        case.readiness = readiness
        self.repository.save_case(case)

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
        self.repository.save_case(case)

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
                self.repository.save_case(case)

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
                response = self._create_chat_completion_with_retries(
                    spec=spec,
                    case=case,
                    messages=messages,
                    tools=tools,
                    response_format={"type": "json_object"},
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

    def _create_chat_completion_with_retries(
        self,
        *,
        spec: AgentSpec,
        case: CaseState,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        response_format: Dict[str, Any],
    ) -> Any:
        """Call the LLM with short retries for transient provider failures."""
        settings = getattr(self.llm, "settings", None)
        attempts = max(1, int(getattr(settings, "llm_retry_attempts", 3)))
        delay_seconds = max(0.0, float(getattr(settings, "llm_retry_delay_seconds", 0.8)))
        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                return self.llm.create_chat_completion(
                    messages=messages,
                    tools=tools,
                    response_format=response_format,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                self.adapter.write_audit_event(
                    case.case_id,
                    {
                        "type": "agent_llm_retry",
                        "agent": spec.name,
                        "attempt": attempt,
                        "max_attempts": attempts,
                        "error": str(exc),
                    },
                )
                if delay_seconds:
                    time.sleep(delay_seconds * attempt)
        raise last_error or RuntimeError("LLM request failed")

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
            "- key_findings must be a non-empty array of role-specific, evidence-backed findings. "
            "If evidence is sparse, state the concrete data gap and the known alert fields instead of returning an empty list.\n"
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
            return (
                "Write a synthesized final case report in metadata.final_report markdown using verified outputs. "
                "Do not concatenate agent summaries. Include a section named '## Important Signals'."
            )
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
        payload = self._json_payload(content)
        if payload is None:
            payload = {"summary": content, "key_findings": [], "metadata": {"raw_response": content}}
        if not isinstance(payload, dict):
            payload = {"summary": str(payload), "metadata": {}}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        if spec.name == "report" and not metadata.get("final_report"):
            for key in ("final_report", "report_markdown", "report"):
                candidate = payload.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    metadata["final_report"] = candidate.strip()
                    break
        # 元数据统一记录运行时、模型、技能和工具调用，方便审计与排障。
        metadata = {
            **metadata,
            "agent_runtime": "multi_agent_llm_team",
            "llm": {"used": True, "provider": self.llm.status().get("provider", "unknown"), "model": self.llm.model},
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
            key_findings=self._findings_from_payload(payload, spec.name, case, readiness),
            evidence_refs=list(case.evidence_refs),
            confidence=confidence,
            recommended_actions=actions,
            policy_flags=self._string_list(payload.get("policy_flags")),
            needs_human_approval=bool(payload.get("needs_human_approval")) or any(action.requires_approval for action in actions),
            metadata=metadata,
        )

    def _readiness_from_output(self, output: AgentEnvelope, case: CaseState) -> Optional[ReadinessReport]:
        """从 Data Readiness Agent 的 metadata 中解析 ReadinessReport。"""
        raw = output.metadata.get("readiness_report")
        if not isinstance(raw, dict):
            recovered = self._fallback_readiness_report(case)
            self.adapter.write_audit_event(
                case.case_id,
                {
                    "type": "readiness_report_recovered",
                    "agent": output.agent,
                    "reason": "Agent output omitted metadata.readiness_report; derived a data-readiness gate from alert fields.",
                },
            )
            return recovered
        try:
            return ReadinessReport(**raw)
        except Exception:
            return None

    @staticmethod
    def _json_payload(content: str) -> Optional[Dict[str, Any]]:
        """Parse a JSON object, including common prose-wrapped JSON responses."""
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                payload = json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                return None
        return payload if isinstance(payload, dict) else None

    def _findings_from_payload(
        self,
        payload: Dict[str, Any],
        agent: str,
        case: CaseState,
        readiness: Optional[ReadinessReport],
    ) -> List[str]:
        """Read model findings with common aliases, then fall back to alert-grounded role findings."""
        candidates = [
            payload.get("key_findings"),
            payload.get("findings"),
            payload.get("keyFindings"),
            payload.get("key_points"),
        ]
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            candidates.extend(
                [
                    metadata.get("key_findings"),
                    metadata.get("findings"),
                    metadata.get("keyFindings"),
                    metadata.get("key_points"),
                ]
            )
        for candidate in candidates:
            findings = self._finding_list(candidate)
            if findings:
                return findings
        return self._fallback_key_findings(agent, case, readiness)

    def _fallback_key_findings(
        self,
        agent: str,
        case: CaseState,
        readiness: Optional[ReadinessReport],
    ) -> List[str]:
        """Create useful, evidence-bound findings when the LLM omits role findings."""
        alert = case.raw_alert
        entities = extract_entities(alert)
        description = self._text(
            nested_get(alert, ["rule.description", "message", "event.action", "alert.title"]),
            case.title,
        )
        action = nested_get(alert, ["event.action"], "unknown_action")
        category = nested_get(alert, ["event.category"], "unknown_category")
        timestamp = nested_get(alert, ["@timestamp", "timestamp", "event.created"], "unknown_time")
        severity = nested_get(alert, ["severity", "rule.level"], "unknown_severity")
        hosts = self._format_entities(entities.get("hosts", []), "no host")
        users = self._format_entities(entities.get("users", []), "no user")
        ips = self._format_entities(entities.get("ips", []), "no source IP")
        evidence = self._format_evidence(case)
        gaps = ", ".join(readiness.gaps) if readiness and readiness.gaps else "no readiness gaps recorded"
        alert_text = f"{description} {action} {category}".lower()
        is_auth_case = "auth" in alert_text or "login" in alert_text
        is_login_chain = ("failed" in alert_text and "success" in alert_text) or "success_after_failures" in alert_text
        case_type = "authentication" if is_auth_case else str(category)
        timeline_finding = (
            f"Known sequence from the alert is {description} at {timestamp}."
            if is_login_chain
            else f"Known event from the alert is {description} at {timestamp}."
        )
        timeline_correlation = (
            f"Event action is {action} in category {category}; adjacent failed-login and success logs should be correlated around this time."
            if is_login_chain
            else f"Event action is {action} in category {category}; related events should be correlated around this time."
        )
        response_scope = (
            f"Account/session review for user {users} and host {hosts} is the immediate response scope supported by the alert."
            if is_auth_case
            else f"Response scope supported by the alert is user {users}, host {hosts}, and source IP {ips}."
        )
        report_focus = (
            f"Report should highlight the failed-login-to-success chain, source IP {ips}, user {users}, and host {hosts}."
            if is_login_chain
            else f"Report should highlight alert '{description}', source IP {ips}, user {users}, and host {hosts}."
        )

        findings_by_agent: Dict[str, List[str]] = {
            "data_readiness": [
                f"Original {case.source} alert provides timestamp {timestamp}, severity {severity}, user {users}, host {hosts}, and source IP {ips}.",
                f"Evidence anchor is {evidence}; readiness gaps: {gaps}.",
            ],
            "team_leader": [
                f"Alert class is an authentication chain: {description}.",
                "Investigation should cover triage, timeline, impact, threat intel, and approval-gated response planning.",
            ],
            "triage": [
                f"{severity} {case_type} alert states: {description}.",
                f"Known target context is user {users} on host {hosts} from source IP {ips}.",
            ],
            "intel": [
                f"Source IP {ips} and account {users} are the primary enrichment targets for unusual-source login analysis.",
                "No reputation verdict is present in the alert payload, so intel confidence should remain bounded until enrichment is returned.",
            ],
            "timeline": [
                timeline_finding,
                timeline_correlation,
            ],
            "impact": [
                f"Currently confirmed affected entities are user {users} and host {hosts}.",
                "Blast radius beyond the listed account, host, and source IP is not confirmed by the current evidence.",
            ],
            "response": [
                "Response should preserve authentication evidence and avoid disruptive containment without analyst approval.",
                response_scope,
            ],
            "verifier": [
                f"Current support comes from {evidence} and the alert text: {description}.",
                "Claims about attacker reputation, lateral movement, or broader compromise require corroborating logs before being treated as verified.",
            ],
            "report": [
                report_focus,
                "Confidence limits and any approval-gated response actions should be explicit in the final case summary.",
            ],
        }
        return findings_by_agent.get(
            agent,
            [f"Alert evidence for {agent}: {description}; entities are user {users}, host {hosts}, source IP {ips}."],
        )

    @staticmethod
    def _fallback_readiness_report(case: CaseState) -> ReadinessReport:
        """Create a conservative data-readiness gate from fields already present in the alert."""
        entities = extract_entities(case.raw_alert)
        timestamp = nested_get(case.raw_alert, ["@timestamp", "timestamp", "event.created"])
        severity = nested_get(case.raw_alert, ["severity", "rule.level"])
        message = nested_get(case.raw_alert, ["rule.description", "message", "event.action", "alert.title"])
        gaps: List[str] = []
        if not timestamp:
            gaps.append("missing_timestamp")
        if not severity:
            gaps.append("missing_severity")
        if not message:
            gaps.append("missing_alert_description")
        if not entities.get("hosts"):
            gaps.append("missing_host_entity")
        if not entities.get("users"):
            gaps.append("missing_user_entity")
        if not entities.get("ips"):
            gaps.append("missing_ip_entity")
        if not case.evidence_refs:
            gaps.append("missing_evidence_reference")

        score = max(25, 90 - len(gaps) * 10)
        if score >= 75:
            gate = ReadinessGate.allow
            confidence_cap = 0.8
            allowed_actions = ["run_full_agent_team", "recommend_approval_gated_response"]
        elif score >= 50:
            gate = ReadinessGate.limited
            confidence_cap = 0.65
            allowed_actions = ["run_limited_agent_team", "request_missing_evidence"]
        else:
            gate = ReadinessGate.triage_only
            confidence_cap = 0.45
            allowed_actions = ["triage_only", "request_missing_evidence"]

        return ReadinessReport(
            readiness_score=score,
            gate=gate,
            gaps=gaps,
            allowed_actions=allowed_actions,
            confidence_cap=confidence_cap,
            normalized_fields={
                "timestamp": timestamp,
                "severity": severity,
                "message": message,
                "entities": entities,
            },
        )

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
        """Prefer Report Agent markdown; otherwise synthesize a structured report."""
        for output in reversed(outputs):
            report = output.metadata.get("final_report")
            if isinstance(report, str) and report.strip():
                return self._ensure_important_signals(report.strip(), outputs, case)
        for output in reversed(outputs):
            raw_report = output.metadata.get("raw_response")
            if output.agent == "report" and isinstance(raw_report, str) and self._looks_like_report(raw_report):
                return self._ensure_important_signals(raw_report.strip(), outputs, case)
        return self._synthesized_final_report(outputs, case)

    def _synthesized_final_report(self, outputs: List[AgentEnvelope], case: CaseState) -> str:
        """Build a readable report from structured outputs when the report agent omits markdown."""
        summary = self._best_summary(outputs)
        lines = [
            f"# Case {case.case_id}: {case.title}",
            "",
            "## Executive Summary",
            summary,
            "",
            "## Important Signals",
        ]
        lines.extend(f"- {signal}" for signal in self._important_signals(outputs, case))

        if case.readiness:
            gaps = ", ".join(case.readiness.gaps) if case.readiness.gaps else "none"
            lines.extend(
                [
                    "",
                    "## Readiness",
                    f"- Score: {case.readiness.readiness_score}",
                    f"- Gate: {case.readiness.gate.value}",
                    f"- Gaps: {gaps}",
                    f"- Confidence cap: {case.readiness.confidence_cap:.2f}",
                ]
            )

        findings = self._key_findings_by_agent(outputs)
        if findings:
            lines.extend(["", "## Key Findings"])
            lines.extend(findings)

        actions = self._recommended_action_lines(outputs)
        if actions:
            lines.extend(["", "## Recommended Actions"])
            lines.extend(actions)

        verifier = next((output for output in reversed(outputs) if output.agent == "verifier"), None)
        if verifier:
            flags = ", ".join(verifier.policy_flags) if verifier.policy_flags else "none"
            lines.extend(["", "## Verification", f"- {verifier.summary}", f"- Policy flags: {flags}"])

        return "\n".join(lines)

    def _ensure_important_signals(self, report: str, outputs: List[AgentEnvelope], case: CaseState) -> str:
        """Guarantee that every final report exposes an Important Signals section."""
        if "important signals" in report.lower():
            return report
        signals = "\n".join(f"- {signal}" for signal in self._important_signals(outputs, case))
        return f"{report.rstrip()}\n\n## Important Signals\n{signals}"

    @staticmethod
    def _looks_like_report(text: str) -> bool:
        lower = text.lower()
        return "# " in text or "\n## " in text or "important signals" in lower or "executive summary" in lower

    def _best_summary(self, outputs: List[AgentEnvelope]) -> str:
        for preferred in ("verifier", "response", "triage"):
            output = next((item for item in reversed(outputs) if item.agent == preferred), None)
            if output and output.summary:
                return output.summary
        for output in reversed(outputs):
            if output.agent != "report" and output.summary:
                return output.summary
        return outputs[-1].summary if outputs else "No agent conclusion was returned."

    def _important_signals(self, outputs: List[AgentEnvelope], case: CaseState) -> List[str]:
        signals: List[str] = []
        for action in case.approvals:
            if action.status.value == "pending" and action.risk in {RiskLevel.high, RiskLevel.critical}:
                signals.append(f"{action.risk.value.upper()} approval required: {action.title}")
        for output in outputs:
            for flag in output.policy_flags:
                signals.append(f"Policy flag from {output.agent}: {flag}")
        for agent in ("response", "impact", "triage", "intel", "timeline", "verifier"):
            output = next((item for item in reversed(outputs) if item.agent == agent), None)
            if output:
                signals.extend(f"{agent}: {finding}" for finding in output.key_findings[:2])
        if case.readiness:
            signals.extend(f"Data gap: {gap}" for gap in case.readiness.gaps)
        return self._unique_lines(signals)[:8] or ["No high-priority signal was returned by the agent team."]

    def _key_findings_by_agent(self, outputs: List[AgentEnvelope]) -> List[str]:
        findings: List[str] = []
        for output in outputs:
            for finding in output.key_findings[:3]:
                findings.append(f"- {output.agent}: {finding}")
        return self._unique_lines(findings)[:18]

    def _recommended_action_lines(self, outputs: List[AgentEnvelope]) -> List[str]:
        lines: List[str] = []
        seen_actions = set()
        for output in outputs:
            for action in output.recommended_actions:
                key = action.action_id or f"{action.action_type}:{action.title}"
                if key in seen_actions:
                    continue
                seen_actions.add(key)
                approval = "requires approval" if action.requires_approval else "no approval required"
                lines.append(f"- [{action.risk.value}] {action.title} ({approval}): {action.description}")
        return lines

    @staticmethod
    def _unique_lines(values: List[str]) -> List[str]:
        seen = set()
        unique_values: List[str] = []
        for value in values:
            normalized = value.strip()
            key = normalized.lower()
            if not normalized or key in seen:
                continue
            seen.add(key)
            unique_values.append(normalized)
        return unique_values

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

    @classmethod
    def _finding_list(cls, value: Any) -> List[str]:
        """Normalize common LLM finding shapes into short finding strings."""
        if isinstance(value, str):
            parts = [part.strip(" -*\t") for part in value.splitlines()]
            if len(parts) <= 1:
                parts = [part.strip(" -*\t") for part in value.split(";")]
            return [part[:700] for part in parts if part][:10]
        if isinstance(value, dict):
            value = value.get("items") or value.get("findings") or value.get("key_findings")
        if isinstance(value, (list, tuple)):
            findings: List[str] = []
            for item in value:
                if isinstance(item, dict):
                    item = item.get("finding") or item.get("summary") or item.get("text") or item.get("title")
                text = str(item).strip()
                if text:
                    findings.append(text[:700])
            return findings[:10]
        return []

    @staticmethod
    def _format_entities(values: List[str], empty: str) -> str:
        if not values:
            return empty
        return ", ".join(values[:3])

    @staticmethod
    def _format_evidence(case: CaseState) -> str:
        if not case.evidence_refs:
            return "the in-memory case payload"
        ref = case.evidence_refs[0]
        parts = [ref.source]
        if ref.index:
            parts.append(f"index {ref.index}")
        if ref.doc_id:
            parts.append(f"document {ref.doc_id}")
        return " / ".join(parts)
