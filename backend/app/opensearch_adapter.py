from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .config import Settings
from .utils import model_to_dict, utc_now


class OpenSearchAdapter(ABC):
    @abstractmethod
    def search_events(self, query: Dict[str, Any], size: int = 50) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_document(self, index: str, doc_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def aggregate_timeline(self, case_query: Dict[str, Any]) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def search_findings_or_alerts(self, query: Dict[str, Any], size: int = 20) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def vector_search_context(self, text: str, size: int = 5) -> List[Dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def write_case_state(self, case_id: str, state: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def write_agent_trace(self, case_id: str, trace: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def write_audit_event(self, case_id: str, event: Dict[str, Any]) -> None:
        raise NotImplementedError

    @staticmethod
    def build_event_query(case_query: Dict[str, Any]) -> Dict[str, Any]:
        must: List[Dict[str, Any]] = []
        filters: List[Dict[str, Any]] = []

        text = case_query.get("text")
        if text:
            must.append(
                {
                    "multi_match": {
                        "query": text,
                        "fields": ["message^3", "rule.description", "event.action", "process.command_line"],
                        "type": "best_fields",
                    }
                }
            )

        start = case_query.get("start")
        end = case_query.get("end")
        if start or end:
            range_query: Dict[str, Any] = {}
            if start:
                range_query["gte"] = start
            if end:
                range_query["lte"] = end
            filters.append({"range": {"@timestamp": range_query}})

        for field, values in {
            "host.name": case_query.get("hosts"),
            "user.name": case_query.get("users"),
            "source.ip": case_query.get("ips"),
            "destination.ip": case_query.get("ips"),
        }.items():
            if values:
                filters.append({"terms": {field: values}})

        return {"query": {"bool": {"must": must or [{"match_all": {}}], "filter": filters}}}


class InMemoryOpenSearchAdapter(OpenSearchAdapter):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cases: Dict[str, Dict[str, Any]] = {}
        self.traces: List[Dict[str, Any]] = []
        self.audit: List[Dict[str, Any]] = []
        self.documents: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.knowledge = [
            {
                "title": "High risk login triage",
                "summary": "Correlate source IP reputation, impossible travel, MFA status, and recent account changes.",
                "tags": ["login", "account", "triage"],
            },
            {
                "title": "Host isolation playbook",
                "summary": "Host isolation is high risk and must require human approval before execution.",
                "tags": ["containment", "isolate_host", "approval"],
            },
            {
                "title": "Evidence handling",
                "summary": "Every SOC conclusion must cite source index, document ID, and the reason it supports the claim.",
                "tags": ["evidence", "audit"],
            },
        ]

    def search_events(self, query: Dict[str, Any], size: int = 50) -> List[Dict[str, Any]]:
        docs: List[Dict[str, Any]] = []
        for index_docs in self.documents.values():
            docs.extend(index_docs.values())
        return docs[:size]

    def get_document(self, index: str, doc_id: str) -> Optional[Dict[str, Any]]:
        return self.documents.get(index, {}).get(doc_id)

    def aggregate_timeline(self, case_query: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {
                "timestamp": case_query.get("timestamp") or utc_now().isoformat(),
                "event": case_query.get("event") or "alert_received",
                "source": "in-memory",
                "count": 1,
            }
        ]

    def search_findings_or_alerts(self, query: Dict[str, Any], size: int = 20) -> List[Dict[str, Any]]:
        return self.search_events(query, size=size)

    def vector_search_context(self, text: str, size: int = 5) -> List[Dict[str, Any]]:
        terms = {term.lower() for term in text.split() if len(term) > 3}
        scored: List[Dict[str, Any]] = []
        for item in self.knowledge:
            haystack = " ".join([item["title"], item["summary"], " ".join(item["tags"])]).lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append({"_score": score, "_source": item})
        return sorted(scored, key=lambda row: row["_score"], reverse=True)[:size]

    def write_case_state(self, case_id: str, state: Any) -> None:
        self.cases[case_id] = model_to_dict(state)

    def write_agent_trace(self, case_id: str, trace: Any) -> None:
        self.traces.append({"case_id": case_id, "trace": model_to_dict(trace), "written_at": utc_now().isoformat()})

    def write_audit_event(self, case_id: str, event: Dict[str, Any]) -> None:
        self.audit.append({"case_id": case_id, "event": event, "written_at": utc_now().isoformat()})


class LiveOpenSearchAdapter(OpenSearchAdapter):
    def __init__(self, settings: Settings):
        try:
            from opensearchpy import OpenSearch
        except ImportError as exc:  # pragma: no cover - requires optional dependency
            raise RuntimeError("opensearch-py is required when demo_mode=false") from exc

        if not settings.opensearch_url:
            raise RuntimeError("OPENSEARCH_URL is required when demo_mode=false")

        auth = None
        if settings.opensearch_username and settings.opensearch_password:
            auth = (settings.opensearch_username, settings.opensearch_password)
        self.settings = settings
        self.client = OpenSearch(
            hosts=[settings.opensearch_url],
            http_auth=auth,
            verify_certs=settings.opensearch_verify_certs,
            timeout=30,
            max_retries=2,
            retry_on_timeout=True,
        )

    def search_events(self, query: Dict[str, Any], size: int = 50) -> List[Dict[str, Any]]:
        body = self.build_event_query(query)
        result = self.client.search(index=self.settings.opensearch_alert_index, body=body, size=size)
        return [hit for hit in result.get("hits", {}).get("hits", [])]

    def get_document(self, index: str, doc_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self.client.get(index=index, id=doc_id)
        except Exception:
            return None

    def aggregate_timeline(self, case_query: Dict[str, Any]) -> List[Dict[str, Any]]:
        body = self.build_event_query(case_query)
        body["aggs"] = {
            "events_over_time": {
                "date_histogram": {"field": "@timestamp", "fixed_interval": "5m"},
                "aggs": {"top_event": {"top_hits": {"size": 1}}},
            }
        }
        result = self.client.search(index=self.settings.opensearch_alert_index, body=body, size=0)
        buckets = result.get("aggregations", {}).get("events_over_time", {}).get("buckets", [])
        return [
            {
                "timestamp": bucket.get("key_as_string"),
                "count": bucket.get("doc_count", 0),
                "sample": bucket.get("top_event", {}).get("hits", {}).get("hits", [])[:1],
            }
            for bucket in buckets
        ]

    def search_findings_or_alerts(self, query: Dict[str, Any], size: int = 20) -> List[Dict[str, Any]]:
        body = self.build_event_query(query)
        try:
            result = self.client.search(index=self.settings.opensearch_findings_index, body=body, size=size)
            hits = result.get("hits", {}).get("hits", [])
            if hits:
                return hits
        except Exception:
            pass
        return self.search_events(query, size=size)

    def vector_search_context(self, text: str, size: int = 5) -> List[Dict[str, Any]]:
        body = {
            "query": {
                "multi_match": {
                    "query": text,
                    "fields": ["title^3", "summary", "content", "tags"],
                    "type": "best_fields",
                }
            }
        }
        try:
            result = self.client.search(index=self.settings.knowledge_index, body=body, size=size)
            return [hit for hit in result.get("hits", {}).get("hits", [])]
        except Exception:
            return []

    def write_case_state(self, case_id: str, state: Any) -> None:
        self.client.index(index=self.settings.cases_index, id=case_id, body=model_to_dict(state), refresh=True)

    def write_agent_trace(self, case_id: str, trace: Any) -> None:
        body = {"case_id": case_id, "trace": model_to_dict(trace), "written_at": utc_now().isoformat()}
        self.client.index(index=self.settings.traces_index, body=body, refresh=False)

    def write_audit_event(self, case_id: str, event: Dict[str, Any]) -> None:
        body = {"case_id": case_id, "event": event, "written_at": utc_now().isoformat()}
        self.client.index(index=self.settings.audit_index, body=body, refresh=False)


def build_opensearch_adapter(settings: Settings) -> OpenSearchAdapter:
    if settings.demo_mode or not settings.opensearch_url:
        return InMemoryOpenSearchAdapter(settings)
    return LiveOpenSearchAdapter(settings)

