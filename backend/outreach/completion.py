"""Ad completion (H4): what a poster's ad is missing, asked for in the chat, saved on that exact ad.

Who and which ad: a chat belongs to one Telegram id; only the ads that id owns (`home/ownership.py`) are touched. With
several live ads nothing is saved until the person says which one, named by city and price.

What is missing, by its effect on being seen and matched (production, 2026-11-04, 335 owner ads in the 6 cities):
photo 67%, rooms 39%, price 27%, neighbourhood 8%; then the move-in date, furnished, bills and deposit. Price comes
right after the photo because an ad without a price never matches an alert with a budget. The phone is never asked:
it becomes public, so it goes on the ad only when the person offers it and says yes (agent intent `phone`).

Asking: one or two things per message, never a form, no buttons. The only claim made is the true general one: ads
with photos and a price reach more people, and a priceless ad does not match a budget alert.

Paths:
- Operator (the agent is off): Admin → Outreach → Chats → a chat → «Ad completion», or `manage.py outreach_complete`.
  Each action prints the next line to send by hand.
- Agent (behind `agent_enabled`): asks after «هنوز هست», saves the answer, asks the next gap, then says it is done.
- No reply: one P1G follow-up 24 to 48 hours after an unanswered P1/P1q, only when "P1G" is in the enabled kinds,
  held for a yes like every message, from the same account and inside its limits. Never a second one.

What was filled is kept on the first message (`extra["completion"]`) for the funnel.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from home import conf as home_conf, deeplink
from home.alerts import city_fa
from home.models import Furnished, HomeListing, HomeMedia, ListingKind, ListingStatus, MediaStatus, PropertyType, Tri
from home.ownership import owned_listings, owns
from outreach import conf, templates
from outreach.models import OutreachConversation, OutreachKind, OutreachMessage, OutreachStatus

logger = logging.getLogger(__name__)

GAPS = ("photo", "price", "rooms", "area", "available_from", "furnished", "bills", "deposit")
# Done means these are there. The rest are asked once if the person is still answering.
CORE = ("photo", "price", "rooms", "area")
FIELDS = tuple(gap for gap in GAPS if gap != "photo")
PER_MESSAGE = 1  # One question per message (Mohammad, 2026-11-06).
MAX_ROUNDS = 3
FOLLOWUP_AFTER = timedelta(hours=24)
FOLLOWUP_UNTIL = timedelta(hours=48)
# Built early enough that a daily build and a person's yes fit before the 48 hours end; never sent before 24.
FOLLOWUP_BUILD_FROM = timedelta(hours=10)
POSTER_FIRST_KINDS = (OutreachKind.P1, OutreachKind.P1Q)

WORDS = {
    "photo": "عکس",
    "price": "قیمت",
    "rooms": "تعداد اتاق",
    "area": "محله",
    "available_from": "تاریخ خالی شدن",
    "furnished": "مبله بودن",
    "bills": "قبوض",
    "deposit": "ودیعه",
}
QUESTIONS = {
    "photo": "اگه چند تا عکس از خونه دارید همین‌جا بفرستید، خودم به آگهی اضافه می‌کنم.",
    "price": "قیمت ماهانه‌ش چقدره؟",
    "rooms": "چند تا اتاق خواب داره؟",
    "area": "تو کدوم محله‌ست؟",
    "available_from": "از کی خالی می‌شه؟",
    "furnished": "مبله‌ست؟",
    "bills": "قبوض تو اجاره حساب شده یا جداست؟",
    "deposit": "ودیعه‌ش چقدره؟",
}
BENEFIT = {
    frozenset({"photo", "price"}): "آگهی عکس‌دار و قیمت‌دار رو آدمای بیشتری می‌بینن، و آگهی بی‌قیمت به کسایی که با بودجه‌ی مشخص دنبال خونه‌ان نشون داده نمی‌شه.",
    frozenset({"photo"}): "آگهی‌های عکس‌دار رو آدمای بیشتری می‌بینن.",
    frozenset({"price"}): "آگهی بی‌قیمت به کسایی که با بودجه‌ی مشخص دنبال خونه‌ان نشون داده نمی‌شه.",
}
DONE = "ممنون، آگهی‌تون الان کامل شد:"
EDIT_LINE = "اگه راحت‌ترید، از این لینک وارد بشید و «ویرایش آگهی» رو بزنید:"


# ---------------------------------------------------------------- what is missing


def has_photo(listing: HomeListing) -> bool:
    return HomeMedia.objects.filter(post_id=listing.post_id, status=MediaStatus.STORED).exists()


def gaps(listing: HomeListing) -> list[str]:
    """What this offer is missing, most important first. Real columns only."""
    if listing.kind != ListingKind.OFFER:
        return []
    found = []
    if not has_photo(listing):
        found.append("photo")
    if listing.price_amount is None:
        found.append("price")
    if listing.rooms is None and listing.property_type not in (PropertyType.ROOM, PropertyType.STUDIO) and not open_plan(listing):
        found.append("rooms")
    if not (listing.area or "").strip() and listing.home_area_id is None:
        found.append("area")
    if listing.available_from is None:
        found.append("available_from")
    if listing.furnished == Furnished.UNKNOWN:
        found.append("furnished")
    if listing.bills_included == Tri.UNKNOWN:
        found.append("bills")
    if listing.deposit_amount is None and listing.deposit_months is None and deposit_applies(listing):
        found.append("deposit")
    return found


LEASE_TRANSFER = ("انتقال لیز", "انتقال_لیز", "واگذاری لیز", "واگذاری_لیز", "lease transfer", "lease takeover", "transfert de bail", "cession de bail")


def deposit_applies(listing: HomeListing) -> bool:
    """No deposit in Quebec (it is not allowed there) and none on a lease transfer (rule 17)."""
    if listing.country == "CA" and (metro_city(listing) or "").casefold() in {"montreal", "montréal", "quebec city", "gatineau", "laval"}:
        return False
    text = ((listing.post.text if listing.post_id else "") or "").casefold()
    return not any(word in text for word in LEASE_TRANSFER)


OPEN_PLAN = ("طراحی داخلی باز", "فضای باز", "فضای مخصوص خواب", "لافت", "loft", "استودیو", "studio", "open plan", "open-plan", "1 zimmer", "1-zimmer")


def open_plan(listing: HomeListing) -> bool:
    """A loft or an open plan has no bedroom count to ask (audit 2026-11-09, conv 23)."""
    text = ((listing.post.text if listing.post_id else "") or "").casefold()
    return any(word in text for word in OPEN_PLAN)


def core_gaps(listing: HomeListing) -> list[str]:
    return [gap for gap in gaps(listing) if gap in CORE]


def live_ads(telegram_id: int) -> list[HomeListing]:
    """This person's live offers, newest first: the only ads completion may touch."""
    return list(
        owned_listings(telegram_id).filter(kind=ListingKind.OFFER, status=ListingStatus.ACTIVE, canonical__isnull=True).order_by("-post__posted_at", "-pk")
    )


