"""Before any first message: does the post really offer a place, or look for one? (Mohammad, 2026-11-06.)

A room offered as «جویای هم‌خانه» was stored as a request and its poster got seeker messages. One model call reads the
post text again before the first message goes; when it disagrees with the stored kind, nothing is sent and the
listing goes back to review.
"""

from __future__ import annotations

import json
import re

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
A neighbourhood written with its own town ("North Vancouver (Blueridge)") is one place. A hashtag or tag line naming the
whole metro or city ("#اجاره_ونکوور", "#VancouverRent", "#تورنتو") is the group tag, not a place, when the post also names
a town inside it. List the places you counted in "names", as written in the post.
Answer with JSON only: {"kind": "offer" or "wanted", "places": <number>, "names": [<place>, ...], "sure": true or false,
"housing": true or false}."""


def agrees(message: OutreachMessage) -> bool | None:
    """True when the post matches the message's audience, False when it does not, None when the model is down."""
    if message.kind not in SEEKER | OWNER or message.listing is None:
        return True
    checked = (message.extra or {}).get("kind_checked")
    if checked in {"offer", "wanted"} and "post_sure" in (message.extra or {}):
        return _fits(message, checked)
    from home.review import seeks_housing

    if seeks_housing(message.listing.post.text if message.listing.post_id else ""):
        # Asked «کسی سراغ داره؟»: wanted, whatever the model would say (audit 2026-11-12 20:16 #398).
        message.extra = {**(message.extra or {}), "kind_checked": "wanted", "post_sure": True, "post_housing": True}
        return _fits(message, "wanted")
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
    names = [str(name) for name in data.get("names") or [] if isinstance(name, str) and name.strip()][:8]
    metro = [name for name in names if metro_tag(name, message.listing)]
    if not (isinstance(places, int) and metro and len(names) > len(metro)):
        metro = []
    if metro:
        # The group's metro tag (#اجاره_ونکوور) next to a town inside that metro is one place, not two (#342).
        places = max(1, places - len(metro))
    message.extra = {
        **(message.extra or {}),
        "kind_checked": kind,
        "post_places": places if isinstance(places, int) else None,
        "post_names": names,
        **({"post_metro_tags": metro} if metro else {}),
        "post_sure": data.get("sure") is True,
        "post_housing": data.get("housing") is not False,
    }
    return _fits(message, kind)


# A wanted post that asks only for an address to register is not a housing lead (audit 2026-11-08, #132).
REGISTRATION_ONLY = ("بدون سکونت", "meldeadresse", "ohne einzug", "ohne wohnsitz", "nur anmeldung", "فقط ملده", "فقط برای ملده", "آدرس ملده")
HOME_WORDS = ("اتاق", "خونه", "خانه", "آپارتمان", "واحد", "سوئیت", "استودیو", "zimmer", "wohnung", "wg ", "room", "apartment", "studio")
# Any mention of registering, with no room or home named, is the same ask (#315: «کسی ملده ... داره؟ ... اگه جایی دارین»).
# «جایی» is not a home word.
REGISTRATION_WORDS = ("ملد", "anmeldung", "meldeadresse")
# No room in Germany rents for this little a month: a budget under it with no home named is a registration ask.
DE_HOUSING_FLOOR = 150


def not_housing(message: OutreachMessage) -> bool:
    """No room or home is wanted: an address registration only, or a post with no type, rooms, area or budget."""
    listing = message.listing
    if message.kind not in SEEKER or listing is None:
        return False
    if (listing.flags or {}).get("not_housing"):
        return True
    text = ((listing.post.text if listing.post_id else "") or "").casefold()
    names_home = any(word in text for word in HOME_WORDS)
    if any(word in text for word in REGISTRATION_ONLY) and not names_home:
        return True
    if any(word in text for word in REGISTRATION_WORDS) and not names_home:
        return True
    budget = listing.price_max or listing.price_amount
    if listing.country == "DE" and budget is not None and budget < DE_HOUSING_FLOOR and not names_home:
        return True
    if listing.property_type == "other" and not names_home and not listing.rooms and not (listing.area or "").strip() and not listing.price_max and not listing.price_amount:
        # «دنبال خونه هستم اگر کسی سراغ داره» names a home: a generic seeker, not «no housing» (audit 2026-11-13 11:15 #400).
        return True
    return (message.extra or {}).get("post_housing") is False


def mark_not_housing(listing) -> None:
    from home.models import HomeListing

    flags = dict(listing.flags or {})
    if flags.get("not_housing"):
        return
    flags["not_housing"] = "pre-send check"
    HomeListing.objects.filter(pk=listing.pk).update(flags=flags, updated_at=timezone.now())
    listing.flags = flags


# What a tag adds around the city's name: «#اجاره_ونکوور», «#VancouverRent», «#Toronto_Housing».
TAG_WORDS = re.compile(r"[#_]|اجاره|مسکن|خانه|خونه|rent(?:al)?s?|housing|homes?|wohnung(?:en)?|mie(?:te|ten)|logements?", re.IGNORECASE)


def metro_tag(name: str, listing) -> bool:
    """The name is the listing's own metro or city, written bare or as a group hashtag."""
    from home.city_names import city_fa
    from home.places import fold

    city = (getattr(listing, "city", "") or "").strip()
    if not city:
        return False
    bare = fold(TAG_WORDS.sub(" ", name or ""))
    return bool(bare) and bare in {fold(city), fold(city_fa(city))} - {""}


def unclear_detail(message: OutreachMessage) -> str:
    """Why `clear` said no, for the audit: the count, the places the model named, and whether it was sure."""
    extra = message.extra or {}
    names = ", ".join(extra.get("post_names") or []) or "-"
    dropped = ", ".join(extra.get("post_metro_tags") or [])
    return f"places={extra.get('post_places')} names=[{names}] sure={extra.get('post_sure')}" + (f" metro_tags=[{dropped}]" if dropped else "")


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
