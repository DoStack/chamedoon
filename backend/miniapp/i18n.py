from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

MESSAGES_DIR = Path(__file__).resolve().parent / "messages"
LOCALE_COOKIE = "koolbar_locale"


@lru_cache
def _load(locale: str) -> dict:
    path = MESSAGES_DIR / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def locale_from_request(request) -> str:
    cookie = request.COOKIES.get(LOCALE_COOKIE, "")
    if cookie in {"en", "fa"}:
        return cookie
    header = (request.META.get("HTTP_ACCEPT_LANGUAGE") or "").lower()
    if header.startswith("fa"):
        return "fa"
    return "en"


def messages_for(locale: str) -> dict:
    return _load("fa" if locale == "fa" else "en")


def t(messages: dict, path: str, **vars) -> str:
    current: object = messages
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return path
        current = current[part]
    text = str(current)
    for key, value in vars.items():
        text = text.replace("{" + key + "}", str(value))
    return text