def own_ad(telegram_id: int, listing_id) -> HomeListing | None:
    """The ad, only when it is a live offer of this very person."""
    try:
        pk = int(listing_id)
    except (TypeError, ValueError):
        return None
    return next((listing for listing in live_ads(telegram_id) if listing.pk == pk), None)


# ---------------------------------------------------------------- words


def metro_city(listing: HomeListing) -> str:
    from outreach.cities import metro_of_listing

    channel = metro_of_listing(listing)
    return channel.city if channel else listing.city


def label(listing: HomeListing, others: list[HomeListing] | None = None) -> str:
    """«تورنتو، ۱۸۰۰ دلار» or «تورنتو، بدون قیمت»; the neighbourhood is added when two would read the same."""
    money = templates.money(listing.price_amount, listing.price_currency) if listing.price_amount else "بدون قیمت"
    text = f"{city_fa(metro_city(listing)) or listing.city}، {money}"
    same = [other for other in (others or []) if other.pk != listing.pk and label(other) == text]
    if same and (listing.area or "").strip():
        text = f"{text}، {listing.area.strip()}"
    return text


def which_text(listings: list[HomeListing]) -> str:
    names = " یا ".join(f"«{label(item, listings)}»" for item in listings[:3])
    return f"کدوم آگهی‌تون؟ {names}"


