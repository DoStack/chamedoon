from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


def send_telegram_message(
    chat_id: int,
    text: str,
    *,
    reply_markup: dict | None = None,
) -> bool:
    token = (settings.TELEGRAM_BOT_TOKEN or "").strip()
    if not token or not chat_id:
        logger.warning("Skipping Telegram send: bot token or chat id missing.")
        return False

    payload: dict[str, object] = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    request = urllib.request.Request(
        f"{TELEGRAM_API}/bot{token}/sendMessage",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not body.get("ok"):
            logger.warning("Telegram sendMessage failed: %s", body)
            return False
        return True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        logger.exception("Telegram sendMessage error for chat_id=%s", chat_id)
        return False


def open_koolbar_markup(startapp: str = "matches") -> dict:
    url = mini_app_link(startapp)
    return {"inline_keyboard": [[{"text": "Open Koolbar", "url": url}]]}


def connected_markup(other_user) -> dict:
    from matching.contact import telegram_dm_contact

    rows = [[{"text": "Open Koolbar", "url": mini_app_link("matches")}]]
    contact = telegram_dm_contact(other_user)
    if contact["https_url"]:
        rows.append([{"text": "Message on Telegram", "url": contact["https_url"]}])
    return {"inline_keyboard": rows}


def mini_app_link(startapp: str = "") -> str:
    public_url = (getattr(settings, "TELEGRAM_MINI_APP_URL", "") or "").rstrip("/")
    if public_url.startswith("https://"):
        suffix = f"?startapp={startapp}" if startapp else ""
        return f"{public_url}/app{suffix}"
    username = (settings.TELEGRAM_BOT_USERNAME or "").lstrip("@")
    short_name = getattr(settings, "TELEGRAM_MINI_APP_SHORT_NAME", "app") or "app"
    if username:
        query = f"?startapp={startapp}" if startapp else ""
        return f"https://t.me/{username}/{short_name}{query}"
    return "https://t.me"
