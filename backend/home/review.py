"""AI review of one housing post: is it a real listing, and what does it say.

One call decides both. The model sees the raw post and the source's defaults and
returns JSON. Everything it returns is checked here before it reaches the
database: enums, dates, money ranges, and the country/currency pair. A value the
checks cannot trust puts the listing in the review queue instead of publishing
it with a guess. Exact reposts are caught first, without a model call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Max, Min, Q
from django.utils import timezone

from ai.llm import complete, llm_enabled
from home.images import same_photo
from home.models import (
    Furnished,
    HomeListing,
    HomeMedia,
    HomePost,
    ListerType,
    ListingKind,
    ListingStatus,
    MediaStatus,
    PostStatus,
    PriceBasis,
    PricePeriod,
    PropertyType,
    RoommateGender,
    Tri,
)

logger = logging.getLogger(__name__)

REJECT_REASONS = {"ad", "noise", "not_housing", "incomplete", "expired", "repost", "other"}
BOOL_FLAGS = (
    "untermiete",
    "wg",
    "wgb",
    "pets_ok",
    "students_only",
    "couples_ok",
    "temporary",
    "first_last",
    "damage_deposit",
    "credit_check",
    "parking",
    "no_smoking",
)
MIN_TEXT_CHARS = 40
TEXT_LIMIT = 4000
REVIEW_CONFIDENCE_MIN = 0.7
REPOST_WINDOW_DAYS = 45
REPEAT_POSTER_TOTAL = 15
REPEAT_POSTER_LISTINGS = 5
SQFT_TO_M2 = Decimal("0.092903")
# Plausible monthly asking prices per currency (offer posts). Outside = review queue.
MONTHLY_RANGE = {
    "EUR": (80, 8000),
    "CAD": (200, 12000),
    "USD": (200, 12000),
}
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
_HANDLE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,31})")
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_NANP = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?([2-9]\d{2})\)?[\s.\-]?([2-9]\d{2})[\s.\-]?(\d{4})(?!\d)")
_INTL = re.compile(r"(?<!\d)\+\d[\d\s().\-]{8,16}\d")

COUNTRY_NOTES = {
    "DE": (
        "Germany notes: Warmmiete / گرم / all inclusive = price_basis warm; Kaltmiete / سرد = cold. "
        "کاتسیون / Kaution is the deposit, often 2 or 3 Monatsmieten (deposit_months). "
        "ملد / ملده / Anmeldung possible = registration yes; بدون ملد = no. "
        "Untermiete = sublet (flag untermiete); WG / وگ = shared flat (flag wg); WGB = Wohnberechtigungsschein (flag wgb). "
        "Nachmieter = lease_transfer. Zwischenmiete / زوییشن‌میته = temporary sublet. "
        "A commission (Provision / کمیسیون) is not the deposit. Prices are monthly euro unless stated."
    ),
    "CA": (
        "Canada notes: a bare number or $ means Canadian dollars per month. «همه‌چیز شامل», all inclusive, "
        "utilities included = bills_included yes; «+ utilities» / «بدون قبوض» = no. "
        "بیسمنت / basement = basement. «لیز ترنسفر» / «انتقال لیز» / lease transfer = lease_transfer. "
        "«سابلت» / sublet = sublet. کاندو / condo, تاون‌هاوس / townhouse = apartment or house by size. "
        "«۱ خوابه» = one bedroom (rooms 1); «1 Bedroom plus Den» = rooms 1; استودیو / bachelor / studio = studio. "
        "«۴½» style sizes are Quebec room counts: rooms = bedrooms is not guaranteed, leave rooms empty. "
        "Area in sqft / فوت مربع: fill size_sqft (never convert yourself). "
        "Deposit wording: «ماه اول و آخر» = first_last true; «ودیعه خسارت» / damage deposit = damage_deposit true; "
        "credit check / رفرنس / «نامه کار» = credit_check true. "
        "«فقط آقا» / «فقط خانم» restricts roommate_gender for a room or shared unit. "
        "Contact is usually a phone number (416, 647, 438, 514, 604 and so on); copy it as written. "
        "Realtors, brokers (مشاور املاک، بروکر) and lenders are lister_type agent or an ad. "
        "Municipality in city: Toronto, North York, Scarborough, Etobicoke, Richmond Hill, Thornhill, Markham, Vaughan, "
        "Mississauga, Montreal, Laval, Brossard. Use the province code in region (ON, QC, BC, AB)."
    ),
}

SYSTEM_PROMPT = """You review Telegram posts for Chamedoon Home, a Persian-language housing board for Iranians living abroad.
Decide if the post is ONE real housing post, then fill every field from the post itself. Posts are Persian, German, English, French or a mix.