def _question(gap: str, listing: HomeListing | None) -> str:
    """A short stay has no monthly rent: ask its price plainly (audit 2026-11-08, T83)."""
    if gap == "price" and listing is not None and listing.rental_mode == "short_term":
        return "اجاره‌ش چقدره؟"
    return QUESTIONS[gap]


def ask_text(asked: list[str], listing: HomeListing | None = None) -> str:
    """One or two gaps in one natural message: what is missing, the question, and the one true benefit line."""
    asked = asked[:PER_MESSAGE]
    if not asked:
        return ""
    missing = " و ".join(WORDS[gap] for gap in asked)
    parts = [f"تو آگهی‌تون {missing} نیومده بود.", *(_question(gap, listing) for gap in asked)]
    benefit = BENEFIT.get(frozenset(gap for gap in asked if gap in ("photo", "price")))
    if benefit:
        parts.append(benefit)
    return " ".join(parts)


def saved_text(listing: HomeListing, filled: list[str]) -> str:
    """Line 1 after a saved answer names what was saved, as it now is on the ad (audit 2026-11-09, conv 23 rule 44)."""
    if len(filled) != 1:
        return "ممنون، همه رو تو آگهی ثبت کردم."
    field = filled[0]
    if field == "rooms" and listing.property_type == PropertyType.STUDIO and listing.rooms is None:
        what = "استدیو بودنش"
    elif field == "rooms" and listing.rooms is not None:
        what = f"{templates.fa_number(int(listing.rooms) if listing.rooms == int(listing.rooms) else listing.rooms)} خوابه بودنش"
    elif field == "price" and listing.price_amount:
        what = f"قیمت {templates.money(listing.price_amount, listing.price_currency)}"
    elif field == "area" and (listing.area or "").strip():
        what = f"محله‌ی {listing.area.strip()}"
    else:
        what = WORDS[field]
    return f"ممنون، {what} رو تو آگهی ثبت کردم."


def retry_text(field: str, listing: HomeListing | None = None) -> str:
    """Their answer could not be saved: ask the same thing once more, plainly, with no link (conv 23)."""
    return f"ببخشید، دقیق متوجه نشدم؛ {_question(field, listing)}"


def view_link(listing: HomeListing, token: str = "") -> str:
    """The ad in the Mini App, with its city and the outreach token. Its owner sees «ویرایش آگهی» on it (`edit_listing`)."""
    return home_conf.mini_app_link(start=deeplink.build(listing.country, metro_city(listing), listing=listing.pk, source=f"o-{token}" if token else ""))


def edit_text(listing: HomeListing, token: str = "") -> str:
    return f"{EDIT_LINE}\n{templates.link_line(view_link(listing, token))}"


def done_text(listing: HomeListing, token: str = "") -> str:
    return f"{DONE}\n{templates.link_line(view_link(listing, token))}"


# ---------------------------------------------------------------- the funnel record


def _record_message(conversation: OutreachConversation | None, listing: HomeListing) -> OutreachMessage | None:
    if conversation is not None and conversation.first_message_id:
        return conversation.first_message
    return OutreachMessage.objects.filter(listing=listing, kind__in=POSTER_FIRST_KINDS, status=OutreachStatus.SENT).order_by("-sent_at").first()


def record(conversation: OutreachConversation | None, listing: HomeListing, *, filled: list[str] = (), asked: list[str] = (), done: bool = False, by: str = "") -> None:
    message = _record_message(conversation, listing)
    if message is None:
        return
    extra = dict(message.extra or {})
    entry = dict(extra.get("completion") or {})
    entry["listing"] = listing.pk
    entry["filled"] = sorted(set(entry.get("filled") or []) | set(filled))
    entry["asked"] = sorted(set(entry.get("asked") or []) | set(asked))
    if by:
        entry["by"] = by
    if done and not entry.get("done_at"):
        entry["done_at"] = timezone.now().isoformat()
    extra["completion"] = entry
    OutreachMessage.objects.filter(pk=message.pk).update(extra=extra, updated_at=timezone.now())
    message.extra = extra


