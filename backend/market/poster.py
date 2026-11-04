"""Who posted a crawled message, as Telegram says it. Used by the housing and the courier readers.

Only Telegram fills these fields. A review model never sees them and never writes them. The username here is not a
contact on its own: only a real person's account counts, so read it through `poster_contact`.
"""

from __future__ import annotations

import re

from django.db import models
from django.utils import timezone

KIND_PERSON = "person"
KIND_BOT = "bot"
KIND_CHANNEL = "channel"
KIND_ANONYMOUS = "anonymous"
KIND_UNKNOWN = "unknown"

VIA_SENDER = "sender"
VIA_FORWARD_AUTHOR = "forward_author"
VIA_FORWARDER = "forwarder"

# Telegram's own accounts that stand in for someone hidden: anonymous admins, old channel signatures, service messages.
STAND_IN_IDS = {1087968824, 136817688, 777000}
USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
POSTER_FIELDS = ("poster_user_id", "poster_username", "poster_name", "poster_kind", "poster_via", "poster_read_at")


class PosterKind(models.TextChoices):
    PERSON = KIND_PERSON, "A person"
    BOT = KIND_BOT, "A bot"
    CHANNEL = KIND_CHANNEL, "A channel or group"
    ANONYMOUS = KIND_ANONYMOUS, "Anonymous admin"
    UNKNOWN = KIND_UNKNOWN, "Telegram did not say"


class PosterVia(models.TextChoices):
    SENDER = VIA_SENDER, "Sent it"
    FORWARD_AUTHOR = VIA_FORWARD_AUTHOR, "Wrote the forwarded message"
    FORWARDER = VIA_FORWARDER, "Forwarded it, the author is hidden or not a person"


class PosterFields(models.Model):
    """On the raw crawled record. Never goes in a public answer, a snapshot or a channel post."""

    poster_user_id = models.BigIntegerField(null=True, blank=True, help_text="Telegram id. Negative for a channel or group.")
    poster_username = models.CharField(max_length=64, blank=True)
    poster_name = models.CharField(max_length=128, blank=True)
    poster_kind = models.CharField(max_length=16, choices=PosterKind.choices, blank=True, help_text="Empty: not worked out yet.")
    poster_via = models.CharField(max_length=16, choices=PosterVia.choices, blank=True)
    poster_read_at = models.DateTimeField(null=True, blank=True, help_text="When Telegram last told us. Empty: known only from the columns stored at crawl time.")

    class Meta:
        abstract = True


def _username(entity) -> str:
    name = (getattr(entity, "username", None) or "").strip().lstrip("@")
    if name:
        return name
    for extra in getattr(entity, "usernames", None) or []:
        if getattr(extra, "active", False) and getattr(extra, "username", None):
            return extra.username
    return ""


def _display(entity) -> str:
    title = (getattr(entity, "title", None) or "").strip()
    if title:
        return title
    parts = ((getattr(entity, "first_name", None) or "").strip(), (getattr(entity, "last_name", None) or "").strip())
    return " ".join(part for part in parts if part)


def _same_chat(sender_id: int | None, chat_id: int | None) -> bool:
    """Telethon marks a channel's id with a -100 prefix; the reader keeps the bare one."""
    if not sender_id or not chat_id or int(sender_id) > 0:
        return False
    return str(abs(int(sender_id))).removeprefix("100") == str(abs(int(chat_id))).removeprefix("100")


def _entity_kind(entity, entity_id: int | None) -> str:
    if entity_id in STAND_IN_IDS or getattr(entity, "id", None) in STAND_IN_IDS:
        return KIND_ANONYMOUS
    if getattr(entity, "title", None) or type(entity).__name__ in {"Channel", "Chat"}:
        return KIND_CHANNEL
    return KIND_BOT if getattr(entity, "bot", False) else KIND_PERSON


def _row(entity, entity_id: int | None, kind: str, via: str) -> dict:
    return {
        "poster_user_id": int(entity_id) if entity_id else None,
        "poster_username": _username(entity)[:64] if entity is not None else "",
        "poster_name": _display(entity)[:128] if entity is not None else "",
        "poster_kind": kind,
        "poster_via": via,
        "poster_read_at": timezone.now(),
    }


def read_poster(message, *, chat_id: int | None = None) -> dict:
    """The poster fields of one Telethon message. A forwarded post belongs to its author when that author is a person."""
    forward = getattr(message, "forward", None)
    if forward is not None:
        author = getattr(forward, "sender", None)
        author_id = getattr(forward, "sender_id", None)
        if author is not None and _entity_kind(author, author_id) == KIND_PERSON:
            return _row(author, author_id, KIND_PERSON, VIA_FORWARD_AUTHOR)
    via = VIA_FORWARDER if forward is not None else VIA_SENDER
    sender = getattr(message, "sender", None)
    sender_id = getattr(message, "sender_id", None)
    if _same_chat(sender_id, chat_id) or (sender_id is None and sender is None and chat_id):
        # A chat speaking as itself: a channel's own post, or an anonymous admin of a group.
        kind = KIND_CHANNEL if getattr(message, "post", False) else KIND_ANONYMOUS
        return _row(sender, sender_id, kind, via)
    if sender is None:
        return _row(None, sender_id, KIND_UNKNOWN, via)
    return _row(sender, sender_id, _entity_kind(sender, sender_id), via)


def poster_values(raw: dict) -> dict:
    """What a reader's row says about the poster, ready for a model. Empty for a row that holds none, like a web preview."""
    return {name: raw[name] for name in POSTER_FIELDS if name in raw}


def poster_contact(post, *, source_username: str = "") -> str:
    """The username to reach the poster on, or nothing. Only a real person's account: never a bot, channel or anonymous admin."""
    if post.poster_kind != KIND_PERSON:
        return ""
    name = (post.poster_username or "").strip().lstrip("@")
    if not USERNAME.fullmatch(name) or name.casefold().endswith("bot"):
        return ""
    return "" if source_username and name.casefold() == source_username.strip().lstrip("@").casefold() else name
