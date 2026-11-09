"""Sends queued outreach DMs from several Telegram accounts, and carries each account's chats.

Runs on bigde-vps, one session per account, each session in this one place only. The backend decides everything:
who gets a message, each account's limits and pauses, what the agent answers. This process only talks to Telegram.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import re
import sys
from pathlib import Path

from telethon import TelegramClient, errors, events
from telethon.sessions import StringSession
from telethon.tl.types import User

import api_client
from config import (
    ACCOUNTS,
    API_HASH,
    API_ID,
    DEVICE_MODEL,
    OUTREACH_SERVICE_SECRET,
    VIDEO_DIR,
    account_config,
    account_device,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("chamedoon.outreach")

TELEGRAM_SERVICE_ID = 777000  # login codes and security notices
TICK_SECONDS = 60
# How long until the next claim, by what the backend said.
CLAIM_WAIT = {"": 60, "empty": 600, "outside_window": 900, "account_gap": 120, "account_hourly_limit": 300, "model_down": 180, "account_paused": 300}
DEFAULT_CLAIM_WAIT = 1800
AGENT_POLL_SECONDS = 60
DIALOG_SCAN_SECONDS = 600
DIALOG_SCAN_LIMIT = 60
HEARTBEAT_SECONDS = 60
MAX_PHOTO_BYTES = 3 * 1024 * 1024
LABEL_LINK = re.compile(r"^(👈 \[[^\]]+\]\((https://t\.me/[^)\s]+)\))$", re.MULTILINE)
SESSION_ERRORS = (
    errors.AuthKeyUnregisteredError,
    errors.SessionRevokedError,
    errors.SessionExpiredError,
    errors.UserDeactivatedError,
    errors.UserDeactivatedBanError,
    errors.AuthKeyDuplicatedError,
)


def with_visible_link(text: str) -> str:
    """Add the full URL under the label link when the backend has not (deploys can lag).

    Telegram Desktop shows links from unknown senders as plain text, so a label alone gives
    desktop readers nothing to copy. Written as [url](url) so markdown keeps the URL intact.
    """
    if "\n[https://t.me/" in text:
        return text
    return LABEL_LINK.sub(lambda m: f"{m.group(1)}\n[{m.group(2)}]({m.group(2)})", text)


async def call(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


async def report(item: dict, outcome: str, **fields) -> None:
    try:
        await call(api_client.result, item["id"], outcome, **fields)
    except api_client.OutreachApiError:
        logger.exception("Could not report %s for outreach %s", outcome, item["id"])


# Per account and video: what Telegram stored after the first upload, so the file goes up once and is reused.
UPLOADED: dict[tuple[str, str], object] = {}


_UNTIL = re.compile(r"until (\d{1,2} \w+ \d{4}),? (\d{1,2}:\d{2}) UTC", re.IGNORECASE)


async def spambot_status(client: TelegramClient) -> str:
    """Ask @SpamBot: "free" (no limits), "until=<ISO time>" (limited until then), "limited" or "unknown"."""
    from datetime import datetime, timezone

    try:
        bot = await client.get_entity("SpamBot")
        sent = await client.send_message(bot, "/start")
        for _ in range(10):
            await asyncio.sleep(2)
            reply = next((m for m in await client.get_messages(bot, limit=3) if not m.out and m.id > sent.id), None)
            if reply is None:
                continue
            text = reply.raw_text or ""
            logger.warning("SpamBot said: %s", " ".join(text[:300].split()))
            if "no limits" in text.lower() or "free as a bird" in text.lower():
                return "free"
            found = _UNTIL.search(text)
            if found:
                try:
                    until = datetime.strptime(f"{found.group(1)} {found.group(2)}", "%d %b %Y %H:%M").replace(tzinfo=timezone.utc)
                except ValueError:
                    until = None
                if until is not None:
                    return f"until={until.isoformat()}"
            return "limited"
    except (errors.RPCError, ValueError) as exc:
        logger.warning("SpamBot could not be asked: %r", exc)
    return "unknown"


async def send_video(client: TelegramClient, item: dict, entity) -> None:
    """The intro video, after the person answered and we answered back, with its one short line as caption."""
    kind = item.get("video") or ""
    path = Path(VIDEO_DIR) / f"{kind}.mp4"
    if not kind or not path.exists():
        await call(api_client.video_result, item["id"], ok=False, error="no_video_file")
        return
    key = (item.get("account", ""), kind)
    try:
        sent = await client.send_file(entity, UPLOADED.get(key) or str(path), caption=item.get("caption") or None, supports_streaming=True)
        if key not in UPLOADED and getattr(sent, "media", None) is not None:
            UPLOADED[key] = sent.media
    except errors.FloodWaitError as exc:
        await call(api_client.video_result, item["id"], ok=False, error=f"FLOOD_WAIT_{exc.seconds}")
        return
    except errors.PeerFloodError:
        status = await spambot_status(client)
        await call(api_client.video_result, item["id"], ok=False, error=f"PEER_FLOOD:{status}"[:64])
        return
    except errors.RPCError as exc:
        await call(api_client.video_result, item["id"], ok=False, error=(exc.message or type(exc).__name__)[:60])
        return
    await call(api_client.video_result, item["id"], ok=True, telegram_message_id=getattr(sent, "id", None))
    logger.info("Sent the %s video for outreach %s from %s", kind, item["id"], item.get("account", "?"))


async def send(client: TelegramClient, item: dict) -> bool:
    """Returns False when this account's session is gone."""
    if item.get("video_only"):
        try:
            entity = await peer(client, item["telegram_user_id"]) if item.get("telegram_user_id") else await client.get_entity(item["username"])
        except (ValueError, errors.RPCError) as exc:
            await call(api_client.video_result, item["id"], ok=False, error=type(exc).__name__[:60])
            return True
        await send_video(client, item, entity)
        return True
    try:
        entity = await client.get_entity(item["username"])
        if not isinstance(entity, User) or entity.bot or entity.deleted:
            await report(item, "failed", error_code="NOT_A_PERSON")
            return True
        sent = await client.send_message(entity, with_visible_link(item["text"]), link_preview=False)
    except errors.FloodWaitError as exc:
        logger.warning("Telegram asked to wait %ss.", exc.seconds)
        await report(item, "retry", error_code="FLOOD_WAIT", retry_after_seconds=exc.seconds)
        return True
    except errors.PeerFloodError as exc:
        # The raw error, then @SpamBot's own word on whether the account is limited and until when.
        status = await spambot_status(client)
        logger.error("PEER_FLOOD on outreach %s: %r; SpamBot: %s", item["id"], exc, status)
        await report(item, "retry", error_code="PEER_FLOOD", error_detail=f"spambot:{status}")
        return True
    except SESSION_ERRORS as exc:
        await report(item, "retry", error_code="SESSION_INVALID", error_detail=type(exc).__name__)
        return False
    except (errors.UsernameNotOccupiedError, errors.UsernameInvalidError, ValueError) as exc:
        await report(item, "failed", error_code="USERNAME_NOT_FOUND", error_detail=str(exc))
        return True
    except errors.RPCError as exc:
        # e.g. PRIVACY_PREMIUM_REQUIRED, ALLOW_PAYMENT_REQUIRED: this person can't be reached.
        await report(item, "failed", error_code=exc.message or type(exc).__name__, error_detail=str(exc))
        return True
    except (OSError, ConnectionError) as exc:
        await report(item, "retry", error_code="CONNECT_FAILED", error_detail=str(exc))
        return True
    await report(item, "sent", recipient_telegram_id=entity.id, telegram_message_id=sent.id)
    logger.info("Sent outreach %s from %s", item["id"], item.get("account", "?"))
    if item.get("video"):
        await asyncio.sleep(4)
        await send_video(client, item, entity)
    return True


