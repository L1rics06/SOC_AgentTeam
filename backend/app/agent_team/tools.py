"""LLM Tool Registry：把内部 Python 能力暴露成 Chat Completions 工具。"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List

from ..models import CaseState
from ..opensearch_adapter import OpenSearchAdapter
from ..utils import redact_sensitive
from .mailbox import TeamMailbox
from .skills import SkillRegistry


class ToolRegistry:
    """为某个 Agent 提供可用工具定义、执行入口和调用摘要。"""

    def __init__(self, agent_name: str, case: CaseState, adapter: OpenSearchAdapter, mailbox: TeamMailbox, skills: SkillRegistry):
        self.agent_name = agent_name
        self.case = case
        self.adapter = adapter
        self.mailbox = mailbox
        self.skills = skills
        self.calls: List[Dict[str, Any]] = []

    def definitions(self, allowed: List[str]) -> List[Dict[str, Any]]:
        """根据 AgentSpec.allowed_tools 过滤出本轮可暴露给 LLM 的工具。"""
        all_definitions = self._definitions()
        return [all_definitions[name] for name in allowed if name in all_definitions]

    def execute(self, name: str, arguments: str) -> Dict[str, Any]:
        """执行一次工具调用，统一处理 JSON 参数、异常和结果脱敏。"""
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        handlers: Dict[str, Callable[..., Any]] = {
            "list_skills": self._list_skills,
            "load_skill": self._load_skill,
            "read_mailbox": self._read_mailbox,
            "send_message": self._send_message,
            "opensearch_search_events": self._opensearch_search_events,
            "opensearch_get_document": self._opensearch_get_document,
            "opensearch_aggregate_timeline": self._opensearch_aggregate_timeline,
            "opensearch_search_findings": self._opensearch_search_findings,
            "opensearch_vector_search": self._opensearch_vector_search,
        }
        handler = handlers.get(name)
        if not handler:
            result = {"error": f"Unknown tool {name}"}
        else:
            try:
                result = handler(**args)
            except Exception as exc:
                result = {"error": str(exc)}
        self.calls.append({"tool": name, "arguments": args, "result_preview": self._preview(result)})
        return redact_sensitive(result)

    def _list_skills(self) -> Dict[str, Any]:
        return {"skills": self.skills.list()}

    def _load_skill(self, name: str) -> Dict[str, Any]:
        return {"name": name, "content": self.skills.load(name)}

    def _read_mailbox(self) -> Dict[str, Any]:
        return {"messages": self.mailbox.read(self.agent_name)}

    def _send_message(self, to_agent: str, message_type: str, content: str) -> Dict[str, Any]:
        return self.mailbox.send(self.agent_name, to_agent, message_type, content)

    def _opensearch_search_events(
        self,
        query_text: str = "",
        hosts: List[str] | None = None,
        users: List[str] | None = None,
        ips: List[str] | None = None,
        start: str | None = None,
        end: str | None = None,
        size: int = 20,
    ) -> Dict[str, Any]:
        """按文本、实体和时间范围搜索事件日志。"""
        query = {
            "text": query_text,
            "hosts": hosts or [],
            "users": users or [],
            "ips": ips or [],
            "start": start,
            "end": end,
        }
        return {"query": query, "hits": self._compact(self.adapter.search_events(query, size=min(size, 50)))}

    def _opensearch_get_document(self, index: str, doc_id: str) -> Dict[str, Any]:
        return {"document": self.adapter.get_document(index=index, doc_id=doc_id)}

    def _opensearch_aggregate_timeline(self, query: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return {"timeline": self.adapter.aggregate_timeline(query or {})}

    def _opensearch_search_findings(self, query_text: str = "", size: int = 10) -> Dict[str, Any]:
        return {"hits": self._compact(self.adapter.search_findings_or_alerts({"text": query_text}, size=min(size, 20)))}

    def _opensearch_vector_search(self, text: str, size: int = 5) -> Dict[str, Any]:
        return {"hits": self._compact(self.adapter.vector_search_context(text, size=min(size, 10)))}

    @staticmethod
    def _compact(items: Any) -> Any:
        """压缩 OpenSearch 命中结果，避免把过大的原始响应塞回 LLM 上下文。"""
        if not isinstance(items, list):
            return items
        compacted = []
        for item in items[:10]:
            if not isinstance(item, dict):
                compacted.append(item)
                continue
            compacted.append(
                {
                    "_index": item.get("_index"),
                    "_id": item.get("_id"),
                    "_score": item.get("_score"),
                    "_source": item.get("_source", item),
                }
            )
        return compacted

    @staticmethod
    def _preview(value: Any) -> Any:
        """生成工具调用记录中的短预览。"""
        text = json.dumps(value, ensure_ascii=False, default=str)
        return text[:500]

    @staticmethod
    def _definitions() -> Dict[str, Dict[str, Any]]:
        """返回 Chat Completions tools 所需的 JSON Schema 定义。"""
        string_array = {"type": "array", "items": {"type": "string"}}
        return {
            "list_skills": {
                "type": "function",
                "function": {
                    "name": "list_skills",
                    "description": "List SOC investigation skills available to this agent.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            "load_skill": {
                "type": "function",
                "function": {
                    "name": "load_skill",
                    "description": "Load a named SOC skill document before using it.",
                    "parameters": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                },
            },
            "read_mailbox": {
                "type": "function",
                "function": {
                    "name": "read_mailbox",
                    "description": "Read messages addressed to this agent or broadcast to all agents.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            "send_message": {
                "type": "function",
                "function": {
                    "name": "send_message",
                    "description": "Send a structured message to another SOC agent.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to_agent": {"type": "string"},
                            "message_type": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["to_agent", "message_type", "content"],
                    },
                },
            },
            "opensearch_search_events": {
                "type": "function",
                "function": {
                    "name": "opensearch_search_events",
                    "description": "Search event logs in OpenSearch by text, entities, and optional time range.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query_text": {"type": "string"},
                            "hosts": string_array,
                            "users": string_array,
                            "ips": string_array,
                            "start": {"type": "string"},
                            "end": {"type": "string"},
                            "size": {"type": "integer"},
                        },
                    },
                },
            },
            "opensearch_get_document": {
                "type": "function",
                "function": {
                    "name": "opensearch_get_document",
                    "description": "Fetch a specific OpenSearch document by index and id.",
                    "parameters": {
                        "type": "object",
                        "properties": {"index": {"type": "string"}, "doc_id": {"type": "string"}},
                        "required": ["index", "doc_id"],
                    },
                },
            },
            "opensearch_aggregate_timeline": {
                "type": "function",
                "function": {
                    "name": "opensearch_aggregate_timeline",
                    "description": "Build a timeline aggregation for the case query.",
                    "parameters": {"type": "object", "properties": {"query": {"type": "object"}}},
                },
            },
            "opensearch_search_findings": {
                "type": "function",
                "function": {
                    "name": "opensearch_search_findings",
                    "description": "Search OpenSearch Security Analytics findings or alerts.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query_text": {"type": "string"}, "size": {"type": "integer"}},
                    },
                },
            },
            "opensearch_vector_search": {
                "type": "function",
                "function": {
                    "name": "opensearch_vector_search",
                    "description": "Search internal SOC knowledge/playbook context.",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}, "size": {"type": "integer"}},
                        "required": ["text"],
                    },
                },
            },
        }