Accept only:
- offer: someone offers a room, flat, house, basement, sublet, lease transfer or short stay for rent.
- wanted: someone looks for a place to rent or a flatmate.
Reject (accept=false) and give reject_reason:
- ad: agencies or realtors promoting themselves, mortgage/loan/broker pitches, services, moving, cleaning, flight tickets, jobs, shops, currency exchange.
- noise: greetings, questions about laws, chatter, rules, join links, pinned posts, news.
- not_housing: anything else not about renting or sharing a home. A property for sale is not_housing for now.
- incomplete: housing is mentioned but nothing usable (no city and no area, nothing to act on).
- expired: the post itself says the place is taken or the date has passed relative to Posted at.
- other: none of the above.

Rules:
- Never guess. Use "" or null for anything the post does not say. Do not invent prices, dates, areas, phone numbers.
- country: ISO-2 of the place described. Use the source default when the post names no other country.
- city: English name of the city or municipality. Use the source default city only if the post names no city and the source is a single-city board. region: province or state code if known, else the source default.
- area: district, neighbourhood, intersection or street as written, Latin script if the post gives it.
- price_amount: the monthly asking price as a number, no symbol. If the post gives several prices, use the total the tenant pays per month and set price_basis. For wanted posts put the budget ceiling in price_max and leave price_amount null. If the price is per night or per week, keep that number and set price_period.
- price_basis: "warm" if utilities are included, "cold" if utilities are extra, else "unknown".
- price_currency: ISO-4217. If the post shows a bare number, use the source default currency.
- deposit: deposit_amount in the same currency, or deposit_months if the post gives months. Never fill both from one phrase.
- registration: Anmeldung / address registration possible "yes", explicitly not possible "no", silent "unknown".
- property_type: room (a room in a shared home), studio, apartment, house, basement, sublet (temporary re-letting by the tenant), lease_transfer, short_stay, other.
- roommate_gender: female / male / couple only when the post restricts it, else "any". Never record nationality, religion or ethnicity.
- lister_type: "agent" for agencies, brokers and catalog posts; "owner" for a tenant or owner posting for themselves; for wanted posts "unknown".
- contact_telegram: the @username the post tells readers to contact, without @. Not the channel itself. contact_phone: a phone number written in the post, as written.
- size_m2 only when the post says square metres. size_sqft only when it says square feet.
- available_from / available_to: YYYY-MM-DD. Resolve relative dates («از اول ماه بعد», «ab 1.11.») against Posted at. If only a month is given use the 1st.
- summary_fa: 1 or 2 plain Persian sentences with the facts a seeker needs (type, area, price, key conditions). No @handles, no URLs, no phone numbers, no greetings, no emojis.
- confidence: per field 0..1 for kind, property_type, city, price, available_from; and overall 0..1. Lower it when the post is vague or contradicts itself.
- flags: booleans only for keys you see in the post: untermiete, wg, wgb, pets_ok, students_only, couples_ok, temporary, first_last, damage_deposit, credit_check, parking, no_smoking. kaution_text: the deposit wording as written, max 80 chars.