async def send_replies(client: TelegramClient, name: str) -> None:
    """The agent's answers that are due. One at a time, until there are none."""
    while True:
        answer = (await call(api_client.agent_next, name)).get("reply")
        if not answer:
            return
        try:
            sent = await client.send_message(await peer(client, answer["telegram_user_id"]), answer["text"], link_preview=False)
        except ValueError:
            # Not in this account's chats any more: a person answers from the phone.
            await call(api_client.agent_result, answer["turn_id"], ok=False, error="PEER_UNKNOWN")
            continue
        except errors.FloodWaitError as exc:
            await call(api_client.agent_result, answer["turn_id"], ok=False, error="FLOOD_WAIT", retry_after_seconds=exc.seconds)
            return
        except errors.RPCError as exc:
            await call(api_client.agent_result, answer["turn_id"], ok=False, error=exc.message or type(exc).__name__)
            continue
        await call(api_client.agent_result, answer["turn_id"], ok=True, telegram_message_id=sent.id)


async def scan_dialogs(client: TelegramClient, name: str) -> int:
    """Private chats where the person wrote last and we wrote to them before: their messages go to the backend, which
    keeps each once. Catches the first replies that came while this worker was down or restarting (2026-11-09: people
    waited hours because their reply never reached the DB)."""
    found = 0
    try:
        async for dialog in client.iter_dialogs(limit=DIALOG_SCAN_LIMIT):
            last = dialog.message
            if not dialog.is_user or last is None or last.out or getattr(dialog.entity, "bot", False):
                continue
            history = await client.get_messages(dialog.entity, limit=10)
            if not any(message.out for message in history):
                continue  # someone we never wrote to: not an outreach chat
            entity = dialog.entity
            for message in reversed(history):
                if message.out:
                    continue
                await call(
                    api_client.inbound,
                    account=name,
                    telegram_user_id=int(entity.id),
                    username=getattr(entity, "username", "") or "",
                    message_id=message.id,
                    text=message.raw_text or "",
                    has_photo=bool(message.photo),
                    has_video=bool(message.video),
                    sent_at=message.date.isoformat() if message.date else "",
                )
                found += 1
    except (errors.RPCError, api_client.OutreachApiError) as exc:
        logger.warning("%s could not scan its dialogs: %r", name, exc)
    return found


