from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

from ai.openrouter import ChatResult

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"


def openai_enabled() -> bool:
    return bool(_api_key())


def openai_model() -> str:
    return (getattr(settings, "OPENAI_MODEL", "") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


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
        return ChatResult(ok=False, error="OpenAI is not configured.")
    chosen = (model or openai_model()).strip() or DEFAULT_MODEL
    payload: dict[str, object] = {
        "model": chosen,
        "messages": messages,
    }
    if not _reasoning_model(chosen):
        payload["temperature"] = temperature
    tokens = max_tokens if max_tokens is not None else _max_tokens()
    if tokens:
        if _uses_completion_tokens(chosen):
            payload["max_completion_tokens"] = tokens
        else:
            payload["max_tokens"] = tokens

    request = urllib.request.Request(
        f"{_base_url()}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
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
        logger.warning("OpenAI chat failed: %s", message)
        return ChatResult(ok=False, model=chosen, error=message, raw=body if isinstance(body, dict) else {})
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.warning("OpenAI chat error: %s", exc)
        return ChatResult(ok=False, model=chosen, error=str(exc))

    if not isinstance(body, dict):
        return ChatResult(ok=False, model=chosen, error="Invalid OpenAI response.")
    err = _error_message(body)
    if err:
        logger.warning("OpenAI chat failed: %s", err)
        return ChatResult(ok=False, model=chosen, error=err, raw=body)
    text = _message_text(body)
    used = str((body.get("model") or chosen) or "")
    return ChatResult(ok=bool(text), text=text, model=used, error="" if text else "Empty model response.", raw=body)


def _api_key() -> str:
    return (getattr(settings, "OPENAI_API_KEY", "") or "").strip()


def _base_url() -> str:
    return (getattr(settings, "OPENAI_BASE_URL", "") or DEFAULT_BASE_URL).rstrip("/") or DEFAULT_BASE_URL


def _timeout() -> int:
    try:
        return max(5, int(getattr(settings, "OPENAI_TIMEOUT_SECONDS", 20) or 20))
    except (TypeError, ValueError):
        return 20


def _max_tokens() -> int:
    try:
        return max(0, int(getattr(settings, "OPENAI_MAX_TOKENS", 400) or 0))
    except (TypeError, ValueError):
        return 400


def _uses_completion_tokens(model: str) -> bool:
    name = model.lower()
    return name.startswith("gpt-5") or name.startswith("o1") or name.startswith("o3") or name.startswith("o4")


def _reasoning_model(model: str) -> bool:
    name = model.lower()
    return name.startswith("o1") or name.startswith("o3") or name.startswith("o4")


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