Reply with JSON only, no markdown:
{
  "accept": true,
  "reject_reason": "",
  "kind": "offer",
  "property_type": "room",
  "country": "DE",
  "region": "",
  "city": "Berlin",
  "area": "Neukölln",
  "price_amount": 520,
  "price_max": null,
  "price_currency": "EUR",
  "price_period": "month",
  "price_basis": "warm",
  "deposit_amount": null,
  "deposit_months": 1,
  "size_m2": 16,
  "size_sqft": null,
  "rooms": 1,
  "furnished": "yes",
  "registration": "yes",
  "bills_included": "yes",
  "available_from": "2026-11-01",
  "available_to": null,
  "min_term_months": 6,
  "roommate_gender": "female",
  "occupants_wanted": 1,
  "lister_type": "owner",
  "contact_telegram": "sara_b",
  "contact_phone": "",
  "summary_fa": "اتاق ۱۶ متری مبله در نویکلن برلین، ۵۲۰ یورو گرم، ملد ممکن است، از اول نوامبر.",
  "flags": {"wg": true, "kaution_text": "1 Monatsmiete"},
  "confidence": {"kind": 0.97, "property_type": 0.95, "city": 0.99, "price": 0.9, "available_from": 0.8, "overall": 0.92}
}
For a reject, send only {"accept": false, "reject_reason": "..."}."""


@dataclass
class ReviewOutcome:
    accepted: bool
    listing: HomeListing | None = None
    reject_reason: str = ""
    error: str = ""
    model: str = ""
    raw: dict = field(default_factory=dict)


def text_key(text: str) -> str:
    """Stable fingerprint of a post's wording, for exact and near-exact reposts."""
    cleaned = (text or "").translate(_FA_DIGITS).lower()
    cleaned = re.sub(r"https?://\S+|@\w+", " ", cleaned)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()[:400]
    return hashlib.sha1(cleaned.encode("utf-8")).hexdigest()


def build_prompt(post: HomePost) -> str:
    source = post.source
    posted = post.posted_at.date().isoformat() if post.posted_at else ""
    notes = COUNTRY_NOTES.get(source.country, "")
    return (
        f"Source: @{source.username} ({source.get_kind_display()})\n"
        f"Source default country: {source.country}\n"
        f"Source default city: {source.default_city or '(general board, no default city)'}\n"
        f"Source default region: {source.default_region or '(none)'}\n"
        f"Source default currency: {source.default_currency}\n"
        f"Source is an agency catalog: {'yes' if source.is_agency else 'no'}\n"
        f"Posted at: {posted}\n"
        f"Author: {post.author_username or post.author_name or 'unknown'}\n"
        f"{notes}\n\n"
        f"Post:\n{post.text[:TEXT_LIMIT]}"
    )


def repost_original(post: HomePost) -> HomePost | None:
    """The earliest earlier post with the same wording in the same country, if any."""
    if not post.text_key or len(post.text or "") < MIN_TEXT_CHARS * 2:
        return None
    earlier = Q(posted_at__lt=post.posted_at) | Q(posted_at=post.posted_at, pk__lt=post.pk)
    return (
        HomePost.objects.filter(
            text_key=post.text_key,
            source__country=post.source.country,
            posted_at__gte=post.posted_at - timedelta(days=REPOST_WINDOW_DAYS),
        )
        .filter(earlier)
        .exclude(pk=post.pk)
        .order_by("posted_at", "pk")
        .first()
    )


def review_post(post: HomePost) -> ReviewOutcome:
    """Review one post and store the verdict. Never raises on model trouble."""
    text = (post.text or "").strip()
    if len(text) < MIN_TEXT_CHARS:
        _finish_rejected(post, "noise", model="rule")
        return ReviewOutcome(accepted=False, reject_reason="noise", model="rule")
    original = repost_original(post)
    if original is not None:
        _finish_repost(post, original)
        return ReviewOutcome(accepted=False, reject_reason="repost", model="rule")
    if not llm_enabled():
        return ReviewOutcome(accepted=False, error="No LLM is configured.")
    result = complete(build_prompt(post), system=SYSTEM_PROMPT, temperature=0, max_tokens=1200)
    if not result.ok:
        _finish_error(post, result.error or "llm_error", result.model)
        return ReviewOutcome(accepted=False, error=result.error or "llm_error", model=result.model)
    data = parse_json(result.text)
    if data is None:
        _finish_error(post, "Invalid model JSON.", result.model)
        return ReviewOutcome(accepted=False, error="Invalid model JSON.", model=result.model)
    return apply_review(post, data, model=result.model)


