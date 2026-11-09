"""The message a person shares from the Mini App into another chat: the main photo, the ad as a group post, a link back.

Same lines as a channel post (price, type and area, details), then the summary, then «برای اطلاعات بیشتر و تماس:» and the
listing's startapp link. Never a contact: the summary goes through `redact_contact`, and the phone and username stay behind
the join gate of the link.
"""

from __future__ import annotations

import html
import logging
import re

from home import bot, conf, deeplink
from home.channel_format import head_lines, quote_lines
from home.contact_text import known_handles, redact_contact
from home.feed import photos_for
from home.models import HomeListing
from home.publish import esc, metro_of

logger = logging.getLogger("home")

LINK_LINE = "برای اطلاعات بیشتر و تماس:"
CAPTION_LIMIT = 1024  # A photo's caption.
TEXT_LIMIT = 4096  # A text message.
_TAG = re.compile(r"<[^>]+>")


def share_link(listing: HomeListing) -> str:
    """Same link as the client's `shareLink`: src-share counts it apart (P0-16)."""
    metro = metro_of(listing)
    start = deeplink.build(listing.country, metro, listing=listing.pk, source="share") if metro else f"home_{listing.pk}"
    return conf.mini_app_link(start=start)


def share_html(listing: HomeListing, *, limit: int = TEXT_LIMIT) -> str:
    """The message in Telegram HTML. Over `limit`, the summary is cut first, then the detail lines, never the link."""
    metro = metro_of(listing)
    head = "\n".join(head_lines(listing, metro))
    details = [line for _priority, line in quote_lines(listing, metro)]
    summary = redact_contact(listing.summary_fa, known_handles(listing))
    tail = f"{LINK_LINE}\n{esc(share_link(listing))}"

    def join(summary_text: str) -> str:
        parts = [head]
        if details:
            parts.append("\n".join(details))
        if summary_text:
            parts.append(esc(summary_text))
        parts.append(tail)
        return "\n\n".join(parts)

    text = join(summary)
    if len(text) > limit and summary:
        room = limit - len(join("")) - 3
        text = join(summary[: max(0, room)].rstrip() + "…" if room > 20 else "")
    while len(text) > limit and details:
        details.pop()
        text = join("")
    return text


def plain(text: str) -> str:
    """The HTML message as plain text, for «کپی متن» and WhatsApp."""
    return html.unescape(_TAG.sub("", text))


def share_message(listing: HomeListing, telegram_user_id: int) -> dict:
    """The text to copy, the photos to save, and a prepared message for `WebApp.shareMessage` (Bot API 8.0).

    `prepared_id` is empty when Telegram refused: the client falls back to t.me/share/url with the same text.
    """
    photos = photos_for(listing)
    text = share_html(listing)
    prepared_id = ""
    if photos:
        result = {
            "type": "photo",
            "id": f"l{listing.pk}",
            "photo_url": photos[0]["full"],
            "thumbnail_url": photos[0]["thumb"],
            "caption": share_html(listing, limit=CAPTION_LIMIT),
            "parse_mode": "HTML",
        }
    else:
        result = {
            "type": "article",
            "id": f"l{listing.pk}",
            "title": plain(text.split("\n", 1)[0]),
            "input_message_content": {"message_text": text, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}},
        }
    try:
        answer = bot.call(
            "savePreparedInlineMessage",
            {
                "user_id": telegram_user_id,
                "result": result,
                # Every kind of chat: a person, a bot, a group, a channel.
                **{f"allow_{kind}_chats": True for kind in ("user", "bot", "group", "channel")},
            },
        )
        prepared_id = str((answer or {}).get("id") or "") if isinstance(answer, dict) else ""
    except bot.BotError as exc:
        logger.info("share_prepare_failed listing=%s error=%s", listing.pk, exc)
    return {
        "text": plain(text),
        "link": share_link(listing),
        "photos": [photo["full"] for photo in photos],
        "prepared_id": prepared_id,
    }
