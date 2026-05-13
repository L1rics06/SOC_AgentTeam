from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable, Optional

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.llm import build_llm_client  # noqa: E402


def _redact(text: str, secrets: Iterable[Optional[str]]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def main() -> int:
    settings = get_settings()
    llm = build_llm_client(settings)
    status = llm.status()
    if not llm.enabled:
        print(json.dumps({"ok": False, "skipped": True, "reason": "LLM is not configured", "status": status}, ensure_ascii=False))
        return 2

    try:
        response = llm.create_chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": "You are a JSON smoke test. Return exactly {\"status\":\"ok\",\"adapter\":\"siliconflow\"}.",
                },
                {
                    "role": "user",
                    "content": "Return exactly {\"status\":\"ok\",\"adapter\":\"siliconflow\"}. No extra keys.",
                },
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        print(
            json.dumps(
                {
                    "ok": payload.get("status") == "ok",
                    "provider": status["provider"],
                    "model": status["model"],
                    "payload_keys": sorted(payload.keys()),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        error = _redact(str(exc), [settings.openai_api_key, settings.siliconflow_api_key])
        print(json.dumps({"ok": False, "provider": status["provider"], "model": status["model"], "error": error[:800]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
