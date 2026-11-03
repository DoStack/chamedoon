"""Smart posting: a pasted post read into the F18 form by the same extraction review uses. It saves no listing and never holds a contact."""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from home import conf
from home.alerts import metro_channel
from home.contact_text import has_contact
from home.models import HomeParseUse, HomePost, ListingKind, PropertyType
from home.review import ask_model, build_prompt, llm_enabled, normalize_listing, parse_json
from home.submit import DESCRIPTION_MIN, app_source

logger = logging.getLogger(__name__)

# Columns of normalize_listing's result that the form takes as they are.
SAME_NAME = (
    "property_type",
    "rental_mode",
    "country",
    "city",
    "area",
    "price_period",
    "price_basis",
    "bills_included",
    "size_m2",
    "rooms",
    "furnished",
    "registration",
    "available_from",
    "available_to",
    "min_term_months",
    "roommate_gender",
)
NOT_STATED = {None, "", "unknown", "any", PropertyType.OTHER}


class ParseCapReached(Exception):
    pass


class ParseUnavailable(Exception):
    pass


class TextTooShort(Exception):
    pass


def daily_cap() -> int:
    return max(1, min(50, conf.row().parse_daily_cap or 10))


def _take_one(user) -> int:
    """Counts this reading against today's cap and answers how many are left. Failed readings count too, since they can cost the same."""
    cap = daily_cap()
    today = timezone.localdate()
    with transaction.atomic():
        use, _created = HomeParseUse.objects.select_for_update().get_or_create(user=user, day=today)
        if use.count >= cap:
            raise ParseCapReached
        HomeParseUse.objects.filter(pk=use.pk).update(count=F("count") + 1)
    return cap - use.count - 1


def _plain(value):
    """Decimals as numbers, as the form sends them."""
    return float(value) if hasattr(value, "as_tuple") else value


def _form_fields(fields: dict) -> dict:
    parsed = {name: _plain(fields.get(name)) for name in SAME_NAME if fields.get(name) not in NOT_STATED}
    price = fields.get("price_amount") if fields.get("kind") == ListingKind.OFFER else fields.get("price_max")
    if price is not None:
        parsed["price"] = float(price)
    if fields.get("deposit_amount") is not None:
        parsed["deposit"] = float(fields["deposit_amount"])
    return parsed


def _missing(parsed: dict, kind: str) -> list[str]:
    needed = ["price", "city"] + (["property_type", "area"] if kind == ListingKind.OFFER else [])
    return [name for name in needed if name not in parsed]


def _nothing_read(*, kind: str, contact_in_text: bool, left: int) -> dict:
    return {"understood": False, "parsed": {}, "missing": _missing({}, kind), "kind_guess": None, "city_guess": None, "contact_in_text": contact_in_text, "parses_left": left}


def _city_guess(fields: dict, said_city: str) -> dict | None:
    if not said_city:
        return None
    channel = metro_channel(fields["country"], fields["city"])
    return {"country": fields["country"], "city": channel.city if channel else fields["city"], "known": bool(channel and channel.enabled)}


def parse(user, *, text: str, kind: str, country: str, city: str = "") -> dict:
    text = text.strip()
    if len(text) < DESCRIPTION_MIN:
        raise TextTooShort
    if not llm_enabled():
        raise ParseUnavailable
    left = _take_one(user)
    post = HomePost(source=app_source(country), text=text, posted_at=timezone.now())
    try:
        result = ask_model(build_prompt(post), model=conf.review_model())
    except Exception as exc:  # noqa: BLE001 - any trouble with the model opens the manual form
        logger.warning("post_parse_failed: %s", type(exc).__name__)
        raise ParseUnavailable from exc
    data = parse_json(result.text) if result.ok else None
    if data is None:
        logger.warning("post_parse_failed: %s", "no answer" if not result.ok else "bad json")
        raise ParseUnavailable
    contact_in_text = has_contact(text)
    if not data.get("accept"):
        return _nothing_read(kind=kind, contact_in_text=contact_in_text, left=left)
    said_city = str(data.get("city") or "").strip()
    # The chosen kind and city fill what the text left out, and the review check needs a place.
    fields, _notes = normalize_listing(post, {**data, "kind": data.get("kind") or kind, "city": said_city or city})
    if fields is None:
        return _nothing_read(kind=kind, contact_in_text=contact_in_text, left=left)
    parsed = _form_fields(fields)
    return {
        "understood": True,
        "parsed": parsed,
        "missing": _missing(parsed, kind),
        "kind_guess": fields["kind"],
        "city_guess": _city_guess(fields, said_city),
        "contact_in_text": contact_in_text,
        "parses_left": left,
    }