def _token(conversation: OutreachConversation | None) -> str:
    first = conversation.first_message if conversation is not None else None
    return first.token if first is not None else ""


# ---------------------------------------------------------------- the chat


def next_line(conversation: OutreachConversation, listing: HomeListing, *, mark: bool = True) -> str:
    """What to say next about this ad: the next one or two gaps, or the thanks with its link when nothing core is left.

    `mark` remembers what was asked (awaiting, rounds) on the conversation; the caller saves it.
    """
    hooks = list(conversation.hooks_done or [])
    left = gaps(listing)
    if not any(gap in CORE for gap in left):
        if mark:
            conversation.hooks_done = [*hooks, "complete"] if "complete" not in hooks else hooks
            conversation.awaiting = {}
            record(conversation, listing, done=True)
        return done_text(listing, _token(conversation))
    if hooks.count("field") >= MAX_ROUNDS:
        return ""
    if _sent_video(conversation):
        # They have a video, not photos: take it and never ask for photos again.
        left = [gap for gap in left if gap != "photo"]
        if not left:
            return ""
    asked = left[:PER_MESSAGE]
    if mark:
        conversation.hooks_done = [*hooks, "field"]
        fields = [gap for gap in asked if gap != "photo"]
        conversation.awaiting = {"listing": listing.pk, "fields": asked, **({"field": fields[0]} if fields else {})}
        record(conversation, listing, asked=asked)
    return ask_text(asked, listing)


VIDEO_WORDS = ("فیلم", "ویدیو", "ویدئو", "video")


def _sent_video(conversation: OutreachConversation) -> bool:
    """They sent or spoke of a video, or sent photos in this chat: never ask them for photos again (audit 2026-11-09,
    conv 22: «تو آگهی‌تون عکس نیومده بود» after two photos were sent)."""
    turns = conversation.turns.filter(direction="in")
    if turns.filter(has_photo=True).exists() or any((detail or {}).get("media") == "video" for detail in turns.values_list("action_detail", flat=True)):
        return True
    return any(word in (text or "").lower() for text in turns.values_list("text", flat=True) for word in VIDEO_WORDS)


@dataclass
class Applied:
    ok: bool
    error: str = ""
    listing: HomeListing | None = None
    filled: list[str] | None = None
    next: str = ""


def apply_values(conversation: OutreachConversation, listing_id, values: dict[str, str], *, by: str) -> Applied:
    """Save one or more fields on one ad of the chat's own person, then say what comes next."""
    from outreach import actions

    listing = own_ad(conversation.telegram_user_id, listing_id)
    if listing is None:
        return Applied(False, "not_their_ad")
    filled = []
    for field, value in values.items():
        if field in FIELDS and str(value or "").strip() and actions.fill_field(listing, field, str(value)):
            filled.append(field)
    if not filled:
        return Applied(False, "value_not_understood", listing)
    listing.refresh_from_db()
    record(conversation, listing, filled=filled, by=by)
    return Applied(True, "", listing, filled, next_line(conversation, listing))


def attach_photos(conversation: OutreachConversation, listing_id, turn_ids: list[int] | None = None, *, by: str) -> Applied:
    """Join the chat's stored photos (all held ones, or the given turns) to one ad of the chat's own person."""
    from home.storage import get_storage
    from outreach.photos import HOUSING, attach_turn_photo, check_photo, held_photos

    listing = own_ad(conversation.telegram_user_id, listing_id)
    if listing is None:
        return Applied(False, "not_their_ad")
    turns = [turn for turn in held_photos(conversation) if turn_ids is None or turn.pk in turn_ids]
    if by == "agent":
        # No person looked: only photos the check calls housing; the rest keep waiting for the operator.
        storage = get_storage()
        turns = [turn for turn in turns if not (turn.action_detail or {}).get("photo_dropped") and storage is not None and check_photo(storage.url(turn.photo_key)) == HOUSING]
    if not turns:
        return Applied(False, "no_photos", listing)
    attached = 0
    error = ""
    for turn in turns:
        result = attach_turn_photo(turn, listing)
        attached += result == ""
        error = error or result
    if not attached:
        return Applied(False, error or "not_attached", listing)
    listing.refresh_from_db()
    refresh_channel_posts(listing)
    record(conversation, listing, filled=["photo"], by=by)
    return Applied(True, "", listing, ["photo"], next_line(conversation, listing))