async def catch_up(client: TelegramClient, name: str) -> None:
    """After a restart: what each chat said and what was answered by hand while this worker was down."""
    try:
        people = (await call(api_client.chats, name)).get("telegram_user_ids") or []
    except api_client.OutreachApiError:
        logger.exception("Could not read the chats of %s", name)
        return
    for telegram_id in people:
        try:
            history = await client.get_messages(int(telegram_id), limit=10)
        except (ValueError, errors.RPCError):
            continue
        for message in reversed(history):
            if message.out:
                if message.raw_text:
                    await call(api_client.outbound, account=name, telegram_user_id=int(telegram_id), message_id=message.id, text=message.raw_text, sent_at=message.date.isoformat() if message.date else "")
            else:
                await call(
                    api_client.inbound,
                    account=name,
                    telegram_user_id=int(telegram_id),
                    username="",
                    message_id=message.id,
                    text=message.raw_text or "",
                    has_photo=bool(message.photo),
                    has_video=bool(message.video),
                    sent_at=message.date.isoformat() if message.date else "",
                )


def watch_chats(client: TelegramClient, name: str, wake: asyncio.Event) -> None:
    @client.on(events.NewMessage(outgoing=True, func=lambda event: event.is_private))
    async def on_own_message(event) -> None:
        # Mohammad answering from his phone: the agent must not answer the same thing again.
        if not event.raw_text:
            return
        try:
            await call(api_client.outbound, account=name, telegram_user_id=event.chat_id, message_id=event.id, text=event.raw_text, sent_at=event.date.isoformat() if event.date else "")
        except api_client.OutreachApiError:
            logger.exception("Could not record a message sent by hand from %s", name)

    @client.on(events.NewMessage(incoming=True, func=lambda event: event.is_private))
    async def on_private_message(event) -> None:
        sender = await event.get_sender()
        if not isinstance(sender, User) or sender.bot or sender.id == TELEGRAM_SERVICE_ID:
            return
        has_photo = bool(event.photo)
        try:
            answer = await call(
                api_client.inbound,
                account=name,
                telegram_user_id=sender.id,
                username=sender.username or "",
                message_id=event.id,
                text=event.raw_text or "",
                has_photo=has_photo,
                has_video=bool(event.video),
                sent_at=event.date.isoformat() if event.date else "",
            )
            # A photo is fetched only from someone in a chat, for their own listing.
            if has_photo and answer.get("want_photo") and (event.photo.sizes or event.file):
                data = await event.download_media(bytes)
                if data and len(data) <= MAX_PHOTO_BYTES:
                    await call(api_client.inbound_photo, answer["turn_id"], base64.b64encode(data).decode("ascii"))
            wake.set()
        except api_client.OutreachApiError:
            logger.exception("Could not record a message from %s to %s", sender.id, name)


