"""AI review of one housing post: is it a real listing, and what does it say.

One call decides both. The model sees the raw post and the source's defaults and
returns JSON. Everything it returns is checked here before it reaches the
database: enums, dates, money ranges, and the country/currency pair. A value the
checks cannot trust puts the listing in the review queue instead of publishing
it with a guess.
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
from django.utils import timezone

from ai.llm import complete, llm_enabled
from home.models import (
    Furnished,
    HomeListing,
    HomePost,
    ListerType,
    ListingKind,
    ListingStatus,
    PostStatus,
    PriceBasis,
    PricePeriod,
    PropertyType,
    RoommateGender,
    Tri,
)

logger = logging.getLogger(__name__)

REJECT_REASONS = {"ad", "noise", "not_housing", "incomplete", "expired", "other"}
FLAG_KEYS = (
    "untermiete",
    "wg",
    "wgb",
    "pets_ok",
    "students_only",
    "couples_ok",
    "temporary",
    "kaution_text",
)
MIN_TEXT_CHARS = 40
TEXT_LIMIT = 4000
REVIEW_CONFIDENCE_MIN = 0.7
# Plausible monthly asking prices per currency (offer posts). Outside = review queue.
MONTHLY_RANGE = {
    "EUR": (80, 8000),
    "CAD": (200, 12000),
    "USD": (200, 12000),
}
_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
_HANDLE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,31})")
_PHONE = re.compile(r"\+?\d[\d\s().\-]{8,17}\d")
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

SYSTEM_PROMPT = """You review Telegram posts for Chamedoon Home, a Persian-language housing board for Iranians living abroad.
Decide if the post is ONE real housing post, then fill every field from the post itself. Posts are Persian, German, English or a mix.

Accept only:
- offer: someone offers a room, flat, house, sublet, lease transfer or short stay for rent.
- wanted: someone looks for a place to rent or a flatmate.
Reject (accept=false) and give reject_reason:
- ad: agencies promoting themselves, mortgage/loan/broker pitches, services, moving, cleaning, flight tickets, jobs, shops.
- noise: greetings, questions about laws, chatter, rules, join links, pinned posts, news.
- not_housing: anything else not about renting or sharing a home. Sale of a property is not_housing for now.
- incomplete: housing is mentioned but nothing usable (no city and no area, nothing to act on).
- expired: the post itself says the place is taken or the date has passed relative to Posted at.
- other: none of the above.

Rules:
- Never guess. Use "" or null for anything the post does not say. Do not invent prices, dates, areas, phone numbers.
- country: ISO-2 of the place described. Use the source default when the post names no other country.
- city: English city name (Berlin, Frankfurt, Munich). Use the source default city only if the post names no city and the source is a single-city channel.
- area: district or street as written (Neukölln, Charlottenburg), Latin script if the post gives it.
- price_amount: the monthly asking price as a number, no symbol. If the post gives several prices, use the total the tenant pays per month and set price_basis. For wanted posts put the budget ceiling in price_max and leave price_amount null.
- price_basis: "warm" if utilities are included (Warmmiete, گرم, all inclusive), "cold" if the post says Kaltmiete or utilities extra (سرد), else "unknown".
- price_currency: ISO-4217. If the post shows a bare number, use the source default currency.
- price_period: month, week or night. Short stays quoted per night or week keep that period.
- deposit: deposit_amount in the same currency, or deposit_months if the post says "2 Monatsmieten" or "کاتسیون ۲ ماه". Never fill both from one phrase.
- Anmeldung / ملد / ثبت آدرس possible: registration "yes". Explicitly not possible (بدون ملد): "no". Silent: "unknown".
- property_type: room (a room in a shared home or WG), studio (one room with its own kitchen/bath), apartment, house, basement, sublet (temporary re-letting by the tenant; Untermiete), lease_transfer, short_stay, other. Use room for «اتاق». Use sublet when the post says Untermiete or ساب‌لت and the unit is whole.
- roommate_gender: female / male / couple only when the post restricts it, else "any". Never record nationality, religion or ethnicity.
- lister_type: "agent" for agencies, brokers (مشاور املاک, Makler) and catalog posts; "owner" for a tenant or owner posting for themselves; else "unknown".
- contact_telegram: the @username the post tells readers to contact, without @. Not the channel itself. contact_phone: a phone number written in the post, digits with country prefix if shown.
- available_from / available_to: YYYY-MM-DD. Resolve «از اول ماه بعد» or «ab 1.11.» against Posted at. If only a month is given use the 1st.
- summary_fa: 1 or 2 plain Persian sentences with the facts a seeker needs (type, area, price, key conditions). No @handles, no URLs, no phone numbers, no greetings, no emojis.
- confidence: per field 0..1 for kind, property_type, city, price, available_from; and overall 0..1. Lower it when the post is vague or contradicts itself.
- flags: booleans only for keys you see in the post: untermiete, wg, wgb, pets_ok, students_only, couples_ok, temporary. kaution_text: the deposit wording as written, max 80 chars.