def finish(conversation: OutreachConversation, listing_id, *, by: str) -> Applied:
    """The operator says it is done: the thanks and the view link, recorded as done."""
    listing = own_ad(conversation.telegram_user_id, listing_id)
    if listing is None:
        return Applied(False, "not_their_ad")
    conversation.hooks_done = [*(conversation.hooks_done or []), "complete"]
    conversation.awaiting = {}
    record(conversation, listing, done=True, by=by)
    return Applied(True, "", listing, [], done_text(listing, _token(conversation)))


def start_line(conversation: OutreachConversation) -> str:
    """The operator's first line about the ad: which ad, or the first gaps."""
    ads = live_ads(conversation.telegram_user_id)
    if not ads:
        return ""
    if len(ads) > 1:
        conversation.awaiting = {"which": "complete", "listings": [item.pk for item in ads[:3]]}
        return which_text(ads)
    return next_line(conversation, ads[0])


# ---------------------------------------------------------------- the channel post


def refresh_channel_posts(listing: HomeListing) -> None:
    """New photos on a published ad: a city post that went out without them is taken down and posted again by the
    publisher, which keeps each channel's daily cap. Only when the publisher will take it back (in its window, or
    claimed by its owner); otherwise the post stays and its text is refreshed. The country post is only edited."""
    from home import bot, status_posts
    from home import conf as home_settings
    from home.models import HomePublication, StatusPost

    first_photo = HomeMedia.objects.filter(post_id=listing.post_id, status=MediaStatus.STORED).order_by("created_at").values_list("created_at", flat=True).first()
    if first_photo is None:
        return
    in_window = listing.owner_claimed_at is not None or listing.last_seen >= timezone.now() - timedelta(days=home_settings.publish_window_days())
    edited = False
    for publication in HomePublication.objects.filter(listing=listing, published_at__lt=first_photo).select_related("channel"):
        if not in_window:
            continue
        removed = True
        for message_id in [int(item) for item in (publication.message_ids or []) if item]:
            try:
                bot.call("deleteMessage", {"chat_id": publication.channel.target(), "message_id": message_id})
            except bot.BotError as exc:
                logger.info("completion_republish_delete_failed listing=%s error=%s", listing.pk, exc.description)
                removed = False
        if removed:
            publication.delete()
        else:
            edited = True
    if edited or (listing.published_at and listing.published_at < first_photo):
        status_posts.apply(listing, StatusPost.RESTORED)


# ---------------------------------------------------------------- one follow-up without a reply (P1G)


def followup_text(message: OutreachMessage, listing: HomeListing) -> str:
    """Greeting, what the ad still lacks (one or two things), the ask, the true benefit, and the edit link."""
    rng = random.Random(message.token)
    name = templates.valid_name(message.recipient_name, message.recipient_username)
    asked = core_gaps(listing)[:PER_MESSAGE] or gaps(listing)[:PER_MESSAGE]
    missing = " و ".join(WORDS[gap] for gap in asked)
    asks = []
    if "photo" in asked:
        asks.append("اگه چند تا عکس از خونه همین‌جا بفرستید، خودم به آگهی اضافه می‌کنم.")
    fields = [gap for gap in asked if gap != "photo"]
    if fields:
        asks.append(f"{' و '.join(WORDS[gap] for gap in fields)} رو هم بگید، ثبتش می‌کنم." if "photo" in asked else f"{' و '.join(WORDS[gap] for gap in fields)} رو بگید، ثبتش می‌کنم.")
    lines = [f"{templates.greeting(name, rng)} آگهی خونه‌تون تو {city_fa(metro_city(listing))} که تو چمدون گذاشتیم هنوز {missing} نداره. {' '.join(asks)}"]
    benefit = BENEFIT.get(frozenset(gap for gap in asked if gap in ("photo", "price")))
    if benefit:
        lines.append(benefit)
    lines.append(edit_text(listing, message.token))
    return "\n".join(lines)


