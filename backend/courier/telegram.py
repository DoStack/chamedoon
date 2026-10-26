"""Bot API actions of the courier bot (TELEGRAM_BOT_TOKEN, never the housing bot's token).

The chat engine returns a list of actions instead of calling Telegram itself. The Django webhook
runs them with `execute`; the polling bot in bot/ gets them from /api/bot/courier/update/ and
runs them with aiogram. Tests read the actions as a transcript.
"""

from __future__ import annotations

import logging

from notifications import telegram as courier_bot

logger = logging.getLogger(__name__)


def send(chat_id, text: str, keyboard: list | None = None, *, reply_keyboard: dict | None = None) -> dict:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    elif reply_keyboard:
        payload["reply_markup"] = reply_keyboard
    return {"method": "sendMessage", "payload": payload}


def edit(chat_id, message_id, text: str, keyboard: list | None = None) -> dict:
    if not message_id:
        return send(chat_id, text, keyboard)
    return {
        "method": "editMessageText",
        "payload": {
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": keyboard or []},
        },
    }


def answer(callback_id: str, text: str = "", *, alert: bool = False) -> dict:
    payload: dict = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    if alert:
        payload["show_alert"] = True
    return {"method": "answerCallbackQuery", "payload": payload}


def execute(actions: list[dict]) -> int:
    """Run actions with the courier bot. An edit that fails (old message) is sent as a new message."""
    done = 0
    for action in actions:
        method, payload = action["method"], dict(action["payload"])
        courier_bot.reset_last_telegram_error()
        body = courier_bot.call_telegram_api(method, payload)
        if body is not None:
            done += 1
            continue
        description = str(courier_bot.last_telegram_error().get("description") or "")
        if method == "editMessageText" and "not modified" not in description:
            payload.pop("message_id", None)
            if courier_bot.call_telegram_api("sendMessage", payload) is not None:
                done += 1
        elif method != "answerCallbackQuery":
            logger.info("Courier bot %s failed: %s", method, description)
    return done
