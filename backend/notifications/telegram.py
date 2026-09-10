from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


def call_telegram_api(method: str, payload: dict) -> dict | None:
    token = (settings.TELEGRAM_BOT_TOKEN or "").strip()
    if not token:
        logger.warning("Skipping Telegram %s: bot token missing.", method)
        return None

    request = urllib.request.Request(
        f"{TELEGRAM_API}/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not body.get("ok"):
            logger.warning("Telegram %s failed: %s", method, body)
            return None
        return body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"ok": False, "description": raw}
        logger.warning("Telegram %s failed: %s", method, body)
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        logger.exception("Telegram %s error", method)
        return None


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
    return call_telegram_api("sendMessage", payload) is not None


def send_telegram_message_result(
    chat_id: int | str,
    text: str,
    *,
    reply_markup: dict | None = None,
) -> dict | None:
    if not chat_id:
        logger.warning("Skipping Telegram send: chat id missing.")
        return None
    payload: dict[str, object] = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    body = call_telegram_api("sendMessage", payload)
    if body is None:
        return None
    result = body.get("result")
    return result if isinstance(result, dict) else None


def edit_telegram_message(
    chat_id: int | str,
    message_id: int,
    text: str,
    *,
    reply_markup: dict | None = None,
) -> bool:
    if not chat_id or not message_id:
        return False
    payload: dict[str, object] = {
        "chat_id": chat_id,
        "message_id": int(message_id),
        "text": text,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return call_telegram_api("editMessageText", payload) is not None


def open_koolbar_markup(startapp: str = "matches") -> dict:
    return {"inline_keyboard": [[_open_koolbar_button(startapp)]]}


def connected_markup(other_user, draft: str = "") -> dict:
    from matching.contact import telegram_dm_contact

    rows = [[_open_koolbar_button("matches")]]
    contact = telegram_dm_contact(other_user, draft)
    if contact["https_url"]:
        rows.append([{"text": "Message on Telegram", "url": contact["https_url"]}])
    return {"inline_keyboard": rows}


def mini_app_https_url(startapp: str = "") -> str | None:
    public_url = (getattr(settings, "TELEGRAM_MINI_APP_URL", "") or "").rstrip("/")
    if public_url.startswith("https://"):
        query = f"?startapp={startapp}" if startapp else ""
        return f"{public_url}/app{query}"
    return None


def mini_app_link(startapp: str = "", *, mode: str = "") -> str:
    username = _bot_username()
    if not username:
        return "https://t.me"
    params: list[str] = []
    if startapp:
        params.append(f"startapp={startapp}")
    if mode:
        params.append(f"mode={mode}")
    query = f"?{'&'.join(params)}" if params else ""
    return f"https://t.me/{username}/{_app_short_name()}{query}"


def mini_app_bot_link() -> str:
    username = _bot_username()
    return f"https://t.me/{username}" if username else "https://t.me"


def mini_app_tg_link(startapp: str = "") -> str:
    username = _bot_username()
    if not username:
        return "tg://resolve"
    query = f"&startapp={startapp}" if startapp else ""
    return f"tg://resolve?domain={username}&appname={_app_short_name()}{query}"


def _bot_username() -> str:
    return (getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "").lstrip("@")


def _app_short_name() -> str:
    return getattr(settings, "TELEGRAM_MINI_APP_SHORT_NAME", "app") or "app"


def _open_koolbar_button(startapp: str = "matches", text: str = "Open Koolbar") -> dict:
    https_url = mini_app_https_url(startapp)
    if https_url:
        return {"text": text, "web_app": {"url": https_url}}
    return {"text": text, "url": mini_app_link(startapp)}
