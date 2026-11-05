"""Telegram reader for housing sources.

Extends the market HistoryReader in three ways: it keeps photo-only messages (the
parts of an album after the captioned one), it records the album id and the
sender id, and it can download one photo. Private groups are joined through
the invite link saved on the source in the admin (HOME_SOURCE_INVITES, key:hash pairs, still works as a fallback). Neither is in the repository.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timezone as dt_timezone

from django.conf import settings
from django.utils.timezone import is_naive, make_aware

from market.poster import read_poster
from market.telegram_history import PAGE_SIZE, PAGE_TIMEOUT_SECONDS, HistoryReader, market_invite_sources

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT_SECONDS = 25
WANTED_PX = 1280
# Usernames a run may look up. Each lookup is a ResolveUsernameRequest, which Telegram limits per account per day;
# once a source's address is stored (`HomeSource.tg_peer_id`) it is never looked up again.
MAX_LOOKUPS_PER_RUN = 10
LOOKUP_DEFERRED = "waits for a username lookup in a later run"
# Telegram errors that mean a stored address no longer works: the next run looks the username up again.
STALE_PEER_ERRORS = {"ChannelInvalidError", "ChannelPrivateError", "ChatIdInvalidError", "PeerIdInvalidError", "ChatForbiddenError"}
# Where a rich message keeps its words (Telegram "rich messages": text in blocks, `message` empty).
_RICH_TEXT_FIELDS = ("texts", "text", "alt")
_RICH_BLOCK_FIELDS = ("title", "subtitle", "author", "text", "caption", "credit", "items", "blocks", "rows", "articles")


def home_invite_sources() -> dict[str, str]:
    raw = (getattr(settings, "HOME_SOURCE_INVITES", "") or "").strip()
    sources: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if ":" not in item:
            continue
        key, invite = item.split(":", 1)
        invite = invite.strip().replace("https://t.me/joinchat/", "").replace("https://t.me/+", "").lstrip("+")
        key = key.strip().lstrip("@")
        if key and invite:
            sources[key] = invite
    return sources


def invite_hash(value: str) -> str:
    """The hash of a private-group invite from any of the link shapes people paste."""
    raw = (value or "").strip()
    raw = raw.split("?", 1)[0].rstrip("/")
    for prefix in ("https://", "http://"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    raw = raw.replace("t.me/", "", 1).replace("telegram.me/", "", 1)
    raw = raw.replace("joinchat/", "", 1)
    return raw.lstrip("+").strip()


def database_invites() -> dict[str, str]:
    """Invite hashes saved on the sources in the admin, keyed by source username."""
    from home.models import HomeSource

    found: dict[str, str] = {}
    for username, link in HomeSource.objects.exclude(invite_link="").values_list("username", "invite_link"):
        hashed = invite_hash(link)
        if hashed:
            found[username] = hashed
    return found


def cached_peers() -> dict[str, tuple[str, int, int | None, str]]:
    """Stored Telegram addresses of the enabled sources: username -> (kind, id, access hash, public username)."""
    from home.models import HomeSource

    rows = HomeSource.objects.filter(enabled=True, tg_peer_id__isnull=False).values_list(
        "username", "tg_peer_kind", "tg_peer_id", "tg_access_hash", "tg_public_username"
    )
    return {username: (kind, peer_id, access_hash, public) for username, kind, peer_id, access_hash, public in rows if kind in {"channel", "chat"}}


def _rich_node_text(node) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, (list, tuple)):
        return "".join(_rich_node_text(item) for item in node)
    for name in _RICH_TEXT_FIELDS:
        value = getattr(node, name, None)
        if value is not None:
            return _rich_node_text(value)
    return ""


def _rich_block_lines(node, depth: int = 0) -> list[str]:
    if node is None or depth > 8:
        return []
    if isinstance(node, (list, tuple)):
        return [line for item in node for line in _rich_block_lines(item, depth + 1)]
    cells = getattr(node, "cells", None)
    if cells is not None:
        row = " | ".join(line for cell in cells for line in _rich_block_lines(cell, depth + 1))
        return [row] if row else []
    if type(node).__name__.startswith("Text") or isinstance(node, str):
        text = _rich_node_text(node).strip()
        return [text] if text else []
    return [line for name in _RICH_BLOCK_FIELDS for line in _rich_block_lines(getattr(node, name, None), depth + 1)]


def rich_message_text(message) -> str:
    """Plain text of a Telegram rich message, "" when it is not one.

    A rich message (Telegram's block format: headings, lists, tables) leaves `message` empty and keeps its
    words in `rich_message.blocks`. Read as an empty message, a rich ad was dropped without a trace.
    """
    blocks = getattr(getattr(message, "rich_message", None), "blocks", None)
    if not blocks:
        return ""
    paragraphs = ("\n".join(_rich_block_lines(block)) for block in blocks)
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph).strip()


def home_message_to_raw(username: str, message, *, public_username: str = "", chat_id: int | None = None) -> dict | None:
    if getattr(message, "action", None):
        return None
    message_id = getattr(message, "id", None)
    posted_at = getattr(message, "date", None)
    if not message_id or posted_at is None:
        return None
    text = (getattr(message, "message", None) or "").strip() or rich_message_text(message)
    has_photo = bool(getattr(message, "photo", None))
    if not text and not has_photo:
        return None
    if is_naive(posted_at):
        posted_at = make_aware(posted_at, dt_timezone.utc)
    sender = getattr(message, "sender", None)
    author_username = ((getattr(sender, "username", None) or "") if sender is not None else "").strip().lstrip("@")
    if author_username.lower() == (public_username or username).lower():
        author_username = ""
    first = (getattr(sender, "first_name", None) or "") if sender is not None else ""
    last = (getattr(sender, "last_name", None) or "") if sender is not None else ""
    author_name = " ".join(part for part in (first.strip(), last.strip()) if part)
    if public_username:
        link = f"https://t.me/{public_username.lstrip('@')}/{int(message_id)}"
    elif chat_id:
        link = f"https://t.me/c/{int(chat_id)}/{int(message_id)}"
    else:
        link = f"https://t.me/{username}/{int(message_id)}"
    grouped = getattr(message, "grouped_id", None)
    sender_id = getattr(message, "sender_id", None)
    return {
        "telegram_message_id": int(message_id),
        "posted_at": posted_at,
        "text": text,
        "views": getattr(message, "views", None),
        "has_photo": has_photo,
        "grouped_id": int(grouped) if grouped else None,
        "author_id": int(sender_id) if sender_id else None,
        "author_username": author_username[:64],
        "author_name": author_name[:128],
        "source_url": link,
        **read_poster(message, chat_id=chat_id),
    }


def pick_thumb(photo) -> str | None:
    """Type letter of the largest size up to WANTED_PX on its long edge, else the smallest above it."""
    sizes = []
    for size in getattr(photo, "sizes", None) or []:
        width = getattr(size, "w", None)
        height = getattr(size, "h", None)
        kind = getattr(size, "type", None)
        if kind and width and height:
            sizes.append((max(width, height), kind))
    if not sizes:
        return None
    sizes.sort()
    within = [item for item in sizes if item[0] <= WANTED_PX]
    return (within[-1] if within else sizes[0])[1]


class HomeHistoryReader(HistoryReader):
    def __init__(self) -> None:
        super().__init__()
        # Loaded here, in sync code: the Telegram calls below run inside an event loop where the ORM is not allowed.
        self._database_invites = database_invites()
        self._peers = cached_peers()
        self._lookups = 0
        # Addresses found or found broken in this run. The caller stores them, outside the event loop.
        self.learned: dict[str, tuple[str, int, int | None, str]] = {}
        self.stale: set[str] = set()

    def needs_lookup(self, username: str) -> bool:
        return username not in self._peers and username not in self.learned

    def lookups_left(self) -> int:
        return max(0, MAX_LOOKUPS_PER_RUN - self._lookups)

    async def _resolve(self, username: str):
        cached = self._peers.get(username)
        if cached is not None:
            from telethon.tl.types import InputPeerChannel, InputPeerChat

            kind, peer_id, access_hash, public = cached
            self._labels[username] = (public, int(peer_id))
            return InputPeerChannel(int(peer_id), int(access_hash or 0)) if kind == "channel" else InputPeerChat(int(peer_id))
        if self._lookups >= MAX_LOOKUPS_PER_RUN:
            self._errors[username] = LOOKUP_DEFERRED
            return None
        self._lookups += 1
        entity = await super()._resolve(username)
        if entity is not None:
            from telethon.tl.types import Channel

            public, chat_id = self._labels[username]
            if isinstance(entity, Channel) and getattr(entity, "access_hash", None) is not None:
                self.learned[username] = ("channel", int(chat_id), int(entity.access_hash), public)
            elif not isinstance(entity, Channel):
                self.learned[username] = ("chat", int(chat_id), None, public)
        return entity

    def _failed(self, username: str, exc: Exception) -> None:
        if type(exc).__name__ in STALE_PEER_ERRORS and username in self._peers:
            self._peers.pop(username, None)
            self.stale.add(username)

    def _invites(self) -> dict[str, str]:
        merged = dict(market_invite_sources())
        merged.update(home_invite_sources())
        merged.update(self._database_invites)
        return merged

    async def _read(self, username: str, entity, before: int | None) -> list:
        public_username, chat_id = self._labels[username]
        kwargs = {"limit": PAGE_SIZE}
        if before:
            kwargs["offset_id"] = int(before)
        messages = await asyncio.wait_for(
            self._client.get_messages(entity, **kwargs),
            timeout=PAGE_TIMEOUT_SECONDS,
        )
        rows = []
        for message in messages:
            raw = home_message_to_raw(username, message, public_username=public_username, chat_id=chat_id)
            if raw is not None:
                rows.append(raw)
        return rows

    def download_photo(self, username: str, message_id: int) -> bytes | None:
        """Bytes of one photo message, or None when it is gone or not a photo."""
        return self._loop.run_until_complete(self._download(username, message_id))

    async def _download(self, username: str, message_id: int) -> bytes | None:
        entity = await self._entity(username)
        if entity is None:
            return None
        message = await asyncio.wait_for(
            self._client.get_messages(entity, ids=int(message_id)), timeout=PAGE_TIMEOUT_SECONDS
        )
        if message is None or not getattr(message, "photo", None):
            return None
        thumb = pick_thumb(message.photo)
        data = await asyncio.wait_for(
            self._client.download_media(message, file=bytes, thumb=thumb),
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
        )
        return bytes(data) if data else None