async def watch_groups(client: TelegramClient, name: str) -> None:
    """Questions under our reposts in the city discussion groups go to the backend; the account says nothing there."""
    try:
        group_ids = [int(value) for value in (await call(api_client.groups)).get("groups", [])]
    except api_client.OutreachApiError:
        logger.warning("%s could not read the group list; no group questions this run.", name)
        return
    if not group_ids:
        return
    try:
        joined = {int(dialog.id) for dialog in await client.get_dialogs(limit=500) if dialog.is_group}
    except errors.RPCError:
        joined = set()
    missing = [group for group in group_ids if group not in joined]
    if missing:
        # Only groups the account is in send it messages; joining is Mohammad's call, never done here.
        logger.warning("%s is not in %d of our groups: %s", name, len(missing), ", ".join(str(group) for group in missing))

    @client.on(events.NewMessage(incoming=True, chats=group_ids, func=lambda event: event.is_reply))
    async def on_group_reply(event) -> None:
        try:
            sender = await event.get_sender()
            if not isinstance(sender, User):
                return
            reply_to = event.message.reply_to
            top = getattr(reply_to, "reply_to_top_id", None) or getattr(reply_to, "reply_to_msg_id", None)
            if not top:
                return
            original = await client.get_messages(event.chat_id, ids=top)
            forwarded = getattr(original, "fwd_from", None) if original else None
            post_id = getattr(forwarded, "channel_post", None) if forwarded else None
            if not post_id:
                # Not under a post of our channel.
                return
            await call(
                api_client.group_question,
                account=name,
                group_chat_id=int(event.chat_id),
                group_message_id=int(event.id),
                channel_post_id=int(post_id),
                asker_id=int(sender.id),
                asker_username=sender.username or "",
                asker_name=" ".join(part for part in (sender.first_name, sender.last_name) if part),
                is_bot=bool(sender.bot),
                text=event.raw_text or "",
            )
        except (api_client.OutreachApiError, errors.RPCError):
            logger.exception("%s could not report a group question", name)

    logger.info("%s listens to %d groups for questions.", name, len(group_ids))


async def connect(name: str) -> TelegramClient | None:
    """None when this account has no session here, or Telegram no longer accepts it."""
    api_id, api_hash, session = account_config(name)
    if not api_id or not api_hash or not session:
        logger.warning("No session for %s here: this account does not run.", name)
        return None
    client = TelegramClient(StringSession(session), api_id, api_hash, catch_up=True, **account_device(name))
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        logger.error("%s is not logged in. Only its owner can log it in again.", name)
        await call(api_client.heartbeat, name, session_invalid=True, error="not_authorized")
        return None
    await warm_cache(client, name)
    return client


async def warm_cache(client: TelegramClient, name: str) -> None:
    """A fresh string session knows no one: read the dialogs so chats can be reached by user id."""
    try:
        await client.get_dialogs(limit=500)
    except errors.RPCError as exc:
        logger.warning("%s could not read its dialogs: %s", name, exc)


async def peer(client: TelegramClient, telegram_id: int):
    """The chat by user id; reads the dialogs once more when the id is not cached yet."""
    try:
        return await client.get_input_entity(int(telegram_id))
    except ValueError:
        await client.get_dialogs(limit=500)
        return await client.get_input_entity(int(telegram_id))


async def account_loop(name: str) -> None:
    client = await connect(name)
    if client is None:
        return
    me = await client.get_me()
    logger.info("%s is up as user id %s.", name, me.id)
    wake = asyncio.Event()
    watch_chats(client, name, wake)
    await watch_groups(client, name)
    # A backend error at start (a deploy, a 500) must not end the account: retry until it answers.
    for attempt in range(30):
        try:
            await catch_up(client, name)
            await scan_dialogs(client, name)
            break
        except api_client.OutreachApiError as exc:
            logger.warning("%s could not catch up (%s), retrying.", name, exc)
            await asyncio.sleep(min(60 * (attempt + 1), 300))
    loop = asyncio.get_running_loop()
    next_claim = 0.0
    next_agent = 0.0
    next_beat = 0.0
    next_scan = loop.time() + DIALOG_SCAN_SECONDS
    while True:
        now = loop.time()
        try:
            if now >= next_scan:
                # Every 10 minutes: any reply that never reached us is recorded and answered.
                if await scan_dialogs(client, name):
                    wake.set()
                next_scan = now + DIALOG_SCAN_SECONDS
            if now >= next_agent or wake.is_set():
                wake.clear()
                await send_replies(client, name)
                next_agent = now + AGENT_POLL_SECONDS
            if now >= next_beat:
                # Alive every minute, whatever the claim waits for (sara went 31 min silent while paused).
                await call(api_client.heartbeat, name)
                next_beat = now + HEARTBEAT_SECONDS
            if now >= next_claim:
                claimed = await call(api_client.claim, name)
                item = claimed.get("message")
                reason = claimed.get("reason") or ""
                if item:
                    alive = await send(client, {**item, "account": name})
                    if not alive:
                        await call(api_client.heartbeat, name, session_invalid=True, error="send_session_error")
                        await client.disconnect()
                        return
                else:
                    logger.info("%s: nothing to send now (%s).", name, reason or "empty")
                next_claim = loop.time() + (CLAIM_WAIT.get(reason, DEFAULT_CLAIM_WAIT) if not item else CLAIM_WAIT[""])
        except api_client.OutreachApiError as exc:
            logger.warning("Backend unavailable: %s", exc)
        except SESSION_ERRORS as exc:
            logger.error("%s lost its session: %s", name, type(exc).__name__)
            await call(api_client.heartbeat, name, session_invalid=True, error=type(exc).__name__)
            await client.disconnect()
            return
        except Exception:
            # Anything else is logged and the loop goes on; a dead loop with a live process sends nothing.
            logger.exception("%s loop error, continuing.", name)
        # A new message wakes the loop; its answer still waits for the natural delay on the backend.
        try:
            await asyncio.wait_for(wake.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass


async def run(names: list[str]) -> None:
    if not API_ID or not API_HASH or not OUTREACH_SERVICE_SECRET:
        raise SystemExit("Set TG_API_ID, TG_API_HASH and OUTREACH_SERVICE_SECRET in outreach_worker/.env.")
    results = await asyncio.gather(*(account_loop(name) for name in names), return_exceptions=True)
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            logger.error("%s stopped: %s", name, result)
    # Every account ended: stay up so a restart loop does not hammer Telegram's login.
    await asyncio.Event().wait()


async def whoami(names: list[str]) -> None:
    """Which account each session is. Sends nothing and reports nothing."""
    for name in names:
        api_id, api_hash, session = account_config(name)
        if not session:
            print(f"{name}: no session here")
            continue
        client = TelegramClient(StringSession(session), api_id, api_hash, **account_device(name))
        await client.connect()
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"{name}: {me.first_name or ''} @{me.username or '-'}")
        else:
            print(f"{name}: session not valid")
        await client.disconnect()


