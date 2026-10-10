"""Read listing groups with the Chamedoon Telegram account.

Public t.me/s/ pages miss the private groups where the ads are posted. This uses
the same session as outreach (one connection per extract run, then disconnect)
so the daily job can see those groups. A session must not be opened from two
places at once; extract runs at 02:00 UTC and the send crons start at 07:00 UTC.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timezone as dt_timezone

from django.conf import settings
from django.utils.timezone import is_naive, make_aware

logger = logging.getLogger(__name__)

DEVICE_MODEL = "Chamedoon Outreach (server)"
PAGE_SIZE = 30
PAGE_TIMEOUT_SECONDS = 12


def history_configured() -> bool:
    return bool(
        getattr(settings, "OUTREACH_TG_API_ID", 0)
        and (getattr(settings, "OUTREACH_TG_API_HASH", "") or "").strip()
        and (getattr(settings, "OUTREACH_TG_SESSION", "") or "").strip()
    )


def market_invite_sources() -> dict[str, str]:
    """Map a stable channel key to a t.me/joinchat or t.me/+ hash."""
    raw = (getattr(settings, "MARKET_SOURCE_INVITES", "") or "").strip()
    sources: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            key, invite = item.split(":", 1)
        else:
            invite = item
            key = f"invite-{invite[:12]}"
        key = key.strip().lstrip("@")
        invite = invite.strip().replace("https://t.me/joinchat/", "").replace("https://t.me/+", "").lstrip("+")
        if key and invite:
            sources[key] = invite
    return sources


def message_to_raw(channel_username: str, message, *, public_username: str = "", chat_id: int | None = None) -> dict | None:
    if getattr(message, "action", None):
        return None
    message_id = getattr(message, "id", None)
    posted_at = getattr(message, "date", None)
    if not message_id or posted_at is None:
        return None
    text = (getattr(message, "message", None) or "").strip()
    if not text:
        return None
    if is_naive(posted_at):
        posted_at = make_aware(posted_at, dt_timezone.utc)
    sender = getattr(message, "sender", None)
    author_username = ((getattr(sender, "username", None) or "") if sender is not None else "").strip().lstrip("@")
    if author_username.lower() == (public_username or channel_username).lower():
        author_username = ""
    first = (getattr(sender, "first_name", None) or "") if sender is not None else ""
    last = (getattr(sender, "last_name", None) or "") if sender is not None else ""
    author_name = " ".join(part for part in (first.strip(), last.strip()) if part)
    if not author_name:
        author_name = (getattr(message, "post_author", None) or "").strip()
    return {
        "channel_username": channel_username,
        "telegram_message_id": int(message_id),
        "posted_at": posted_at,
        "text": text,
        "views": getattr(message, "views", None),
        "has_photo": bool(getattr(message, "photo", None)),
        "author_username": author_username[:64],
        "author_name": author_name[:128],
        "source_url": _message_url(public_username, chat_id, int(message_id), channel_username),
    }


def _message_url(public_username: str, chat_id: int | None, message_id: int, channel_username: str) -> str:
    if public_username:
        return f"https://t.me/{public_username.lstrip('@')}/{message_id}"
    if chat_id:
        return f"https://t.me/c/{int(chat_id)}/{message_id}"
    return f"https://t.me/{channel_username}/{message_id}"


class HistoryReader:
    """One logged-in client for every source in a single extract run."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._client = None
        self._entities: dict = {}
        self._labels: dict[str, tuple[str, int | None]] = {}
        self._errors: dict[str, str] = {}
        self._opened = False

    def open(self) -> None:
        self._loop.run_until_complete(self._open())
        self._opened = True

    def close(self) -> None:
        try:
            if self._client is not None and not self._loop.is_closed():
                self._loop.run_until_complete(self._close())
        except Exception:
            logger.exception("Telegram history disconnect failed")
        finally:
            self._client = None
            if not self._loop.is_closed():
                self._loop.close()

    def page(self, username: str, before: int | None) -> list | None:
        if not self._opened:
            return None
        return self._loop.run_until_complete(self._page(username, before))

    def error_for(self, username: str) -> str:
        return self._errors.get(username, "")

    async def _open(self) -> None:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        session = StringSession(settings.OUTREACH_TG_SESSION)
        self._client = TelegramClient(
            session,
            int(settings.OUTREACH_TG_API_ID),
            settings.OUTREACH_TG_API_HASH,
            device_model=DEVICE_MODEL,
            connection_retries=1,
            retry_delay=1,
            timeout=8,
        )
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError("telegram session is not authorized")

    async def _close(self) -> None:
        await self._client.disconnect()

    async def _page(self, username: str, before: int | None) -> list | None:
        if username in self._errors and username not in self._entities:
            return None
        try:
            entity = await self._entity(username)
            if entity is None:
                return None
            try:
                return await self._read(username, entity, before)
            except Exception as exc:
                if not _is_private(exc) or username in market_invite_sources():
                    raise
                from telethon.tl.functions.channels import JoinChannelRequest

                await self._client(JoinChannelRequest(entity))
                logger.info("Joined Telegram source @%s", username)
                return await self._read(username, entity, before)
        except Exception as exc:
            self._errors[username] = str(exc)[:500]
            self._entities.pop(username, None)
            logger.warning("Telegram history failed for %s: %s", username, exc)
            return None

    async def _entity(self, username: str):
        entity = self._entities.get(username)
        if entity is not None:
            return entity
        entity = await self._resolve(username)
        if entity is None:
            logger.warning("Telegram history skipped %s: %s", username, self._errors.get(username, "unavailable"))
            return None
        self._entities[username] = entity
        return entity

    async def _read(self, username: str, entity, before: int | None) -> list:
        public_username, chat_id = self._labels[username]
        kwargs = {"limit": PAGE_SIZE}
        if before:
            kwargs["offset_id"] = int(before)
        messages = await asyncio.wait_for(
            self._client.get_messages(entity, **kwargs),
            timeout=PAGE_TIMEOUT_SECONDS,
        )
        posts = []
        for message in messages:
            raw = message_to_raw(
                username,
                message,
                public_username=public_username,
                chat_id=chat_id,
            )
            if raw is not None:
                posts.append(raw)
        return posts

    async def _resolve(self, username: str):
        from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
        from telethon.tl.types import Channel, Chat, ChatInviteAlready, User

        invites = market_invite_sources()
        if username in invites:
            checked = await self._client(CheckChatInviteRequest(invites[username]))
            if isinstance(checked, ChatInviteAlready):
                entity = checked.chat
            else:
                updates = await self._client(ImportChatInviteRequest(invites[username]))
                chats = list(getattr(updates, "chats", None) or [])
                if not chats:
                    self._errors[username] = "join did not return a chat; an admin may need to approve the account"
                    return None
                entity = chats[0]
                logger.info("Joined Telegram source %s", username)
        else:
            entity = await self._client.get_entity(username)
            if isinstance(entity, User):
                self._errors[username] = "not a group or channel"
                return None
        if not isinstance(entity, (Channel, Chat)):
            self._errors[username] = "not a group or channel"
            return None
        public_username = (getattr(entity, "username", None) or "").strip().lstrip("@")
        chat_id = getattr(entity, "id", None)
        self._labels[username] = (public_username, int(chat_id) if chat_id else None)
        return entity


def _is_private(exc: Exception) -> bool:
    name = type(exc).__name__
    return name in {"ChannelPrivateError", "ChatForbiddenError", "ChannelInvalidError"}
