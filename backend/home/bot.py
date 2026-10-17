"""A small Telegram Bot API client for the housing bot (@chamedoon_home_bot).

Separate from the courier bot on purpose: its own token, set in Admin → Home settings.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT_SECONDS = 20


class BotError(RuntimeError):
    def __init__(self, description: str, code: int = 0, retry_after: int = 0) -> None:
        super().__init__(description)
        self.description = description
        self.code = code
        self.retry_after = retry_after


def bot_token() -> str:
    from home import conf

    return conf.bot_token()


def call(method: str, payload: dict | None = None, *, token: str | None = None) -> dict | list:
    """POST one Bot API method as JSON and return its `result`. Raises BotError on any failure."""
    token = token or bot_token()
    if not token:
        raise BotError("The housing bot token is not set in Home settings.")
    request = urllib.request.Request(
        API.format(token=token, method=method),
        data=json.dumps(payload or {}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            raise BotError(f"HTTP {exc.code}", code=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise BotError(f"Telegram unreachable: {exc}") from exc
    if not isinstance(body, dict) or not body.get("ok"):
        parameters = (body.get("parameters") or {}) if isinstance(body, dict) else {}
        raise BotError(
            str((body or {}).get("description") or "Telegram refused the request"),
            code=int((body or {}).get("error_code") or 0),
            retry_after=int(parameters.get("retry_after") or 0),
        )
    return body.get("result")


def _button(button: dict | None) -> dict:
    return {"reply_markup": {"inline_keyboard": [[button]]}} if button else {}


def send_message(chat, text: str, button: dict | None = None, *, reply_to: int | None = None) -> list[int]:
    payload = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True, **_button(button)}
    if reply_to:
        payload["reply_to_message_id"] = int(reply_to)
        payload["allow_sending_without_reply"] = True
    result = call("sendMessage", payload)
    return [int(result["message_id"])]


def send_photo(chat, photo_url: str, caption: str, button: dict | None = None) -> list[int]:
    payload = {"chat_id": chat, "photo": photo_url, "caption": caption, "parse_mode": "HTML", **_button(button)}
    result = call("sendPhoto", payload)
    return [int(result["message_id"])]


def send_album(chat, photo_urls: list[str], caption: str) -> list[int]:
    # Albums cannot carry buttons in the Bot API, so the caption holds the Mini App link instead.
    media = [{"type": "photo", "media": url} for url in photo_urls[:10]]
    media[0]["caption"] = caption
    media[0]["parse_mode"] = "HTML"
    result = call("sendMediaGroup", {"chat_id": chat, "media": media})
    return [int(item["message_id"]) for item in result]


def check_channel(username: str) -> dict:
    """Resolve a channel and confirm the housing bot is an admin who can post. Raises BotError."""
    chat = call("getChat", {"chat_id": f"@{username.lstrip('@')}"})
    me = call("getMe")
    member = call("getChatMember", {"chat_id": chat["id"], "user_id": me["id"]})
    status = member.get("status")
    if status not in {"administrator", "creator"}:
        raise BotError(f"@{me.get('username')} is not an admin of this channel")
    if status == "administrator" and not member.get("can_post_messages", False):
        raise BotError(f"@{me.get('username')} cannot post messages in this channel")
    return {"chat_id": int(chat["id"]), "title": str(chat.get("title") or ""), "bot": str(me.get("username") or "")}