def apply_review(post: HomePost, data: dict, *, model: str = "") -> ReviewOutcome:
    if not bool(data.get("accept")):
        reason = str(data.get("reject_reason") or "").strip().lower()
        if reason not in REJECT_REASONS or reason == "repost":
            reason = "other"
        _finish_rejected(post, reason, model=model, raw=data)
        return ReviewOutcome(accepted=False, reject_reason=reason, model=model, raw=data)

    fields, notes = normalize_listing(post, data)
    if fields is None:
        _finish_rejected(post, "incomplete", model=model, raw=data)
        return ReviewOutcome(accepted=False, reject_reason="incomplete", model=model, raw=data)

    first_seen, last_seen = _seen_window(post)
    with transaction.atomic():
        listing, _ = HomeListing.objects.update_or_create(
            post=post,
            defaults={
                **fields,
                "needs_review": bool(notes),
                "review_note": "; ".join(notes)[:255],
                "first_seen": first_seen,
                "last_seen": last_seen,
            },
        )
        link_duplicate(listing)
        post.status = PostStatus.ACCEPTED
        post.reject_reason = ""
        post.review_json = data
        post.model = model[:96]
        post.error = ""
        post.reviewed_at = timezone.now()
        post.save(update_fields=["status", "reject_reason", "review_json", "model", "error", "reviewed_at"])
    return ReviewOutcome(accepted=True, listing=listing, model=model, raw=data)


def _seen_window(post: HomePost):
    """First and last time this wording appeared, so a repost extends the listing instead of adding one."""
    if not post.text_key:
        return post.posted_at, post.posted_at
    window = HomePost.objects.filter(text_key=post.text_key, source__country=post.source.country).aggregate(
        first=Min("posted_at"), last=Max("posted_at")
    )
    return min(window["first"] or post.posted_at, post.posted_at), max(window["last"] or post.posted_at, post.posted_at)


