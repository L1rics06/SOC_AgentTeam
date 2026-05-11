"""通用工具函数：时间、ID、脱敏、实体/IOC 提取和兼容性转换。"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
}


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间。"""
    return datetime.now(timezone.utc)


def short_id(prefix: str, seed: Optional[str] = None) -> str:
    """基于种子生成稳定短 ID；无种子时使用当前时间。"""
    raw = seed or utc_now().isoformat()
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10].upper()
    return f"{prefix}-{digest}"


def model_to_dict(model: Any) -> Dict[str, Any]:
    """兼容 Pydantic v1/v2，把模型转换为普通 dict。"""
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    if hasattr(model, "dict"):
        return model.dict()
    return dict(model)


def nested_get(data: Dict[str, Any], candidates: Iterable[str], default: Any = None) -> Any:
    """按候选点号路径依次取值，返回第一个非空结果。"""
    for path in candidates:
        current: Any = data
        ok = True
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                ok = False
                break
        if ok and current not in (None, ""):
            return current
    return default


def flatten(data: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """把嵌套 dict 展平成点号路径，方便实体提取。"""
    out: Dict[str, Any] = {}
    for key, value in data.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(flatten(value, dotted))
        else:
            out[dotted] = value
    return out


def redact_sensitive(value: Any) -> Any:
    """递归脱敏常见密钥、Token、Cookie 和密码字段。"""
    if isinstance(value, dict):
        clean: Dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in SENSITIVE_KEYS:
                clean[key] = "***REDACTED***"
            else:
                clean[key] = redact_sensitive(item)
        return clean
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def severity_to_score(raw: Any) -> int:
    """把不同来源的严重级别表示统一映射到 0-100 分。"""
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return max(0, min(100, int(raw)))
    text = str(raw).lower()
    if text in {"critical", "crit", "p1"}:
        return 95
    if text in {"high", "p2"}:
        return 80
    if text in {"medium", "med", "p3"}:
        return 55
    if text in {"low", "info", "informational", "p4"}:
        return 25
    match = re.search(r"\d+", text)
    return max(0, min(100, int(match.group(0)))) if match else 0


def extract_entities(alert: Dict[str, Any]) -> Dict[str, List[str]]:
    """从告警中提取主机、用户、IP 和域名实体。"""
    flat = flatten(alert)
    entities: Dict[str, List[str]] = {"hosts": [], "users": [], "ips": [], "domains": []}
    for key, value in flat.items():
        if value is None:
            continue
        text = str(value)
        lowered = key.lower()
        if any(token in lowered for token in ["host", "hostname", "agent.name"]):
            entities["hosts"].append(text)
        if any(token in lowered for token in ["user", "account", "principal"]):
            entities["users"].append(text)
        for ip in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
            try:
                ipaddress.ip_address(ip)
                entities["ips"].append(ip)
            except ValueError:
                pass
        for domain in re.findall(r"\b[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+\b", text):
            if not re.match(r"^\d+\.\d+\.\d+\.\d+$", domain):
                entities["domains"].append(domain.lower())
    return {key: sorted(set(values)) for key, values in entities.items()}


def extract_iocs(alert: Dict[str, Any]) -> Dict[str, List[str]]:
    """提取常见 IOC：IP、域名和 MD5/SHA1/SHA256 哈希。"""
    text = str(alert)
    entities = extract_entities(alert)
    hashes = re.findall(r"\b[a-fA-F0-9]{32}\b|\b[a-fA-F0-9]{40}\b|\b[a-fA-F0-9]{64}\b", text)
    return {
        "ips": entities["ips"],
        "domains": entities["domains"],
        "hashes": sorted(set(hash_.lower() for hash_ in hashes)),
    }


def first_non_empty(values: Iterable[Any], default: Any = None) -> Any:
    """返回序列中的第一个非空值。"""
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return default
