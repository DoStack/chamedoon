"""Send one queued outreach DM per cron run, from the Chamedoon Telegram account.

Runs inside a Vercel function (60 s), so each run connects, sends at most one
message and disconnects. Uses its own Telegram session (OUTREACH_TG_SESSION):
a session used from two places at once gets revoked by Telegram.
"""

from __future__ import annotations

import asyncio
import logging

from django.conf import settings

from outreach.models import OutreachMessage
from outreach.services import PEER_FLOOD, claim_next_message, record_result

logger = logging.getLogger(__name__)

DEVICE_MODEL = "Chamedoon Outreach (server)"
DELIVERY_TIMEOUT_SECONDS = 40
SESSION_INVALID_PAUSE_SECONDS = 24 * 60 * 60


def sender_configured() -> bool:
    return bool(
        getattr(settings, "OUTREACH_TG_API_ID", 0)
        and getattr(settings, "OUTREACH_TG_API_HASH", "")
        and getattr(settings, "OUTREACH_TG_SESSION", "")
    )


def send_next() -> dict:
    message, reason = claim_next_message()
    if message is None:
        return {"sent": False, "reason": reason}
    outcome = asyncio.run(_deliver_within_timeout(message))
    record_result(message, **outcome)
    logger.info("Outreach %s to @%s: %s", message.pk, message.recipient_username, outcome)
    return {
        "sent": outcome["outcome"] == "sent",
        "message_id": message.pk,
        "username": message.recipient_username,
        "outcome": outcome["outcome"],
        "error_code": outcome.get("error_code", ""),
    }


async def _deliver_within_timeout(message: OutreachMessage) -> dict:
    try:
        return await asyncio.wait_for(_deliver(message), timeout=DELIVERY_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        # It may have been delivered; never risk sending the same DM twice.
        return {"outcome": "failed", "error_code": "TIMEOUT"}


async def _deliver(message: OutreachMessage) -> dict:
    from telethon import TelegramClient, errors, types
    from telethon.sessions import StringSession

    try:
        session = StringSession(settings.OUTREACH_TG_SESSION)
    except ValueError:  # a mangled OUTREACH_TG_SESSION value
        return {
            "outcome": "retry",
            "error_code": "SESSION_INVALID",
            "retry_after_seconds": SESSION_INVALID_PAUSE_SECONDS,
        }
    client = TelegramClient(
        session,
        int(settings.OUTREACH_TG_API_ID),
        settings.OUTREACH_TG_API_HASH,
        device_model=DEVICE_MODEL,
        connection_retries=1,
        retry_delay=1,
        timeout=10,
    )
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return {
                "outcome": "retry",
                "error_code": "SESSION_INVALID",
                "retry_after_seconds": SESSION_INVALID_PAUSE_SECONDS,
            }
        entity = await client.get_entity(message.recipient_username)
        if not isinstance(entity, types.User) or entity.bot or entity.deleted:
            return {"outcome": "failed", "error_code": "NOT_A_PERSON"}
        # The text is markdown: «👈 [label](url)» and «[url](url)» become tappable links.
        sent = await client.send_message(entity, message.text, link_preview=False)
        return {"outcome": "sent", "recipient_telegram_id": entity.id, "telegram_message_id": sent.id}
    except errors.FloodWaitError as exc:
        return {"outcome": "retry", "error_code": "FLOOD_WAIT", "retry_after_seconds": exc.seconds}
    except errors.PeerFloodError:
        return {"outcome": "retry", "error_code": PEER_FLOOD}
    except (errors.UsernameNotOccupiedError, errors.UsernameInvalidError, ValueError) as exc:
        return {"outcome": "failed", "error_code": "USERNAME_NOT_FOUND", "error_detail": str(exc)}
    except errors.RPCError as exc:
        # e.g. PRIVACY_PREMIUM_REQUIRED, ALLOW_PAYMENT_REQUIRED: this person can't be reached.
        return {"outcome": "failed", "error_code": exc.message or type(exc).__name__, "error_detail": str(exc)}
    except (OSError, ConnectionError) as exc:
        return {"outcome": "retry", "error_code": "CONNECT_FAILED", "error_detail": str(exc)}
    finally:
        await client.disconnect()
