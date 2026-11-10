"""The closed list of things the agent may do, each on one listing the caller has already checked is theirs.

Extend, rented, remove, fill a field, add a photo, put the phone on the ad. Nothing else changes a listing.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from home import freshness, status_posts
from home.models import HomeListing, HomeListingEdit, ListingKind, ListingStatus, PropertyType, StatusPost

PHONE = re.compile(r"^\+?\d{7,15}$")
MAX_PRICE = Decimal("20000")
STUDIO = re.compile(r"استودیو|استدیو|studio|einzimmer|1[- ]zimmer")


def extend_listing(listing: HomeListing, telegram_id: int) -> None:
    """«هنوز هست»: an expired ad comes back, an active one counts as confirmed by its owner."""
    now = timezone.now()
    if listing.status == ListingStatus.EXPIRED:
        listing.status = ListingStatus.ACTIVE
        listing.last_seen = now
        listing.last_confirmed_at = now
        listing.save(update_fields=["status", "last_seen", "last_confirmed_at", "updated_at"])
        if listing.status_post:
            status_posts.apply(listing, StatusPost.RESTORED)
        return
    freshness.confirm_available(listing, telegram_id)


def mark_rented(listing: HomeListing) -> bool:
    return freshness.set_rented(listing)


def remove_listing(listing: HomeListing) -> None:
    """Gone for everyone, its channel posts edited to closed, its contact dropped. Same as the owner's delete."""
    with transaction.atomic():
        current = HomeListing.objects.select_for_update(of=("self",)).select_related("post").get(pk=listing.pk)
        if current.status == ListingStatus.REMOVED:
            return
        was_open = current.status == ListingStatus.ACTIVE
        current.status = ListingStatus.REMOVED
        current.dedup_key = ""
        current.contact_telegram = ""
        current.contact_phone = ""
        current.save(update_fields=["status", "dedup_key", "contact_telegram", "contact_phone", "updated_at"])
        HomeListingEdit.objects.filter(listing=current).delete()
    if was_open:
        from home.inbox import listing_closed

        listing_closed(current)
    if current.published_at or current.publications.exists():
        status_posts.apply(current, StatusPost.RENTED)


