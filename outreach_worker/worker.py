"""Sends queued outreach DMs from the Chamedoon Telegram account, one at a time.

The backend decides who gets a message, the daily limit and the send window;
this process only sends, reports back, and records replies and opt-outs.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys

from telethon import TelegramClient, errors, events
from telethon.sessions import StringSession
from telethon.tl.types import User

import api_client
from config import (
    API_HASH,
    API_ID,
    DEVICE_MODEL,
    IDLE_SECONDS,
    MAX_DELAY_SECONDS,
    MIN_DELAY_SECONDS,
    OUTREACH_SERVICE_SECRET,
    SESSION,
)
from replies import OPT_OUT_CONFIRMATION, is_opt_out

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("chamedoon.outreach")

TELEGRAM_SERVICE_ID = 777000  # login codes and security notices
GATE_SLEEP_SECONDS = 1800


async def call(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


async def report(item: dict, outcome: str, **fields) -> None:
    try:
        await call(api_client.result, item["id"], outcome, **fields)
    except api_client.OutreachApiError:
        logger.exception("Could not report %s for outreach %s", outcome, item["id"])


async def send(client: TelegramClient, item: dict) -> None:
    try:
        entity = await client.get_entity(item["username"])
        if not isinstance(entity, User) or entity.bot or entity.deleted:
            await report(item, "failed", error_code="NOT_A_PERSON")
            return
        sent = await client.send_message(entity, item["text"], link_preview=False)
    except errors.FloodWaitError as exc:
        logger.warning("Telegram asked to wait %ss.", exc.seconds)
        await report(item, "retry", error_code="FLOOD_WAIT", retry_after_seconds=exc.seconds)
        return
    except errors.PeerFloodError:
        logger.error("Telegram limited this account (PEER_FLOOD). The backend pauses sending for 48h.")
        await report(item, "retry", error_code="PEER_FLOOD")
        return
    except (errors.UsernameNotOccupiedError, errors.UsernameInvalidError, ValueError) as exc:
        await report(item, "failed", error_code="USERNAME_NOT_FOUND", error_detail=str(exc))
        return
    except errors.RPCError as exc:
        # e.g. PRIVACY_PREMIUM_REQUIRED, ALLOW_PAYMENT_REQUIRED: this person can't be reached.
        await report(item, "failed", error_code=exc.message or type(exc).__name__, error_detail=str(exc))
        return
    await report(item, "sent", recipient_telegram_id=entity.id, telegram_message_id=sent.id)
    logger.info("Sent outreach %s to @%s", item["id"], item["username"])


def watch_replies(client: TelegramClient) -> None:
    @client.on(events.NewMessage(incoming=True, func=lambda event: event.is_private))
    async def on_private_message(event) -> None:
        sender = await event.get_sender()
        if not isinstance(sender, User) or sender.bot or sender.id == TELEGRAM_SERVICE_ID:
            return
        username = sender.username or ""
        try:
            if is_opt_out(event.raw_text):
                await call(api_client.opt_out, username=username, telegram_user_id=sender.id)
                await event.reply(OPT_OUT_CONFIRMATION)
                logger.info("Opt-out recorded for %s", sender.id)
            else:
                await call(api_client.reply, username=username, telegram_user_id=sender.id)
        except api_client.OutreachApiError:
            logger.exception("Could not record reply from %s", sender.id)


async def run() -> None:
    if not API_ID or not API_HASH or not OUTREACH_SERVICE_SECRET:
        raise SystemExit("Set TG_API_ID, TG_API_HASH and OUTREACH_SERVICE_SECRET in outreach_worker/.env.")
    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH, device_model=DEVICE_MODEL)
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("This account is not logged in. Run: python login.py")
    me = await client.get_me()
    logger.info("Sending as %s (@%s).", me.first_name, me.username)
    watch_replies(client)

    while True:
        try:
            await call(api_client.heartbeat)
            claimed = await call(api_client.claim)
        except api_client.OutreachApiError as exc:
            logger.warning("Backend unavailable: %s", exc)
            await asyncio.sleep(120)
            continue
        item = claimed.get("message")
        if not item:
            reason = claimed.get("reason") or "empty"
            logger.info("Nothing to send now (%s).", reason)
            await asyncio.sleep(IDLE_SECONDS if reason == "empty" else GATE_SLEEP_SECONDS)
            continue
        await send(client, item)
        await asyncio.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))


async def dry_run(limit: int) -> None:
    """Print the next queued messages. Touches neither Telegram nor the queue."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows pipes default to cp1252
    data = await call(api_client.preview, limit)
    print(
        f"Sending: {data['gate']} | attempted today {data['attempted_today']}/{data['daily_limit']}"
        f" | queued {data['queued']}"
    )
    for message in data["messages"]:
        print(f"\n--- #{message['id']} -> @{message['username']}\n{message['text']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Print the next queued messages and exit.")
    parser.add_argument("--limit", type=int, default=5, help="How many messages --dry-run prints.")
    args = parser.parse_args()
    asyncio.run(dry_run(args.limit) if args.dry_run else run())


if __name__ == "__main__":
    main()
