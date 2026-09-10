from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

MESSAGES_DIR = Path(__file__).resolve().parent / "messages"
LOCALE_COOKIE = "koolbar_locale"
SESSION_LOCALE_KEY = "koolbar_locale"


@lru_cache
def _load(locale: str) -> dict:
    path = MESSAGES_DIR / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def locale_from_request(request) -> str:
    session_locale = ""
    try:
        session_locale = request.session.get(SESSION_LOCALE_KEY, "") or ""
    except Exception:
        pass
    if session_locale in {"en", "fa"}:
        return session_locale
    cookie = request.COOKIES.get(LOCALE_COOKIE, "")
    if cookie in {"en", "fa"}:
        return cookie
    header = (request.META.get("HTTP_ACCEPT_LANGUAGE") or "").lower()
    if header.startswith("fa"):
        return "fa"
    return "en"


def remember_locale(request, locale: str) -> None:
    if locale not in {"en", "fa"}:
        return
    request.session[SESSION_LOCALE_KEY] = locale


def safe_next_path(value: str | None) -> str:
    path = (value or "").strip()
    if not path.startswith("/") or path.startswith("//"):
        return "/app/"
    parsed = urlparse(path)
    if parsed.scheme or parsed.netloc:
        return "/app/"
    return path


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
