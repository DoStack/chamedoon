"""The model call for housing reviews.

The shared chain in `ai.llm` tries free OpenRouter models first. For housing that was
slow (up to 15 s per model before falling through) and the free models often
returned broken JSON. Housing reviews therefore go straight to one chosen OpenAI
model in JSON mode, with little reasoning, and fall back to the shared chain only
when no OpenAI key is set. This module does HTTP only, no database work, so it can
run in worker threads.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

from ai.llm import complete as shared_complete
from ai.openrouter import ChatResult

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5-mini"
TIMEOUT_SECONDS = 60
MAX_COMPLETION_TOKENS = 3000


def _openai_key() -> str:
    return (getattr(settings, "OPENAI_API_KEY", "") or "").strip()


def _openai_base() -> str:
    return ((getattr(settings, "OPENAI_BASE_URL", "") or "https://api.openai.com/v1").rstrip("/")) or "https://api.openai.com/v1"


def review_completion(prompt: str, *, system: str, model: str) -> ChatResult:
    key = _openai_key()
    chosen = (model or DEFAULT_MODEL).strip()
    if not key:
        return shared_complete(prompt, system=system, temperature=0, max_tokens=1200)
    payload: dict[str, object] = {
        "model": chosen,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }
    if chosen.startswith(("gpt-5", "o1", "o3", "o4")):
        payload["max_completion_tokens"] = MAX_COMPLETION_TOKENS
        payload["reasoning_effort"] = "minimal" if chosen.startswith("gpt-5") else "low"
    else:
        payload["max_tokens"] = 1500
        payload["temperature"] = 0
    request = urllib.request.Request(
        f"{_openai_base()}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            message = (json.loads(raw).get("error") or {}).get("message") or f"HTTP {exc.code}"
        except (ValueError, AttributeError):
            message = raw[:200] or f"HTTP {exc.code}"
        logger.warning("Housing review call failed: %s", message)
        return ChatResult(ok=False, model=chosen, error=str(message)[:250])
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return ChatResult(ok=False, model=chosen, error=f"{type(exc).__name__}: {exc}"[:250])
    choices = body.get("choices") or []
    text = ""
    if choices and isinstance(choices[0], dict):
        text = str(((choices[0].get("message") or {}).get("content")) or "").strip()
    if not text:
        reason = choices[0].get("finish_reason") if choices and isinstance(choices[0], dict) else ""
        return ChatResult(ok=False, model=str(body.get("model") or chosen), error=f"Empty model response ({reason}).")
    return ChatResult(ok=True, text=text, model=str(body.get("model") or chosen), raw=body)
