"""Messages to Mohammad and to the private group that watches the chats. Sent by the housing bot, never by a sending account.

A failure here never stops the step that called it: everything is also in the admin.
"""

from __future__ import annotations

import logging

from home import bot
from outreach import conf

logger = logging.getLogger(__name__)
TELEGRAM_LIMIT = 3900


def _post(chat_id: int | None, text: str) -> list[int]:
    if not chat_id:
        return []
    try:
        return bot.send_message(chat_id, text[:TELEGRAM_LIMIT])
    except bot.BotError as exc:
        logger.warning("outreach_notify_failed chat=%s error=%s", chat_id, exc.description)
    except OSError as exc:
        logger.warning("outreach_notify_failed chat=%s error=%s", chat_id, exc)
    return []


def tell_mohammad(text: str) -> None:
    row = conf.row()
    logger.warning("outreach_alert %s", text)
    _post(row.alert_chat_id or row.transcript_chat_id, text)


def transcript(text: str) -> None:
    _post(conf.row().transcript_chat_id, text)