def normalize_listing(post: HomePost, data: dict) -> tuple[dict | None, list[str]]:
    """Return clean model fields plus review notes, or (None, []) when unusable."""
    source = post.source
    notes: list[str] = []
    kind = _choice(data.get("kind"), ListingKind)
    if not kind:
        return None, []
    property_type = _choice(data.get("property_type"), PropertyType) or PropertyType.OTHER

    country = _country(data.get("country")) or source.country
    city = _clean(data.get("city"), 64) or source.default_city
    area = _clean(data.get("area"), 96)
    if not city and not area:
        return None, []
    region = _region(data.get("region")) or (source.default_region if country == source.country else "")
    if country != source.country:
        notes.append(f"country {country} differs from source {source.country}")

    period = _choice(data.get("price_period"), PricePeriod) or PricePeriod.MONTH
    amount = _money(data.get("price_amount"))
    price_max = _money(data.get("price_max"))
    currency = _currency(data.get("price_currency")) or source.default_currency
    if currency != source.default_currency:
        notes.append(f"currency {currency} differs from source default {source.default_currency}")
    for value, label in ((amount, "price"), (price_max, "budget")):
        if value is not None and period == PricePeriod.MONTH and currency in MONTHLY_RANGE:
            low, high = MONTHLY_RANGE[currency]
            if not (low <= value <= high):
                notes.append(f"{label} {value} {currency} outside {low}-{high}")

    deposit_amount = _money(data.get("deposit_amount"))
    deposit_months = _decimal(data.get("deposit_months"), maximum=Decimal("12"))
    if deposit_amount is not None and deposit_months is not None:
        deposit_months = None
        notes.append("deposit given as amount and months; kept the amount")

    size_m2 = _decimal(data.get("size_m2"), maximum=Decimal("2000"))
    if size_m2 is None:
        sqft = _decimal(data.get("size_sqft"), maximum=Decimal("20000"))
        if sqft is not None:
            size_m2 = (sqft * SQFT_TO_M2).quantize(Decimal("0.1"))

    available_from = _iso_date(data.get("available_from"))
    available_to = _iso_date(data.get("available_to"))
    if available_from and available_to and available_to < available_from:
        available_to = None
        notes.append("end date before start date")
    if available_from and post.posted_at and available_from < post.posted_at.date() - timedelta(days=365):
        available_from = None
        notes.append("start date more than a year before the post")

    confidence = _confidence(data.get("confidence"))
    overall = confidence.get("overall")
    if overall is not None and overall < REVIEW_CONFIDENCE_MIN:
        notes.append(f"low confidence {overall:.2f}")

    contact_tg = _clean(str(data.get("contact_telegram") or "").lstrip("@"), 64)
    if contact_tg.lower() == source.username.lower():
        contact_tg = ""
    if not contact_tg:
        contact_tg = _first_handle(post.text, exclude=source.username)
    contact_phone = _phone(data.get("contact_phone"), country) or _first_phone(post.text, country)

    flags = _flags(data.get("flags"))
    if kind == ListingKind.OFFER and amount is None:
        flags["price_missing"] = True  # normal for «پیام بدید» posts: shown as "ask for price", not queued
    warning = _deposit_warning(region, flags, deposit_amount, deposit_months)
    if warning:
        flags["deposit_warning"] = warning

    lister = _choice(data.get("lister_type"), ListerType) or ListerType.UNKNOWN
    if kind == ListingKind.WANTED:
        lister = ListerType.UNKNOWN
    elif source.is_agency:
        lister = ListerType.AGENT
    elif _repeat_poster(post):
        lister = ListerType.AGENT
        flags["repeat_poster"] = True

    gender = _choice(data.get("roommate_gender"), RoommateGender) or RoommateGender.ANY
    if property_type not in {PropertyType.ROOM, PropertyType.SUBLET, PropertyType.SHORT_STAY, PropertyType.BASEMENT}:
        gender = RoommateGender.ANY if gender != RoommateGender.COUPLE else gender

    fields = {
        "kind": kind,
        "property_type": property_type,
        "country": country,
        "region": region,
        "city": city,
        "area": area,
        "price_amount": amount,
        "price_max": price_max,
        "price_currency": currency,
        "price_period": period,
        "price_basis": _choice(data.get("price_basis"), PriceBasis) or PriceBasis.UNKNOWN,
        "deposit_amount": deposit_amount,
        "deposit_months": deposit_months,
        "size_m2": size_m2,
        "rooms": _decimal(data.get("rooms"), maximum=Decimal("20")),
        "furnished": _choice(data.get("furnished"), Furnished) or Furnished.UNKNOWN,
        "registration": _choice(data.get("registration"), Tri) or Tri.UNKNOWN,
        "bills_included": _choice(data.get("bills_included"), Tri) or Tri.UNKNOWN,
        "available_from": available_from,
        "available_to": available_to,
        "min_term_months": _small_int(data.get("min_term_months"), maximum=60),
        "roommate_gender": gender,
        "occupants_wanted": _small_int(data.get("occupants_wanted"), maximum=10),
        "lister_type": lister,
        "contact_telegram": contact_tg,
        "contact_phone": contact_phone,
        "summary_fa": _summary(data.get("summary_fa")),
        "flags": flags,
        "confidence": confidence,
        "dedup_key": post.text_key or text_key(post.text),
    }
    if not fields["summary_fa"]:
        notes.append("no summary")
    return fields, notes


def _deposit_warning(region: str, flags: dict, amount, months) -> str:
    """Rules a seeker should know about. Shown as a label, never used to hide the listing."""
    if region == "ON" and flags.get("damage_deposit"):
        return "Ontario allows only a last-month deposit, not a damage deposit"
    if region == "QC" and (flags.get("damage_deposit") or amount is not None or months is not None):
        return "Quebec landlords cannot ask for a deposit"
    return ""


def _repeat_poster(post: HomePost) -> bool:
    """A sender who keeps posting is almost always an agent or a bumping landlord."""
    if not post.author_id:
        return False
    since = post.posted_at - timedelta(days=30)
    posts = HomePost.objects.filter(
        author_id=post.author_id, source__country=post.source.country, posted_at__gte=since, posted_at__lte=post.posted_at
    )
    if posts.count() >= REPEAT_POSTER_TOTAL:
        return True
    return posts.filter(status=PostStatus.ACCEPTED).count() >= REPEAT_POSTER_LISTINGS


