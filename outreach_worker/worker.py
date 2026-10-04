"""Sends queued outreach DMs from the Chamedoon Telegram account, one at a time.

The backend decides who gets a message, the daily limit and the send window;
this process only sends, reports back, and records replies and opt-outs.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import re
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
from replies import is_opt_out

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("chamedoon.outreach")

TELEGRAM_SERVICE_ID = 777000  # login codes and security notices
GATE_SLEEP_SECONDS = 1800
TRACKED_LINK = re.compile(r"\?startapp=o_[A-Za-z0-9_-]+")
LABEL_LINK = re.compile(r"^(👈 \[[^\]]+\]\((https://t\.me/[^)\s]+)\))$", re.MULTILINE)


def with_visible_link(text: str) -> str:
    """Add the full URL under the label link when the backend has not (deploys can lag).

    Telegram Desktop shows links from unknown senders as plain text, so a label alone gives
    desktop readers nothing to copy. Written as [url](url) so markdown keeps the URL intact.
    """
    if "\n[https://t.me/" in text:
        return text
    return LABEL_LINK.sub(lambda m: f"{m.group(1)}\n[{m.group(2)}]({m.group(2)})", text)
SAMPLE_TEXT = """سلام 👋
از «چمدون» پیام میدم.

۲ مهر توی کانال «koolbarcanada» نوشته بودید می‌خواید بار از 🇨🇦 تورنتو به 🇮🇷 تهران بفرستید.

✈️ ۱ مسافر برای همین مسیر پیدا کردیم:
• ۳۰ مهر — تا ۳٫۵ کیلو

برای دیدن مسافرها و پیام دادن مستقیم بهشون:
👈 [دیدن مشخصات مسافر](https://t.me/Chamed0on_bot)
[https://t.me/Chamed0on_bot](https://t.me/Chamed0on_bot)

هر سوالی داشتید همین‌جا بپرسید 🙏"""


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
        sent = await client.send_message(entity, with_visible_link(item["text"]), link_preview=False)
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
                logger.info("Opt-out recorded for %s", sender.id)
            else:
                await call(api_client.reply, username=username, telegram_user_id=sender.id)
        except api_client.OutreachApiError:
            logger.exception("Could not record reply from %s", sender.id)


async def connect() -> TelegramClient:
    if not API_ID or not API_HASH or not OUTREACH_SERVICE_SECRET:
        raise SystemExit("Set TG_API_ID, TG_API_HASH and OUTREACH_SERVICE_SECRET in outreach_worker/.env.")
    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH, device_model=DEVICE_MODEL)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise SystemExit("This account is not logged in. Run: python login.py")
    return client


async def run() -> None:
    client = await connect()
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


async def send_once() -> None:
    """Claim and send exactly one queued message (same checks as the loop), then exit."""
    client = await connect()  # connect first: a claim we cannot send would be lost after the lease
    try:
        claimed = await call(api_client.claim)
        item = claimed.get("message")
        if not item:
            print(f"Nothing sent ({claimed.get('reason') or 'empty'}).")
            return
        await send(client, item)
    finally:
        await client.disconnect()


async def dry_run(limit: int) -> None:
    """Print the next queued messages. Touches neither Telegram nor the queue."""
    data = await call(api_client.preview, limit)
    print(
        f"Sending: {data['gate']} | attempted today {data['attempted_today']}/{data['daily_limit']}"
        f" | queued {data['queued']}"
    )
    for message in data["messages"]:
        verdict = f"skip: {message['skip']}" if message.get("skip") else "will send"
        print(f"\n--- #{message['id']} -> @{message['username']} [{verdict}] {message.get('source_url', '')}")
        for traveler in message.get("travelers", []):
            print(f"    traveler {traveler_line(traveler)}")
        print(message["text"])


def traveler_line(traveler: dict) -> str:
    when = traveler["flight_date"] or f"{traveler['date_from']}..{traveler['date_to']}"
    kg = f", {traveler['capacity_kg']} kg" if traveler.get("capacity_kg") else ""
    return f"@{traveler['username'] or '?'}: {when}{kg}, score {traveler['score']}"


async def build() -> None:
    """Queue messages now instead of waiting for the daily cron."""
    data = await call(api_client.build)
    print(f"Queued {data['created']} new message(s). Skipped: {data['skipped']}")


async def test_send(username: str, limit: int) -> None:
    """Send the next queued DMs to `username` (e.g. yourself) exactly as recipients would get them.

    The tracked link is swapped for the first matched traveler's listing: a tap on a test copy must
    not count as the real recipient opening their link, and their match page is theirs.
    The queue is not touched.
    """
    username = username.strip().lstrip("@")
    messages = (await call(api_client.preview, limit))["messages"] or [{"text": SAMPLE_TEXT}]
    client = await connect()
    try:
        for index, message in enumerate(messages, start=1):
            travelers = message.get("travelers") or []
            test_link = f"?startapp=explore_{travelers[0]['request_id']}" if travelers else ""
            text = with_visible_link(TRACKED_LINK.sub(test_link, message["text"]))
            await client.send_message(username, text, link_preview=False)
            if index < len(messages):
                await asyncio.sleep(3)
    finally:
        await client.disconnect()
    print(f"Sent {len(messages)} test message(s) to @{username}.")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows pipes default to cp1252
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Print the next queued messages and exit.")
    parser.add_argument("--limit", type=int, help="How many messages --dry-run prints (5) or --test-to sends (1).")
    parser.add_argument("--build", action="store_true", help="Queue messages now instead of waiting for the cron.")
    parser.add_argument("--test-to", metavar="USERNAME", help="Send sample DMs with match details to this username.")
    parser.add_argument("--once", action="store_true", help="Send exactly one queued message to its real recipient and exit.")
    args = parser.parse_args()
    if args.build:
        asyncio.run(build())
    elif args.once:
        asyncio.run(send_once())
    elif args.test_to:
        asyncio.run(test_send(args.test_to, args.limit or 1))
    elif args.dry_run:
        asyncio.run(dry_run(args.limit or 5))
    else:
        asyncio.run(run())


if __name__ == "__main__":
    main()
