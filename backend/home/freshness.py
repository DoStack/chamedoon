"""Freshness and safety of housing listings.

* Relative time («۳ ساعت پیش») and the freshness line under a listing
  («🕒 ۳ ساعت پیش · ✅ هنوز موجود (دیروز تأیید شد)»).
* Expiry: a listing not seen again, nor confirmed, for N days (Home settings) expires and
  leaves the feeds, search and alerts. Expired listings keep opening from old links.
* «هنوز موجوده؟»: the housing bot asks the person who posted a listing in the Mini App,
  once per round, after `confirm_after_days`. Anyone can confirm a crawled listing.
* «اجاره رفت»: two different people, or the owner once, mark a listing rented; every
  channel post of it is edited to the rented template (home/status_posts.py).
* «🚩 گزارش»: one-tap reasons. Two scam reports hide the listing and queue it for review.
* The scam warning shown before contact: in full the first time per person, short after.

Reporters are counted by Telegram id, so a Mini App user and the same person in the bot
are one person.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from home import bot, conf
from home.models import (
    HomeListing,
    HomeReport,
    HomeSafetySeen,
    ListingKind,
    ListingStatus,
    ReportReason,
    StatusPost,
)

logger = logging.getLogger(__name__)

APP_PREFIX = "app_"
TAKEN_REPORTS_TO_RENT = 2
SCAM_REPORTS_TO_HIDE = 2
OWNER_BATCH = 20
PER_MESSAGE_RESERVE_SECONDS = 3
PROBLEM_REASONS = (ReportReason.SCAM, ReportReason.WRONG, ReportReason.DUPLICATE, ReportReason.OTHER)
# Statuses a «اجاره رفت» can still change: an expired listing can still be rented out, a reported one waits for an admin.
RENTABLE = (ListingStatus.ACTIVE, ListingStatus.EXPIRED)

PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

SCAM_WARNING_FA = (
    "⚠️ قبل از دیدن خانه و امضای قرارداد هیچ پولی (ودیعه، رزرو، کارت هدیه) نفرستید. "
    "ما آگهی را جمع کرده‌ایم و صاحبش را تأیید نکرده‌ایم.\n"
    "اگر کسی پیش از بازدید پول خواست یا گفت خارج از کشور است و کلید را پست می‌کند، "
    "احتمالاً کلاهبرداری است؛ با «🚩 گزارش» به ما خبر بدهید."
)
SCAM_SHORT_FA = "⚠️ قبل از دیدن خانه پولی نفرستید."
SCAM_WARNING_EN = (
    "⚠️ Do not send any money (deposit, holding fee, gift cards) before you have seen the place and signed a lease. "
    "We collected this listing and have not verified who posted it.\n"
    "If someone asks for money before a viewing, or says they are abroad and will mail the keys, "
    "it is most likely a scam: tell us with «Report»."
)
SCAM_SHORT_EN = "⚠️ Never send money before you have seen the place."

_WORDS = {
    "fa": {
        "now": "همین الان",
        "minutes": "{n} دقیقه پیش",
        "hours": "{n} ساعت پیش",
        "yesterday": "دیروز",
        "days": "{n} روز پیش",
        "confirmed": "✅ هنوز موجود ({when} تأیید شد)",
    },
    "en": {
        "now": "just now",
        "minutes": "{n} min ago",
        "hours": "{n} h ago",
        "yesterday": "yesterday",
        "days": "{n} days ago",
        "confirmed": "✅ still available (confirmed {when})",
    },
}


# ---------------------------------------------------------------------------
# Relative time.


def _number(value: int, locale: str) -> str:
    return str(value).translate(PERSIAN_DIGITS) if locale == "fa" else str(value)


def relative_time(when: datetime | None, *, now: datetime | None = None, locale: str = "fa") -> str:
    """«همین الان»، «۸ دقیقه پیش»، «۳ ساعت پیش»، «دیروز»، «۴ روز پیش» (or the English words)."""
    if when is None:
        return ""
    words = _WORDS["en" if locale == "en" else "fa"]
    locale = "en" if locale == "en" else "fa"
    seconds = ((now or timezone.now()) - when).total_seconds()
    if seconds < 60:
        return words["now"]
    minutes = int(seconds // 60)
    if minutes < 60:
        return words["minutes"].replace("{n}", _number(minutes, locale))
    hours = minutes // 60
    if hours < 24:
        return words["hours"].replace("{n}", _number(hours, locale))
    days = hours // 24
    if days == 1:
        return words["yesterday"]
    return words["days"].replace("{n}", _number(days, locale))


def confirmed_line(listing: HomeListing, *, now: datetime | None = None, locale: str = "fa") -> str:
    if not listing.last_confirmed_at or listing.status != ListingStatus.ACTIVE:
        return ""
    words = _WORDS["en" if locale == "en" else "fa"]
    return words["confirmed"].replace("{when}", relative_time(listing.last_confirmed_at, now=now, locale=locale))


def freshness_line(listing: HomeListing, *, now: datetime | None = None, locale: str = "fa") -> str:
    """«🕒 ۳ ساعت پیش · ✅ هنوز موجود (دیروز تأیید شد)»; the second part only when someone confirmed it."""
    parts = [f"🕒 {relative_time(listing.last_seen, now=now, locale=locale)}"]
    confirmed = confirmed_line(listing, now=now, locale=locale)
    if confirmed:
        parts.append(confirmed)
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Who is reporting.


def is_app_listing(listing: HomeListing) -> bool:
    return listing.post.source.username.startswith(APP_PREFIX)


def is_owner(listing: HomeListing, telegram_id: int | None) -> bool:
    """The person who posted it in the Mini App (or bot). Crawled listings have no owner here."""
    return bool(telegram_id) and is_app_listing(listing) and listing.post.author_id == int(telegram_id)


def _user_for(telegram_id: int):
    from home.alerts import find_user

    return find_user(telegram_id)


def record(listing: HomeListing, reason: str, telegram_id: int, user=None) -> bool:
    """One row per person, listing and reason. Returns True when it is new."""
    if user is None:
        user = _user_for(telegram_id)
    found = HomeReport.objects.filter(listing=listing, reason=reason).filter(
        Q(telegram_user_id=telegram_id) | (Q(user=user) if user is not None else Q(pk__in=[]))
    )
    if found.exists():
        return False
    try:
        with transaction.atomic():
            HomeReport.objects.create(listing=listing, reason=reason, telegram_user_id=telegram_id, user=user)
    except IntegrityError:
        return False
    return True


def reporters(listing: HomeListing, reason: str) -> int:
    """Different people behind one reason: the Telegram id, or the account of an old row without one."""
    people = set()
    for telegram_id, user_id in HomeReport.objects.filter(listing=listing, reason=reason).values_list("telegram_user_id", "user_id"):
        people.add(("t", telegram_id) if telegram_id else ("u", user_id))
    return len(people)


# ---------------------------------------------------------------------------
# Rented, still available, reported.


def set_rented(listing: HomeListing) -> bool:
    """Mark a listing rented and edit its channel posts. Returns False when it was not rentable."""
    from home import status_posts

    updated = HomeListing.objects.filter(pk=listing.pk, status__in=RENTABLE).update(
        status=ListingStatus.RENTED, rented_at=timezone.now(), updated_at=timezone.now()
    )
    if not updated:
        return False
    listing.refresh_from_db()
    status_posts.apply(listing, StatusPost.RENTED)
    return True


def mark_taken(listing: HomeListing, telegram_id: int, user=None) -> str:
    """«اجاره رفت» from one person. Returns rented, noted or closed (already rented or hidden)."""
    if listing.status not in RENTABLE:
        return "closed"
    record(listing, ReportReason.TAKEN, telegram_id, user)
    if is_owner(listing, telegram_id) or reporters(listing, ReportReason.TAKEN) >= TAKEN_REPORTS_TO_RENT:
        return "rented" if set_rented(listing) else "closed"
    return "noted"


def confirm_available(listing: HomeListing, telegram_id: int, user=None) -> bool:
    """«هنوز موجوده» from the owner or anyone who checked. Only an active listing can be confirmed."""
    if listing.status != ListingStatus.ACTIVE:
        return False
    record(listing, ReportReason.AVAILABLE, telegram_id, user)
    listing.last_confirmed_at = timezone.now()
    listing.save(update_fields=["last_confirmed_at", "updated_at"])
    return True


def report(listing: HomeListing, reason: str, telegram_id: int, user=None) -> str:
    """«🚩 گزارش» with a reason. Returns hidden, noted or ignored.

    Two different people saying scam hide the listing (status reported), queue it for
    review and edit its channel posts. Other reasons are kept for the admin list.
    The older «problem» button of the Mini App still sends the listing to review at once.
    """
    if reason == ReportReason.PROBLEM:
        record(listing, reason, telegram_id, user)
        listing.needs_review = True
        listing.review_note = "reported by a Mini App user"
        listing.save(update_fields=["needs_review", "review_note", "updated_at"])
        return "noted"
    if reason not in PROBLEM_REASONS:
        return "ignored"
    record(listing, reason, telegram_id, user)
    if reason != ReportReason.SCAM:
        return "noted"
    count = reporters(listing, ReportReason.SCAM)
    if count < SCAM_REPORTS_TO_HIDE or listing.status not in RENTABLE:
        return "noted"
    from home import status_posts

    listing.status = ListingStatus.REPORTED
    listing.needs_review = True
    listing.review_note = f"reported as a scam by {count} people"
    listing.save(update_fields=["status", "needs_review", "review_note", "updated_at"])
    status_posts.apply(listing, StatusPost.REPORTED)
    return "hidden"


def restore(listing: HomeListing) -> None:
    """An admin found a reported listing fine: back to active, channel posts back to the original."""
    from home import status_posts

    listing.status = ListingStatus.ACTIVE
    listing.needs_review = False
    listing.review_note = ""
    listing.save(update_fields=["status", "needs_review", "review_note", "updated_at"])
    if listing.status_post:
        status_posts.apply(listing, StatusPost.RESTORED)


# ---------------------------------------------------------------------------
# Expiry.


def expire_stale(*, now: datetime | None = None) -> dict:
    """Active listings not seen again, nor confirmed, for the expiry days of their kind become expired."""
    now = now or timezone.now()
    out = {}
    for kind, days in ((ListingKind.OFFER, conf.expire_offer_days()), (ListingKind.WANTED, conf.expire_wanted_days())):
        cut = now - timedelta(days=days)
        out[f"expired_{kind}"] = (
            HomeListing.objects.filter(status=ListingStatus.ACTIVE, kind=kind, last_seen__lt=cut)
            .filter(Q(last_confirmed_at__isnull=True) | Q(last_confirmed_at__lt=cut))
            .update(status=ListingStatus.EXPIRED, updated_at=now)
        )
    return out


# ---------------------------------------------------------------------------
# «هنوز موجوده؟» to the owner.


def listing_title(listing: HomeListing) -> str:
    from home.publish import TYPE_FA, city_fa

    place = (listing.area or "").strip() or city_fa(listing.city)
    kind = TYPE_FA.get(listing.property_type, "ملک")
    return f"{kind} · {place}" if place else kind


def owner_question(listing: HomeListing) -> tuple[str, list]:
    from home.publish import esc

    text = (
        f"🏠 آگهی شما در چمدون خانه: <b>{esc(listing_title(listing))}</b>\n\n"
        "<b>هنوز موجوده؟</b>\n"
        "اگر اجاره رفته، با یک دکمه خبر بدهید تا کسی بی‌جهت با شما تماس نگیرد."
    )
    keyboard = [
        [{"text": "✅ بله، هنوز هست", "callback_data": f"hx:o:{listing.pk}:y"}],
        [{"text": "🏁 اجاره رفت", "callback_data": f"hx:o:{listing.pk}:r"}],
    ]
    return text, keyboard


def owners_to_ask(*, now: datetime | None = None):
    """Active Mini App listings whose last confirmation (or posting) is older than `confirm_after_days`
    and whose owner was not asked since then."""
    now = now or timezone.now()
    cut = now - timedelta(days=conf.confirm_after_days())
    return (
        HomeListing.objects.filter(
            status=ListingStatus.ACTIVE,
            post__source__username__startswith=APP_PREFIX,
            post__author_id__isnull=False,
        )
        .annotate(fresh_since=Coalesce("last_confirmed_at", "first_seen"))
        .filter(fresh_since__lt=cut)
        .filter(Q(confirm_asked_at__isnull=True) | Q(confirm_asked_at__lt=F("fresh_since")))
        .select_related("post")
        .order_by("fresh_since", "id")
    )


def ask_owners(*, limit: int = OWNER_BATCH, stop_at: float | None = None, now: datetime | None = None) -> dict:
    """DM each owner «هنوز موجوده؟» once per round. Never raises on Telegram trouble."""
    out = {"asked": 0, "failed": 0, "retry_after": 0}
    if not conf.bot_token():
        return {**out, "skipped": conf.BOT_TOKEN_MISSING}
    now = now or timezone.now()
    for listing in owners_to_ask(now=now)[:limit]:
        if stop_at is not None and time.monotonic() + PER_MESSAGE_RESERVE_SECONDS >= stop_at:
            break
        previous = listing.confirm_asked_at
        claimed = HomeListing.objects.filter(pk=listing.pk, confirm_asked_at=previous).update(confirm_asked_at=now)
        if not claimed:
            continue
        text, keyboard = owner_question(listing)
        try:
            bot.call(
                "sendMessage",
                {
                    "chat_id": listing.post.author_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "reply_markup": {"inline_keyboard": keyboard},
                },
            )
        except bot.BotError as exc:
            if exc.retry_after or exc.code == 429:
                HomeListing.objects.filter(pk=listing.pk).update(confirm_asked_at=previous)
                out["retry_after"] = exc.retry_after or 1
                break
            # Blocked the bot or never started it: counted as asked, so it is not retried every run.
            out["failed"] += 1
            logger.info("Home owner check for listing %s failed: %s", listing.pk, exc.description)
            continue
        out["asked"] += 1
    return out


# ---------------------------------------------------------------------------
# Scam warning before contact.


def contact_warning(telegram_id: int | None, *, locale: str = "fa", record_seen: bool = True) -> dict:
    """The warning shown before contact: in full the first time per person, a short line afterwards."""
    full = True
    if telegram_id:
        if record_seen:
            _row, full = HomeSafetySeen.objects.get_or_create(telegram_user_id=int(telegram_id))
        else:
            full = not HomeSafetySeen.objects.filter(telegram_user_id=int(telegram_id)).exists()
    if locale == "en":
        return {"full": full, "text": SCAM_WARNING_EN if full else SCAM_SHORT_EN, "short": SCAM_SHORT_EN, "long": SCAM_WARNING_EN}
    return {"full": full, "text": SCAM_WARNING_FA if full else SCAM_SHORT_FA, "short": SCAM_SHORT_FA, "long": SCAM_WARNING_FA}


def deposit_note(listing: HomeListing) -> str:
    """The provincial deposit rule (Ontario, Quebec) from the channel posts, shown before contact."""
    from home.publish import REGION_WARNING_FA

    return REGION_WARNING_FA.get(listing.region or "", "")


# ---------------------------------------------------------------------------
# Job step.


def run_freshness(*, stop_at: float | None = None) -> dict:
    """Expire stale listings, ask owners, and retry channel edits Telegram asked to retry later."""
    from home import status_posts

    out = {"ok": True, **expire_stale()}
    out["owners"] = ask_owners(stop_at=stop_at)
    out["status_posts"] = status_posts.retry_pending(stop_at=stop_at)
    return out