def link_duplicate(listing: HomeListing) -> None:
    """Merge a listing with an earlier one that has the same wording, or the same contact, city and price."""
    base = HomeListing.objects.exclude(pk=listing.pk).exclude(status=ListingStatus.DUPLICATE).filter(country=listing.country)
    match = None
    if listing.dedup_key:
        match = base.filter(dedup_key=listing.dedup_key).order_by("first_seen").first()
    if match is None and listing.price_amount is not None and (listing.contact_telegram or listing.contact_phone):
        low = listing.price_amount * Decimal("0.95")
        high = listing.price_amount * Decimal("1.05")
        same_person = base.filter(
            kind=listing.kind,
            city=listing.city,
            property_type=listing.property_type,
            price_amount__gte=low,
            price_amount__lte=high,
        )
        if listing.contact_telegram:
            match = same_person.filter(contact_telegram__iexact=listing.contact_telegram).order_by("first_seen").first()
        if match is None and listing.contact_phone:
            match = same_person.filter(contact_phone=listing.contact_phone).order_by("first_seen").first()
    if match is not None:
        merge_listings(listing, match)


def merge_listings(left: HomeListing, right: HomeListing) -> HomeListing:
    """Keep the earlier listing as the original and mark the other as its repost. Returns the original."""
    def order(item: HomeListing):
        return (item.first_seen, item.post.posted_at, item.pk)

    original, repost = (left, right) if order(left) <= order(right) else (right, left)
    repost.status = ListingStatus.DUPLICATE
    repost.canonical = original
    repost.save(update_fields=["status", "canonical", "updated_at"])
    HomeListing.objects.filter(canonical=repost).update(canonical=original)
    if original.last_seen is None or repost.last_seen > original.last_seen:
        original.last_seen = repost.last_seen
    if repost.first_seen < original.first_seen:
        original.first_seen = repost.first_seen
    original.save(update_fields=["last_seen", "first_seen", "updated_at"])
    return original


def link_photo_duplicates(listing: HomeListing) -> HomeListing | None:
    """Merge with a listing in the same city that shows the same photo at a compatible price.

    A shared photo alone is not enough: agents reuse building photos across units. The price
    must be within 10% (or unknown) or the contact must be the same. A shared photo with a
    different price is only noted on the listing.
    """
    if listing.status == ListingStatus.DUPLICATE:
        return None
    mine = list(
        HomeMedia.objects.filter(post_id=listing.post_id, status=MediaStatus.STORED, phash__isnull=False).values_list(
            "phash", flat=True
        )
    )
    if not mine:
        return None
    candidates = (
        HomeListing.objects.exclude(pk=listing.pk)
        .exclude(status=ListingStatus.DUPLICATE)
        .filter(
            country=listing.country,
            city=listing.city,
            kind=listing.kind,
            first_seen__gte=listing.first_seen - timedelta(days=REPOST_WINDOW_DAYS),
            first_seen__lte=listing.last_seen + timedelta(days=REPOST_WINDOW_DAYS),
            post__media__status=MediaStatus.STORED,
        )
        .distinct()
        .order_by("first_seen")
    )
    for other in candidates:
        theirs = HomeMedia.objects.filter(post_id=other.post_id, status=MediaStatus.STORED, phash__isnull=False).values_list(
            "phash", flat=True
        )
        if not any(same_photo(a, b) for a in mine for b in theirs):
            continue
        if _prices_compatible(listing, other) or _same_contact(listing, other):
            return merge_listings(listing, other)
        flags = dict(listing.flags or {})
        flags["shared_photo_with"] = other.pk
        listing.flags = flags
        listing.save(update_fields=["flags", "updated_at"])
    return None


def _prices_compatible(left: HomeListing, right: HomeListing) -> bool:
    if left.price_amount is None or right.price_amount is None:
        return True
    low, high = sorted((left.price_amount, right.price_amount))
    return high <= low * Decimal("1.10")


def _same_contact(left: HomeListing, right: HomeListing) -> bool:
    if left.contact_telegram and left.contact_telegram.lower() == right.contact_telegram.lower():
        return True
    return bool(left.contact_phone and left.contact_phone == right.contact_phone)