async def dry_run(limit: int) -> None:
    """Print the next queued messages. Touches neither Telegram nor the queue."""
    data = await call(api_client.preview, limit)
    print(f"Sending: {data['gate']} | attempted in 24h {data['attempted_today']}/{data['daily_limit']} | queued {data['queued']}")
    for message in data["messages"]:
        verdict = f"skip: {message['skip']}" if message.get("skip") else "will send"
        print(f"\n--- #{message['id']} {message.get('kind', '')} {message.get('city', '')} -> @{message['username']} [{verdict}] {message.get('source_url', '')}")
        print(message["text"])


async def build() -> None:
    """Queue messages now instead of waiting for the daily cron."""
    data = await call(api_client.build)
    print(f"Queued {data['created']} new message(s). Skipped: {data['skipped']}")


async def test_send(name: str, username: str, limit: int) -> None:
    """Send the next queued DMs to `username`, a person who said yes to the test, from account `name`.

    The queue is not touched. Opened by anyone but the recipient, the link counts for nothing.
    """
    username = username.strip().lstrip("@")
    messages = (await call(api_client.preview, limit))["messages"]
    if not messages:
        print("Nothing queued to test with.")
        return
    client = await connect(name)
    if client is None:
        return
    try:
        for index, message in enumerate(messages, start=1):
            await client.send_message(username, with_visible_link(message["text"]), link_preview=False)
            if index < len(messages):
                await asyncio.sleep(3)
    finally:
        await client.disconnect()
    print(f"Sent {len(messages)} test message(s) to @{username} from {name}.")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows pipes default to cp1252
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Print the next queued messages and exit.")
    parser.add_argument("--limit", type=int, help="How many messages --dry-run prints (5) or --test-to sends (1).")
    parser.add_argument("--build", action="store_true", help="Queue messages now instead of waiting for the cron.")
    parser.add_argument("--whoami", action="store_true", help="Say which Telegram account each session is, and exit.")
    parser.add_argument("--account", help="The account for --test-to, or the only one to run.")
    parser.add_argument("--test-to", metavar="USERNAME", help="Send the next queued messages to this username instead of their recipients.")
    parser.add_argument("--hold", type=int, metavar="ID", help="Keep queued message ID out of sending until released.")
    parser.add_argument("--release", type=int, metavar="ID", help="Queue a held message ID again.")
    parser.add_argument("--reason", default="", help="Why --hold holds the message (shown in Admin).")
    args = parser.parse_args()
    names = [args.account.lower()] if args.account else ACCOUNTS
    if args.hold:
        print(api_client.hold(args.hold, args.reason))
    elif args.release:
        print(api_client.release(args.release))
    elif args.build:
        asyncio.run(build())
    elif args.whoami:
        asyncio.run(whoami(names))
    elif args.test_to:
        if not args.account:
            raise SystemExit("--test-to needs --account.")
        asyncio.run(test_send(args.account.lower(), args.test_to, args.limit or 1))
    elif args.dry_run:
        asyncio.run(dry_run(args.limit or 5))
    else:
        asyncio.run(run(names))


if __name__ == "__main__":
    main()
