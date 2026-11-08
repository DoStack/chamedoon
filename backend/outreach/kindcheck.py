"""Before any first message: does the post really offer a place, or look for one? (Mohammad, 2026-11-06.)

A room offered as «جویای هم‌خانه» was stored as a request and its poster got seeker messages. One model call reads the
post text again before the first message goes; when it disagrees with the stored kind, nothing is sent and the
listing goes back to review.
"""

from __future__ import annotations

import json

from django.utils import timezone


from outreach.models import OutreachKind, OutreachMessage

SEEKER = {OutreachKind.S1, OutreachKind.S1B, OutreachKind.S2}
OWNER = {OutreachKind.P1, OutreachKind.P1Q, OutreachKind.P6}
PROMPT = """You read one housing post from a Telegram group. Decide who has the place.
"offer": the poster has a room or home and wants a tenant or flatmate. «جویای هم‌خانه», «هم‌خونه می‌خوام», «اتاق آماده اجاره است»,
«Nachmieter gesucht», «Mitbewohner gesucht» with a room described are offers.
"wanted": the poster has no place and is looking for one.
Also count the distinct neighbourhoods, towns or cities the post names as where the place is or where they want to live
("places"), and say whether you are sure of the kind, the area and the city ("sure"). Several towns, a vague area, a post
that could be offer or wanted, or a city you cannot tell: sure is false.
"housing" is false when the poster wants no place to live: only an address to register (ملده بدون سکونت, Meldeadresse,
Anmeldung only), a parking spot, storage, or anything that is not a room or a home.
Answer with JSON only: {"kind": "offer" or "wanted", "places": <number>, "sure": true or false, "housing": true or false}."""


def agrees(message: OutreachMessage) -> bool | None:
    """True when the post matches the message's audience, False when it does not, None when the model is down."""
    if message.kind not in SEEKER | OWNER or message.listing is None:
        return True
    checked = (message.extra or {}).get("kind_checked")
    if checked in {"offer", "wanted"} and "post_sure" in (message.extra or {}):
        return _fits(message, checked)
    from ai.openrouter import complete

    text = (message.listing.post.text or "")[:2500]
    result = complete(text, system=PROMPT, temperature=0, max_tokens=120)
    if not result.ok:
        return None
    try:
        data = json.loads((result.text or "").strip().strip("`").removeprefix("json").strip())
        kind = data.get("kind")
    except (ValueError, AttributeError):
        return None
    if kind not in {"offer", "wanted"}:
        return None
    places = data.get("places")
    message.extra = {
        **(message.extra or {}),
        "kind_checked": kind,
        "post_places": places if isinstance(places, int) else None,
        "post_sure": data.get("sure") is True,
        "post_housing": data.get("housing") is not False,
    }
    return _fits(message, kind)


# A wanted post that asks only for an address to register is not a housing lead (audit 2026-11-08, #132).
REGISTRATION_ONLY = ("بدون سکونت", "meldeadresse", "ohne einzug", "ohne wohnsitz", "nur anmeldung", "فقط ملده", "فقط برای ملده", "آدرس ملده")
HOME_WORDS = ("اتاق", "خونه", "خانه", "آپارتمان", "واحد", "سوئیت", "استودیو", "zimmer", "wohnung", "wg ", "room", "apartment", "studio")


def not_housing(message: OutreachMessage) -> bool:
    """No room or home is wanted: an address registration only, or a post with no type, rooms, area or budget."""
    listing = message.listing
    if message.kind not in SEEKER or listing is None:
        return False
    text = ((listing.post.text if listing.post_id else "") or "").casefold()
    if any(word in text for word in REGISTRATION_ONLY) and not any(word in text for word in HOME_WORDS):
        return True
    if listing.property_type == "other" and not listing.rooms and not (listing.area or "").strip() and not listing.price_max and not listing.price_amount:
        return True
    return (message.extra or {}).get("post_housing") is False


def clear(message: OutreachMessage) -> bool:
    """The post names one place and the model is sure of its kind, area and city. Anything less: no first message at
    all, and no work to fix the listing (Mohammad, 2026-11-08). Read after `agrees`, from the same model call."""
    if message.kind not in SEEKER | OWNER or message.listing is None:
        return True
    extra = message.extra or {}
    places = extra.get("post_places")
    return extra.get("post_sure") is True and (places is None or places <= 1)


AREA_PROMPT = """You read one housing post from a Telegram group and the area our database stored for it.
Say whether the stored area is one of the places the poster names (same neighbourhood, town or street, in any language or
spelling). When the post names several towns, the stored area must be one of them; a different neighbourhood is false.
Answer with JSON only: {"area_ok": true} or {"area_ok": false}."""


def area_ok(message: OutreachMessage) -> bool | None:
    """S1 names a place and a count for the person's area: the stored area must be one the post names (audit 2026-11-08,
    #126: Newmarket/Aurora/Richmond Hill stored as North York). None when the model is down."""
    listing = message.listing
    if message.kind != OutreachKind.S1 or listing is None or not (listing.area or "").strip():
        return True
    checked = (message.extra or {}).get("area_checked")
    if isinstance(checked, bool):
        return checked
    from ai.openrouter import complete

    prompt = f"Stored area: {listing.area}\n\nPost:\n{(listing.post.text or '')[:2500]}"
    result = complete(prompt, system=AREA_PROMPT, temperature=0, max_tokens=40)
    if not result.ok:
        return None
    try:
        verdict = json.loads((result.text or "").strip().strip("`").removeprefix("json").strip()).get("area_ok")
    except (ValueError, AttributeError):
        return None
    if not isinstance(verdict, bool):
        return None
    message.extra = {**(message.extra or {}), "area_checked": verdict}
    return verdict


def _fits(message: OutreachMessage, kind: str) -> bool:
    return (kind == "wanted") if message.kind in SEEKER else (kind == "offer")


def flag(message: OutreachMessage) -> None:
    """The stored kind is doubtful: the listing goes back to review and leaves the site until a person looks."""
    listing = message.listing
    listing.needs_review = True
    listing.save(update_fields=["needs_review", "updated_at"])
    message.extra = {**(message.extra or {}), "kind_flagged_at": timezone.now().isoformat(), "stored_kind": listing.kind}