def _first_of(message: OutreachMessage) -> OutreachMessage | None:
    pk = (message.extra or {}).get("first")
    return OutreachMessage.objects.filter(pk=pk).first() if pk else None


def _replied(first: OutreachMessage) -> bool:
    if first.replied_at is not None:
        return True
    return bool(first.recipient_telegram_id) and OutreachConversation.objects.filter(telegram_user_id=first.recipient_telegram_id).exists()


def build_followups(result, *, now: datetime | None = None, dry_run: bool = False) -> None:
    """One P1G per person, ever, after an unanswered P1/P1q about an ad that still has core gaps."""
    from outreach.housing import listing_live
    from outreach.services import is_opted_out, new_message_status, new_token

    if OutreachKind.P1G not in conf.enabled_kinds():
        return
    now = now or timezone.now()
    firsts = (
        OutreachMessage.objects.filter(
            kind__in=POSTER_FIRST_KINDS,
            status=OutreachStatus.SENT,
            replied_at__isnull=True,
            stopped_at__isnull=True,
            member_at__isnull=True,
            sent_at__lte=now - FOLLOWUP_BUILD_FROM,
            sent_at__gt=now - FOLLOWUP_UNTIL,
            recipient_telegram_id__isnull=False,
            listing__isnull=False,
            account__isnull=False,
        )
        .select_related("listing", "listing__post", "listing__post__source", "account")
        .order_by("sent_at")
    )
    for first in firsts:
        telegram_id = first.recipient_telegram_id
        if OutreachMessage.objects.filter(kind=OutreachKind.P1G, recipient_telegram_id=telegram_id).exists():
            continue
        listing = first.listing
        if _replied(first) or is_opted_out(username=first.recipient_username, telegram_user_id=telegram_id):
            continue
        if not listing_live(listing) or not owns(listing, telegram_id) or not core_gaps(listing):
            continue
        token = new_token()
        message = OutreachMessage(
            kind=OutreachKind.P1G,
            recipient_username=first.recipient_username,
            recipient_name=first.recipient_name,
            recipient_telegram_id=telegram_id,
            listing=listing,
            country=first.country,
            city=first.city,
            source_at=first.source_at,
            variant="a",
            extra={"first": first.pk, "gaps": core_gaps(listing)[:PER_MESSAGE]},
            account=first.account,
            token=token,
        )
        message.text = followup_text(message, listing)
        message.status = new_message_status()
        message.error_code = "awaiting_yes" if message.status == OutreachStatus.HELD else ""
        if not dry_run:
            message.save()
        result.created.append(message)


def followup_due(message: OutreachMessage, now: datetime) -> bool:
    """Not before 24 hours after the first message."""
    first = _first_of(message)
    return first is not None and first.sent_at is not None and now >= first.sent_at + FOLLOWUP_AFTER


def followup_skip_reason(message: OutreachMessage, now: datetime) -> str:
    from outreach.housing import listing_live
    from outreach.services import is_opted_out

    first = _first_of(message)
    if first is None or first.sent_at is None:
        return "no_first_message"
    if now > first.sent_at + FOLLOWUP_UNTIL:
        return "stale"
    if _replied(first):
        return "replied"
    if first.member_at is not None:
        return "member"
    if first.stopped_at is not None or is_opted_out(username=message.recipient_username, telegram_user_id=message.recipient_telegram_id):
        return "opted_out"
    if message.listing is None or not listing_live(message.listing):
        return "listing_closed"
    if not owns(message.listing, message.recipient_telegram_id):
        return "not_owner"
    if not core_gaps(message.listing):
        return "complete"
    return ""


def followup_render(message: OutreachMessage) -> str:
    """Today's gaps: something filled in the meantime is not asked."""
    if message.listing is None:
        return message.text
    message.extra = {**(message.extra or {}), "gaps": core_gaps(message.listing)[:PER_MESSAGE]}
    return followup_text(message, message.listing)