def _finish_rejected(post: HomePost, reason: str, *, model: str = "", raw: dict | None = None) -> None:
    post.status = PostStatus.REJECTED
    post.reject_reason = reason[:64]
    post.review_json = raw or None
    post.model = model[:96]
    post.error = ""
    post.reviewed_at = timezone.now()
    post.save(update_fields=["status", "reject_reason", "review_json", "model", "error", "reviewed_at"])


def _finish_repost(post: HomePost, original: HomePost) -> None:
    _finish_rejected(post, "repost", model="rule", raw={"repost_of": original.pk})
    post.media.all().delete()
    listing = getattr(original, "listing", None)
    if listing is not None and post.posted_at > listing.last_seen:
        listing.last_seen = post.posted_at
        listing.save(update_fields=["last_seen", "updated_at"])


def _finish_error(post: HomePost, error: str, model: str) -> None:
    post.status = PostStatus.ERROR
    post.error = (error or "")[:255]
    post.model = (model or "")[:96]
    post.reviewed_at = timezone.now()
    post.save(update_fields=["status", "error", "model", "reviewed_at"])


def parse_json(text: str) -> dict | None:
    cleaned = _JSON_FENCE.sub("", (text or "").strip()).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        body = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def _clean(value: object, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _choice(value: object, enum) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in {choice.value for choice in enum} else ""


def _country(value: object) -> str:
    raw = str(value or "").strip()
    return raw.upper() if len(raw) == 2 and raw.isalpha() else ""


def _region(value: object) -> str:
    raw = str(value or "").strip()
    return raw.upper() if 2 <= len(raw) <= 3 and raw.isalpha() else ""


def _currency(value: object) -> str:
    raw = str(value or "").strip().upper()
    return raw if len(raw) == 3 and raw.isalpha() else ""


def _decimal(value: object, *, maximum: Decimal) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).translate(_FA_DIGITS).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if number <= 0 or number > maximum:
        return None
    return number.quantize(Decimal("0.1"))


def _money(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).translate(_FA_DIGITS).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if number <= 0 or number > Decimal("10000000"):
        return None
    return number.quantize(Decimal("0.01"))


def _small_int(value: object, *, maximum: int) -> int | None:
    try:
        number = int(Decimal(str(value).translate(_FA_DIGITS)))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if 0 < number <= maximum else None


def _iso_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()[:10]
    if len(raw) != 10:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _confidence(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    clean: dict[str, float] = {}
    for key, raw in value.items():
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= number <= 1:
            clean[str(key)[:24]] = round(number, 2)
    return clean


def _flags(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    clean: dict[str, object] = {}
    for key in BOOL_FLAGS:
        if isinstance(value.get(key), bool):
            clean[key] = value[key]
    text = _clean(value.get("kaution_text"), 80)
    if text:
        clean["kaution_text"] = text
    return clean


def _summary(value: object) -> str:
    text = _clean(value, 500)
    text = re.sub(r"https?://\S+|t\.me/\S+|@\w+", "", text)
    text = _NANP.sub("", text.translate(_FA_DIGITS))
    return re.sub(r"\s+", " ", text).strip()


def _phone(value: object, country: str) -> str:
    """Digits in international form. North American numbers become +1 followed by ten digits."""
    raw = str(value or "").translate(_FA_DIGITS)
    digits = re.sub(r"\D", "", raw)
    if country in {"CA", "US"}:
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        return f"+1{digits}" if len(digits) == 10 else ""
    if len(digits) < 9:
        return ""
    return f"+{digits}"[:24] if raw.strip().startswith("+") else digits[:24]


def _first_phone(text: str, country: str) -> str:
    cleaned = (text or "").translate(_FA_DIGITS)
    if country in {"CA", "US"}:
        match = _NANP.search(cleaned)
        return f"+1{match.group(1)}{match.group(2)}{match.group(3)}" if match else ""
    match = _INTL.search(cleaned)
    return _phone(match.group(0), country) if match else ""


def _first_handle(text: str, *, exclude: str) -> str:
    for match in _HANDLE.finditer(text or ""):
        handle = match.group(1)
        if handle.lower() != exclude.lower() and not handle.lower().endswith("bot"):
            return handle
    return ""
