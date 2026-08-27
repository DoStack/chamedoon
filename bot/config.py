from __future__ import annotations

import os


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
BOT_USERNAME = env("TELEGRAM_BOT_USERNAME").lstrip("@")
BOT_SERVICE_SECRET = env("BOT_SERVICE_SECRET")
API_BASE_URL = env("API_BASE_URL", "http://localhost:8000").rstrip("/")
MINI_APP_URL = env("TELEGRAM_MINI_APP_URL").rstrip("/")
MINI_APP_SHORT_NAME = env("TELEGRAM_MINI_APP_SHORT_NAME", "app") or "app"


def mini_app_https_url(startapp: str = "") -> str | None:
    if MINI_APP_URL.startswith("https://"):
        query = f"?startapp={startapp}" if startapp else ""
        return f"{MINI_APP_URL}/app{query}"
    return None


def telegram_app_link(startapp: str = "") -> str:
    if BOT_USERNAME:
        query = f"?startapp={startapp}" if startapp else ""
        return f"https://t.me/{BOT_USERNAME}/{MINI_APP_SHORT_NAME}{query}"
    return "https://t.me"


def telegram_bot_link() -> str:
    return f"https://t.me/{BOT_USERNAME}" if BOT_USERNAME else "https://t.me"
