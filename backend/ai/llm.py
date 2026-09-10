from __future__ import annotations

import logging

from django.conf import settings

from ai.openai import chat as openai_chat
from ai.openai import openai_enabled, openai_model
from ai.openrouter import ChatResult
from ai.openrouter import chat as openrouter_chat
from ai.openrouter import openrouter_enabled, openrouter_model

logger = logging.getLogger(__name__)

DEFAULT_FREE_MODELS = (
    "openrouter/free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
)


def llm_enabled() -> bool:
    return openrouter_enabled() or openai_enabled()


def llm_status() -> dict:
    free = list(free_models()) if openrouter_enabled() else []
    paid = openai_model() if openai_enabled() else ""
    parts = [*free]
    if paid:
        parts.append(paid)
    return {
        "openrouter": openrouter_enabled(),
        "openai": openai_enabled(),
        "enabled": llm_enabled(),
        "free_models": free,
        "paid_model": paid,
        "model": " → ".join(parts) if parts else "disabled",
    }


def free_models() -> tuple[str, ...]:
    names: list[str] = []
    primary = openrouter_model()
    if primary:
        names.append(primary)
    raw = (getattr(settings, "OPENROUTER_MODEL_FALLBACKS", "") or "").strip()
    extras = [item.strip() for item in raw.split(",") if item.strip()]
    for name in extras or DEFAULT_FREE_MODELS:
        if name not in names:
            names.append(name)
    for name in DEFAULT_FREE_MODELS:
        if len(names) >= 3:
            break
        if name not in names:
            names.append(name)
    return tuple(names[:3])


def complete(
    prompt: str,
    *,
    system: str = "",
    model: str | None = None,
    temperature: float = 0,
    max_tokens: int | None = None,
    allow_paid: bool = True,
) -> ChatResult:
    messages: list[dict[str, str]] = []
    if system.strip():
        messages.append({"role": "system", "content": system.strip()})
    messages.append({"role": "user", "content": prompt})
    return chat(
        messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        allow_paid=allow_paid,
    )


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0,
    max_tokens: int | None = None,
    allow_paid: bool = True,
) -> ChatResult:
    errors: list[str] = []
    chain = [model.strip()] if (model or "").strip() else list(free_models())
    if openrouter_enabled():
        for name in chain:
            logger.info("Trying OpenRouter model %s", name)
            result = openrouter_chat(
                messages,
                model=name,
                temperature=temperature,
                max_tokens=max_tokens,
                include_fallbacks=False,
            )
            if result.ok and result.text:
                logger.info("OpenRouter model %s returned a response.", result.model or name)
                return result
            errors.append(f"{name}: {result.error or 'empty response'}")
            logger.warning("OpenRouter model %s failed: %s", name, result.error or "empty response")
    elif chain and not openai_enabled():
        errors.append("OpenRouter is not configured.")

    if allow_paid and openai_enabled():
        paid = openai_model()
        logger.info("Trying paid OpenAI model %s", paid)
        result = openai_chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens if max_tokens is not None else _paid_max_tokens(),
        )
        if result.ok and result.text:
            logger.info("OpenAI model %s returned a response.", result.model or paid)
            return result
        errors.append(f"{paid}: {result.error or 'empty response'}")
        logger.warning("OpenAI model %s failed: %s", paid, result.error or "empty response")

    if not errors:
        return ChatResult(ok=False, error="No LLM is configured.")
    return ChatResult(ok=False, error="; ".join(errors))


def _paid_max_tokens() -> int:
    try:
        return max(0, int(getattr(settings, "OPENAI_MAX_TOKENS", 400) or 0))
    except (TypeError, ValueError):
        return 400
