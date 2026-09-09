from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "openrouter/free"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TITLE = "Koolbar"


@dataclass
class ChatResult:
    ok: bool
    text: str = ""
    model: str = ""
    error: str = ""
    raw: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok and bool(self.text)


def openrouter_enabled() -> bool:
    return bool(_api_key())


def openrouter_model() -> str:
    return (getattr(settings, "OPENROUTER_MODEL", "") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def complete(
    prompt: str,
    *,
    system: str = "",
    model: str | None = None,
    temperature: float = 0,
    max_tokens: int | None = None,
) -> ChatResult:
    messages: list[dict[str, str]] = []
    if system.strip():
        messages.append({"role": "system", "content": system.strip()})
    messages.append({"role": "user", "content": prompt})
    return chat(messages, model=model, temperature=temperature, max_tokens=max_tokens)


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0,
    max_tokens: int | None = None,
) -> ChatResult:
    key = _api_key()
    if not key:
        return ChatResult(ok=False, error="OpenRouter is not configured.")
    chosen = (model or openrouter_model()).strip() or DEFAULT_MODEL
    payload: dict[str, object] = {
        "model": chosen,
        "messages": messages,
        "temperature": temperature,
    }
    fallbacks = _fallback_models(chosen)
    if fallbacks:
        payload["models"] = fallbacks
    tokens = max_tokens if max_tokens is not None else _max_tokens()
    if tokens:
        payload["max_tokens"] = tokens

    request = urllib.request.Request(
        f"{_base_url()}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": _referer(),
            "X-Title": _app_title(),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_timeout()) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"error": {"message": raw or f"HTTP {exc.code}"}}
        message = _error_message(body) or f"HTTP {exc.code}"
        logger.warning("OpenRouter chat failed: %s", message)
        return ChatResult(ok=False, model=chosen, error=message, raw=body if isinstance(body, dict) else {})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.warning("OpenRouter chat error: %s", exc)
        return ChatResult(ok=False, model=chosen, error=str(exc))

    if not isinstance(body, dict):
        return ChatResult(ok=False, model=chosen, error="Invalid OpenRouter response.")
    err = _error_message(body)
    if err:
        logger.warning("OpenRouter chat failed: %s", err)
        return ChatResult(ok=False, model=chosen, error=err, raw=body)
    text = _message_text(body)
    used = str((body.get("model") or chosen) or "")
    return ChatResult(ok=bool(text), text=text, model=used, error="" if text else "Empty model response.", raw=body)


def _api_key() -> str:
    return (getattr(settings, "OPENROUTER_API_KEY", "") or "").strip()


def _base_url() -> str:
    return (getattr(settings, "OPENROUTER_BASE_URL", "") or DEFAULT_BASE_URL).rstrip("/") or DEFAULT_BASE_URL


def _timeout() -> int:
    try:
        return max(5, int(getattr(settings, "OPENROUTER_TIMEOUT_SECONDS", 30) or 30))
    except (TypeError, ValueError):
        return 30


def _max_tokens() -> int:
    try:
        return max(0, int(getattr(settings, "OPENROUTER_MAX_TOKENS", 800) or 0))
    except (TypeError, ValueError):
        return 800


def _app_title() -> str:
    return (getattr(settings, "OPENROUTER_APP_TITLE", "") or DEFAULT_TITLE).strip() or DEFAULT_TITLE


def _referer() -> str:
    url = (getattr(settings, "OPENROUTER_HTTP_REFERER", "") or "").strip()
    if url:
        return url
    mini = (getattr(settings, "TELEGRAM_MINI_APP_URL", "") or "").strip()
    return mini or "https://koolbar-jet.vercel.app"


def _fallback_models(primary: str) -> list[str]:
    raw = (getattr(settings, "OPENROUTER_MODEL_FALLBACKS", "") or "").strip()
    names = [item.strip() for item in raw.split(",") if item.strip() and item.strip() != primary]
    if not names:
        return []
    return [primary, *names]


def _error_message(body: dict) -> str:
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "").strip()
    if isinstance(error, str):
        return error.strip()
    return ""


def _message_text(body: dict) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first, dict) else None
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [str(part.get("text") or "").strip() for part in content if isinstance(part, dict)]
            return "\n".join(part for part in parts if part).strip()
    text = first.get("text") if isinstance(first, dict) else None
    return str(text or "").strip()
