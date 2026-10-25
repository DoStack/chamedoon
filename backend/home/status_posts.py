"""Edit the channel posts of a listing when its status changes: rented, reported, or restored.

Every post of the listing is edited, never deleted: the country channel's message ids
(`HomeListing.channel_message_ids`) and each city channel's (`HomePublication.message_ids`).

The Bot API cannot take the photo off a photo post, so a photo post keeps its photo and
gets the template as its caption; a text post gets it as its text. Either way the
buttons go. Under an album the buttons are a separate short reply (home/publish.py
`_send`); that reply is edited too, the album's other photos are left alone.

"Message is not modified" counts as done. A 429 (or any retry_after) keeps the listing
pending, and the housing job's freshness step tries again later. Any other refusal is
written to `status_post_error` and not retried.
"""

from __future__ import annotations

import logging
import time

from home import bot, conf
from home.guard import is_courier_channel
from home.models import HomeChannel, HomeListing, ReportReason, StatusPost

logger = logging.getLogger(__name__)

RETRY_BATCH = 20
PER_LISTING_RESERVE_SECONDS = 3
NO_TEXT = "no text in the message"


class RetryLater(Exception):
    def __init__(self, seconds: int) -> None:
        super().__init__(seconds)
        self.seconds = seconds


def _day_month(value) -> str:
    from home.publish import MONTHS, fa

    return f"{fa(value.day)} {MONTHS[value.month - 1]}"


def rented_text(listing: HomeListing) -> str:
    """«✅ اجاره رفت · <s>اتاق · ریچموند هیل</s>» and «۲ نفر گزارش دادند · ۲۶ اکتبر»."""
    from django.utils import timezone

    from home import freshness
    from home.publish import esc, fa

    title = esc(freshness.listing_title(listing))
    owner = listing.post.author_id
    by_owner = (
        freshness.is_app_listing(listing)
        and owner is not None
        and listing.reports.filter(reason=ReportReason.TAKEN, telegram_user_id=owner).exists()
    )
    who = "صاحب آگهی خبر داد" if by_owner else f"{fa(max(1, freshness.reporters(listing, ReportReason.TAKEN)))} نفر گزارش دادند"
    when = _day_month(timezone.localtime(listing.rented_at or timezone.now()))
    return f"✅ <b>اجاره رفت</b> · <s>{title}</s>\n{who} · {when}"


def reported_text(listing: HomeListing) -> str:
    from home import freshness
    from home.publish import esc

    title = esc(freshness.listing_title(listing))
    return (
        f"⚠️ <b>این آگهی گزارش شده</b> · <s>{title}</s>\n"
        "چند نفر این آگهی را مشکوک دانسته‌اند و در حال بررسی است. قبل از دیدن خانه هیچ پولی نفرستید."
    )


SHORT_LINE = {StatusPost.RENTED: "✅ اجاره رفت", StatusPost.REPORTED: "⚠️ این آگهی گزارش شده"}


def targets(listing: HomeListing) -> list[tuple[HomeChannel, list[int]]]:
    """Every channel post of a listing: the country channel's, then each city channel's."""
    found = []
    if listing.channel_id and listing.published_at:
        ids = [int(item) for item in (listing.channel_message_ids or []) if item] or (
            [int(listing.channel_message_id)] if listing.channel_message_id else []
        )
        if ids:
            found.append((listing.channel, ids))
    for publication in listing.publications.filter(published_at__isnull=False).select_related("channel"):
        ids = [int(item) for item in (publication.message_ids or []) if item]
        if ids:
            found.append((publication.channel, ids))
    return found


def _call(method: str, payload: dict) -> str:
    """One edit. Returns "" when done, NO_TEXT when the message has no text, else the refusal. Raises RetryLater."""
    try:
        bot.call(method, payload)
    except bot.BotError as exc:
        description = exc.description or ""
        if "not modified" in description:
            return ""
        if exc.retry_after or exc.code == 429:
            raise RetryLater(exc.retry_after or 5) from exc
        if NO_TEXT in description.lower() or "no caption" in description.lower():
            return NO_TEXT
        return description[:200] or "Telegram refused the edit"
    return ""


def edit_message(chat, message_id: int, text: str, caption: str, buttons: list | None, *, first: bool) -> str:
    """Edit one post: as text, else (a photo post) as caption. A later album photo is left alone."""
    markup = {"inline_keyboard": buttons or []}
    problem = _call(
        "editMessageText",
        {
            "chat_id": chat,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": markup,
        },
    )
    if problem != NO_TEXT:
        return problem
    if not first:
        return ""
    problem = _call(
        "editMessageCaption",
        {"chat_id": chat, "message_id": message_id, "caption": caption, "parse_mode": "HTML", "reply_markup": markup},
    )
    return "" if problem == NO_TEXT else problem


