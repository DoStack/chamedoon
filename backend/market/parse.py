from __future__ import annotations

import re
from datetime import datetime, timezone as dt_timezone
from html import unescape
from typing import Any

from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive, make_aware

_POST_ID = re.compile(r'data-post="([^"/]+)/(\d+)"')
_TEXT = re.compile(
    r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*'
    r'(?:<div class="tgme_widget_message_|<div class="tgme_widget_message_footer)',
    re.S,
)
_TIME = re.compile(r'<time[^>]*datetime="([^"]+)"')
_VIEWS = re.compile(r'class="tgme_widget_message_views">([^<]+)')
_AUTHOR = re.compile(
    r'class="tgme_widget_message_owner_name"[^>]*href="https://t\.me/(?:s/)?([^"?]+)"[^>]*>'
    r'\s*<span[^>]*>([^<]+)',
    re.I,
)
_INLINE_BUTTON_HREF = re.compile(
    r'<a[^>]*class="[^"]*tgme_widget_message_inline_button[^"]*"[^>]*href="([^"]+)"'
    r'|<a[^>]*href="([^"]+)"[^>]*class="[^"]*tgme_widget_message_inline_button[^"]*"',
    re.I,
)
_TME_USER = re.compile(
    r"^(?:https?://)?(?:t\.me|telegram\.me)/(?!s/)(?P<user>[A-Za-z0-9_]{3,32})/?$",
    re.I,
)
_TG_RESOLVE = re.compile(r"^tg://resolve\?domain=(?P<user>[A-Za-z0-9_]{3,32})\b", re.I)


def strip_tags(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(re.sub(r"\n{3,}", "\n\n", text)).strip()


def parse_views(raw: str) -> int | None:
    value = (raw or "").strip().upper().replace(",", "")
    if not value:
        return None
    match = re.match(r"([0-9]*\.?[0-9]+)\s*([KMB])?", value)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    multiplier = {None: 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
    return int(number * multiplier)


def parse_posted_at(raw: str) -> datetime | None:
    if not raw:
        return None
    parsed = parse_datetime(raw.replace("Z", "+00:00"))
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if is_naive(parsed):
        return make_aware(parsed, dt_timezone.utc)
    return parsed


def parse_preview_html(html: str, *, default_username: str = "") -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    blocks = re.split(r'<div class="tgme_widget_message_wrap[^"]*">', html)
    for block in blocks[1:]:
        identity = _POST_ID.search(block)
        if not identity:
            continue
        username, message_id = identity.group(1), int(identity.group(2))
        text_match = _TEXT.search(block)
        time_match = _TIME.search(block)
        views_match = _VIEWS.search(block)
        author_match = _AUTHOR.search(block)
        posted_at = parse_posted_at(time_match.group(1) if time_match else "")
        if posted_at is None:
            continue
        channel = username or default_username
        owner_username = (author_match.group(1) if author_match else "").strip().lstrip("@")
        author_name = strip_tags(author_match.group(2)) if author_match else ""
        contact = contact_username_from_block(block, channel) or ""
        if not contact and owner_username and owner_username.lower() != channel.lower():
            contact = owner_username
        posts.append(
            {
                "channel_username": channel,
                "telegram_message_id": message_id,
                "posted_at": posted_at,
                "text": strip_tags(text_match.group(1)) if text_match else "",
                "views": parse_views(views_match.group(1) if views_match else ""),
                "has_photo": "tgme_widget_message_photo" in block,
                "author_username": contact[:64],
                "author_name": author_name[:128],
            }
        )
    return posts


def contact_username_from_block(block: str, channel: str = "") -> str:
    channel_l = (channel or "").strip().lstrip("@").lower()
    for match in _INLINE_BUTTON_HREF.finditer(block or ""):
        href = unescape((match.group(1) or match.group(2) or "").strip())
        handle = _username_from_href(href)
        if handle and handle.lower() != channel_l:
            return handle
    return ""


def _username_from_href(href: str) -> str:
    raw = (href or "").strip()
    if not raw:
        return ""
    path = raw.split("?", 1)[0].rstrip("/")
    user = _TME_USER.match(path)
    if user:
        return user.group("user")
    resolve = _TG_RESOLVE.match(raw)
    if resolve:
        return resolve.group("user")
    return ""