def _decimal(value: str) -> Decimal | None:
    text = (value or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫,", "0123456789.."))
    found = re.search(r"\d+(?:\.\d+)?", text)
    if not found:
        return None
    try:
        return Decimal(found.group(0))
    except InvalidOperation:
        return None


MONTHS = {
    # Gregorian month names as people write them in Persian, English and German.
    "ژانویه": 1, "ژانویهٔ": 1, "فوریه": 2, "مارس": 3, "آوریل": 4, "آپریل": 4, "مه": 5, "می": 5, "ژوئن": 6, "جون": 6,
    "ژوئیه": 7, "جولای": 7, "اوت": 8, "آگوست": 8, "سپتامبر": 9, "اکتبر": 10, "نوامبر": 11, "دسامبر": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "januar": 1, "februar": 2, "märz": 3, "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10, "dezember": 12,
}
NOW_WORDS = ("الان", "همین الان", "فوری", "خالیه", "آماده", "now", "immediately", "asap", "sofort", "ab sofort")
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").translate(DIGITS).replace("ي", "ی").replace("ك", "ک")).strip().lower()


def parse_date(value: str, today=None):
    """A move-in date as people write it: 2026-12-01, 1/12, «اول دسامبر», «1 Dec», «الان». None when unsure."""
    from datetime import date

    today = today or timezone.localdate()
    text = _plain(value)
    if not text:
        return None
    if any(word in text for word in NOW_WORDS):
        return today
    found = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    try:
        if found:
            return date(int(found.group(1)), int(found.group(2)), int(found.group(3)))
        day = 1 if re.search(r"\bاول\b|اوایل|first|anfang", text) else None
        number = re.search(r"\b(\d{1,2})\b", text)
        month = next((index for name, index in MONTHS.items() if re.search(rf"(^|\s){re.escape(name)}", text)), None)
        if month is None:
            slash = re.search(r"\b(\d{1,2})[/.](\d{1,2})\b", text)
            if not slash:
                return None
            day, month = int(slash.group(1)), int(slash.group(2))
        elif day is None:
            day = int(number.group(1)) if number else 1
        year = today.year if month >= today.month - 1 else today.year + 1
        return date(year, month, day)
    except ValueError:
        return None


def parse_furnished(value: str) -> str:
    text = _plain(value)
    if re.search(r"نیمه|partial|teilweise|بعضی", text):
        return "partial"
    if re.search(r"بدون مبل|مبله نیست|خالیه|خالی|unfurnished|unmöbliert|^نه|^خیر|^no", text):
        return "no"
    if re.search(r"مبله|مبل|furnished|möbliert|^آره|^بله|^yes|^ja", text):
        return "yes"
    return ""


def parse_bills(value: str) -> str:
    text = _plain(value)
    if re.search(r"جدا|نیست|سرد|kalt|not included|exclusive|^نه|^خیر|^no", text):
        return "no"
    if re.search(r"شامل|با قبوض|حساب شده|گرم|warm|included|inklusive|all.?in|^آره|^بله|^yes|^ja", text):
        return "yes"
    return ""


def fill_field(listing: HomeListing, field: str, value: str) -> bool:
    """H4: one fact from the chat or an operator, only sensible values. The fields are in `outreach.completion.GAPS`."""
    if listing.status != ListingStatus.ACTIVE or listing.kind != ListingKind.OFFER:
        return False
    updates: dict = {}
    if field == "available_from":
        moved = parse_date(value)
        if moved is None or not (timezone.localdate() - timedelta(days=60) <= moved <= timezone.localdate() + timedelta(days=400)):
            return False
        updates["available_from"] = moved
    elif field == "furnished":
        choice = parse_furnished(value)
        if not choice:
            return False
        updates["furnished"] = choice
    elif field == "bills":
        choice = parse_bills(value)
        if not choice:
            return False
        updates["bills_included"] = choice
    elif field == "deposit":
        text = _plain(value)
        number = _decimal(text)
        if number is None:
            return False
        if re.search(r"ماه|month|monat|miete", text) and Decimal("0.5") <= number <= Decimal("6"):
            updates["deposit_months"] = number
        elif Decimal("50") <= number <= MAX_PRICE * 3:
            updates["deposit_amount"] = number
        else:
            return False
    elif field == "price":
        number = _decimal(value)
        if number is None or not Decimal("50") <= number <= MAX_PRICE:
            return False
        updates["price_amount"] = number
    elif field == "rooms" and STUDIO.search(_plain(value)):
        # «استدیو هستش» answers the bedroom question: a studio has none to count (audit 2026-11-09, conv 23 T129).
        updates["property_type"] = PropertyType.STUDIO
    elif field == "rooms":
        number = _decimal(value)
        if number is None or not Decimal("0.5") <= number <= Decimal("12"):
            return False
        updates["rooms"] = number
    elif field == "size":
        number = _decimal(value)
        if number is not None and re.search(r"sq\.?\s*ft|square\s*f(?:ee|oo)t|فوت", _plain(value)):
            number = (number * Decimal("0.0929")).quantize(Decimal("1"))
        if number is None or not Decimal("8") <= number <= Decimal("1000"):
            return False
        updates["size_m2"] = number
    elif field == "area":
        text = re.sub(r"\s+", " ", value or "").strip()[:96]
        if len(text) < 2 or "@" in text or "http" in text or re.search(r"\d{6,}", text) or not is_place(listing, text):
            return False
        updates["area"] = text
    else:
        return False
    HomeListing.objects.filter(pk=listing.pk).update(**updates, updated_at=timezone.now())
    listing.refresh_from_db()
    _after_change(listing, area=field == "area")
    return True


# A floor area, never a place: «۷۰ متر», «70m2», «85 qm», «900 sq ft» (audit 2026-11-10, conv 26 T142).
SIZE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:متر|m2|m²|qm|sqm|sq\.?\s*ft|square|فوت)", re.IGNORECASE)


def is_size(value: str) -> bool:
    return bool(SIZE.search(_plain(value)))


def is_place(listing: HomeListing, value: str) -> bool:
    """An area answer is written only when it names a place: never a size or a bare number. With digits in it, it must
    resolve to a known area, a town, or carry a postcode (audit 2026-11-10, conv 26: «70 متر» replaced Eichwalde)."""
    import copy

    text = _plain(value)
    if is_size(text) or len(re.sub(r"[\d\s.,،\-/]+", "", text)) < 2:
        return False
    if not re.search(r"\d", text):
        return True
    if re.search(r"(?<!\d)\d{4,5}(?!\d)", text):
        return True
    from home.areas import match_area
    from outreach.housing import town_of

    probe = copy.copy(listing)
    probe.area = value
    try:
        return match_area(probe) is not None or bool(town_of(probe))
    except Exception:  # noqa: BLE001 - an unknown place is not written
        return False


def set_phone(listing: HomeListing, phone: str) -> bool:
    """H6: only after an explicit yes, because the number becomes public on the ad."""
    phone = re.sub(r"[^\d+]", "", phone or "")
    if not PHONE.fullmatch(phone) or listing.status != ListingStatus.ACTIVE:
        return False
    HomeListing.objects.filter(pk=listing.pk).update(contact_phone=phone[:24], updated_at=timezone.now())
    listing.refresh_from_db()
    _after_change(listing)
    return True


def _after_change(listing: HomeListing, *, area: bool = False) -> None:
    from home.snapshots import after_submit

    if area:
        from home.areas import link_area

        link_area(listing)
    after_submit(listing)
    if listing.published_at or listing.publications.exists():
        status_posts.apply(listing, StatusPost.RESTORED)