Reply with JSON only, no markdown:
{
  "accept": true,
  "reject_reason": "",
  "kind": "offer",
  "property_type": "room",
  "country": "DE",
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
    return (
        f"Source: @{source.username} ({source.get_kind_display()})\n"
        f"Source default country: {source.country}\n"
        f"Source default city: {source.default_city or '(general board, no default city)'}\n"
        f"Source default currency: {source.default_currency}\n"
        f"Source is an agency catalog: {'yes' if source.is_agency else 'no'}\n"
        f"Posted at: {posted}\n"
        f"Author: {post.author_username or post.author_name or 'unknown'}\n\n"
        f"Post:\n{post.text[:TEXT_LIMIT]}"
    )


def review_post(post: HomePost) -> ReviewOutcome:
    """Run the AI on one post and store the verdict. Never raises on model trouble."""
    text = (post.text or "").strip()
    if len(text) < MIN_TEXT_CHARS:
        _finish_rejected(post, "noise", model="rule")
        return ReviewOutcome(accepted=False, reject_reason="noise", model="rule")
    if not llm_enabled():
        return ReviewOutcome(accepted=False, error="No LLM is configured.")
    result = complete(build_prompt(post), system=SYSTEM_PROMPT, temperature=0, max_tokens=1100)
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
        if reason not in REJECT_REASONS:
            reason = "other"
        _finish_rejected(post, reason, model=model, raw=data)
        return ReviewOutcome(accepted=False, reject_reason=reason, model=model, raw=data)

    fields, notes = normalize_listing(post, data)
    if fields is None:
        _finish_rejected(post, "incomplete", model=model, raw=data)
        return ReviewOutcome(accepted=False, reject_reason="incomplete", model=model, raw=data)

    with transaction.atomic():
        listing, _ = HomeListing.objects.update_or_create(
            post=post,
            defaults={
                **fields,
                "needs_review": bool(notes),
                "review_note": "; ".join(notes)[:255],
                "first_seen": post.posted_at,
                "last_seen": post.posted_at,
            },
        )
        _link_duplicate(listing)
        post.status = PostStatus.ACCEPTED
        post.reject_reason = ""
        post.review_json = data
        post.model = model[:96]
        post.error = ""
        post.reviewed_at = timezone.now()
        post.save(
            update_fields=["status", "reject_reason", "review_json", "model", "error", "reviewed_at"]
        )
    return ReviewOutcome(accepted=True, listing=listing, model=model, raw=data)


def normalize_listing(post: HomePost, data: dict) -> tuple[dict | None, list[str]]:
    """Return clean model fields plus review notes, or (None, []) when unusable."""
    source = post.source
    notes: list[str] = []
    kind = _choice(data.get("kind"), ListingKind)
    if not kind:
        return None, []
    property_type = _choice(data.get("property_type"), PropertyType) or PropertyType.OTHER

    country = _country(data.get("country")) or source.country
    city = _clean(data.get("city"), 64) or ("" if not source.default_city else source.default_city)
    if not city and not _clean(data.get("area"), 96):
        return None, []
    if country != source.country and source.default_currency in MONTHLY_RANGE:
        notes.append(f"country {country} differs from source {source.country}")

    period = _choice(data.get("price_period"), PricePeriod) or PricePeriod.MONTH
    amount = _money(data.get("price_amount"))
    price_max = _money(data.get("price_max"))
    currency = _currency(data.get("price_currency")) or source.default_currency
    if kind == ListingKind.OFFER and amount is None:
        notes.append("offer without a price")
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
    contact_phone = _phone(data.get("contact_phone")) or _first_phone(post.text)

    lister = _choice(data.get("lister_type"), ListerType) or ListerType.UNKNOWN
    if kind == ListingKind.WANTED:
        lister = ListerType.UNKNOWN
    elif source.is_agency:
        lister = ListerType.AGENT

    gender = _choice(data.get("roommate_gender"), RoommateGender) or RoommateGender.ANY
    if property_type not in {PropertyType.ROOM, PropertyType.SUBLET, PropertyType.SHORT_STAY}:
        gender = RoommateGender.ANY if gender != RoommateGender.COUPLE else gender

    fields = {
        "kind": kind,
        "property_type": property_type,
        "country": country,
        "city": city,
        "area": _clean(data.get("area"), 96),
        "price_amount": amount,
        "price_max": price_max,
        "price_currency": currency,
        "price_period": period,
        "price_basis": _choice(data.get("price_basis"), PriceBasis) or PriceBasis.UNKNOWN,
        "deposit_amount": deposit_amount,
        "deposit_months": deposit_months,
        "size_m2": _decimal(data.get("size_m2"), maximum=Decimal("2000")),
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
        "flags": _flags(data.get("flags")),
        "confidence": confidence,
        "dedup_key": post.text_key or text_key(post.text),
    }
    if not fields["summary_fa"]:
        notes.append("no summary")
    return fields, notes


def _link_duplicate(listing: HomeListing) -> None:
    """Mark a listing as a repost when the same wording or contact+price was already seen."""
    base = HomeListing.objects.exclude(pk=listing.pk).exclude(status=ListingStatus.DUPLICATE)
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
    if match is None:
        return
    listing.status = ListingStatus.DUPLICATE
    listing.canonical = match
    listing.save(update_fields=["status", "canonical", "updated_at"])
    if listing.last_seen and (match.last_seen is None or listing.last_seen > match.last_seen):
        match.last_seen = listing.last_seen
        match.save(update_fields=["last_seen", "updated_at"])


def _finish_rejected(post: HomePost, reason: str, *, model: str = "", raw: dict | None = None) -> None:
    post.status = PostStatus.REJECTED
    post.reject_reason = reason[:64]
    post.review_json = raw or None
    post.model = model[:96]
    post.error = ""
    post.reviewed_at = timezone.now()
    post.save(update_fields=["status", "reject_reason", "review_json", "model", "error", "reviewed_at"])


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
    for key in FLAG_KEYS:
        if key not in value:
            continue
        if key == "kaution_text":
            text = _clean(value[key], 80)
            if text:
                clean[key] = text
        elif isinstance(value[key], bool):
            clean[key] = value[key]
    return clean


def _summary(value: object) -> str:
    text = _clean(value, 500)
    text = re.sub(r"https?://\S+|t\.me/\S+|@\w+", "", text)
    text = _PHONE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _phone(value: object) -> str:
    digits = re.sub(r"[^\d+]", "", str(value or "").translate(_FA_DIGITS))
    return digits[:24] if len(re.sub(r"\D", "", digits)) >= 9 else ""


def _first_phone(text: str) -> str:
    for match in _PHONE.finditer((text or "").translate(_FA_DIGITS)):
        phone = _phone(match.group(0))
        if phone:
            return phone
    return ""


def _first_handle(text: str, *, exclude: str) -> str:
    for match in _HANDLE.finditer(text or ""):
        handle = match.group(1)
        if handle.lower() != exclude.lower() and not handle.lower().endswith("bot"):
            return handle
    return ""