def _contents(listing: HomeListing, channel: HomeChannel) -> tuple[str, str, str, list | None, list | None]:
    """Text, caption and the short reply line of the current template, with buttons for the post and the reply."""
    from home import publish

    if listing.status_post == StatusPost.RESTORED:
        metro = publish.metro_of(listing, channel)
        buttons = publish.post_buttons(listing, channel, metro)
        text = publish.format_listing_text(listing, metro=metro)
        caption = publish.format_listing_text(listing, limit=publish.CAPTION_LIMIT, metro=metro)
        return text, caption, f"👆 {publish.DETAILS_BUTTON}", buttons, buttons
    body = rented_text(listing) if listing.status_post == StatusPost.RENTED else reported_text(listing)
    return body, body, SHORT_LINE.get(listing.status_post, body), None, None


def push(listing: HomeListing) -> dict:
    """Edit every channel post of the listing to its `status_post` template. Never raises on Telegram trouble."""
    out = {"edited": 0, "errors": 0, "retry_after": 0}
    if not listing.status_post:
        HomeListing.objects.filter(pk=listing.pk).update(status_post_pending=False)
        return out
    found = targets(listing)
    if not found:
        HomeListing.objects.filter(pk=listing.pk).update(status_post_pending=False, status_post_error="")
        return out
    if not conf.step_enabled("freshness"):
        # Switched off: the edit waits and goes out once freshness is back on.
        HomeListing.objects.filter(pk=listing.pk).update(status_post_pending=True)
        return {**out, "skipped": "freshness is switched off in Home settings"}
    if not conf.bot_token():
        HomeListing.objects.filter(pk=listing.pk).update(status_post_pending=True, status_post_error=conf.BOT_TOKEN_MISSING)
        return {**out, "skipped": conf.BOT_TOKEN_MISSING}
    problems = []
    try:
        for channel, ids in found:
            if is_courier_channel(channel.username, channel.chat_id):
                continue
            text, caption, short, buttons, reply_buttons = _contents(listing, channel)
            chat = channel.target()
            problem = edit_message(chat, ids[0], text, caption, buttons, first=True)
            if problem:
                problems.append(f"@{channel.username}/{ids[0]}: {problem}")
            else:
                out["edited"] += 1
            if len(ids) > 1:
                # The last id under an album is the reply with the buttons, when it went out.
                problem = edit_message(chat, ids[-1], short, short, reply_buttons, first=False)
                if problem:
                    problems.append(f"@{channel.username}/{ids[-1]}: {problem}")
    except RetryLater as exc:
        out["retry_after"] = exc.seconds
        HomeListing.objects.filter(pk=listing.pk).update(
            status_post_pending=True, status_post_error=f"Telegram asked to retry in {exc.seconds}s"
        )
        return out
    out["errors"] = len(problems)
    if problems:
        logger.warning("Home status edit for listing %s: %s", listing.pk, "; ".join(problems))
    HomeListing.objects.filter(pk=listing.pk).update(status_post_pending=False, status_post_error="; ".join(problems)[:300])
    return out


def apply(listing: HomeListing, template: str) -> dict:
    """Set the template of the listing's channel posts and edit them now; on 429 the job retries."""
    listing.status_post = template
    listing.status_post_pending = True
    listing.save(update_fields=["status_post", "status_post_pending", "updated_at"])
    return push(listing)


def retry_pending(*, limit: int = RETRY_BATCH, stop_at: float | None = None) -> dict:
    out = {"edited": 0, "errors": 0, "waiting": 0, "retry_after": 0}
    if not conf.bot_token():
        out["waiting"] = HomeListing.objects.filter(status_post_pending=True).count()
        return {**out, "skipped": conf.BOT_TOKEN_MISSING} if out["waiting"] else out
    rows = HomeListing.objects.filter(status_post_pending=True).select_related("post", "post__source", "channel").order_by("updated_at")
    for listing in rows[:limit]:
        if stop_at is not None and time.monotonic() + PER_LISTING_RESERVE_SECONDS >= stop_at:
            break
        result = push(listing)
        out["edited"] += result["edited"]
        out["errors"] += result["errors"]
        if result["retry_after"]:
            out["retry_after"] = result["retry_after"]
            break
    out["waiting"] = HomeListing.objects.filter(status_post_pending=True).count()
    return out
