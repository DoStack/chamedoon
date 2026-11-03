"""From the Mini App's posting form to what `create_item_request` takes, with one reason per refusal.

Same data as the bot's four steps (`flow.request_payload`), plus stops and categories for a trip.
"""

from __future__ import annotations

from datetime import date

from django.utils import timezone

from courier import flow, texts
from item_requests.models import Category, ItemRequest, RequestStatus, RequestType


class PostingError(Exception):
    def __init__(self, code: str, field: str) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


def _when(request_type: str, when: dict, today: date) -> dict:
    kind = when.get("kind") or "open"
    if kind == "exact":
        raw = when.get("date") or ""
        if not raw and request_type == RequestType.SUPPLY:
            raise PostingError("flight_date_required", "when")
        day = texts.parse_typed_date(raw, today)
        if day is None:
            past = _is_past(raw, today)
            raise PostingError("date_past" if past and request_type == RequestType.SUPPLY else "date_invalid", "when")
        return {"k": "x", "v": day.isoformat()}
    if request_type == RequestType.SUPPLY:
        raise PostingError("flight_date_required", "when")
    if kind == "month":
        month = when.get("month") or ""
        if month not in {key for key, _start, _end in texts.upcoming_months(today, count=12)}:
            raise PostingError("date_invalid", "when")
        return {"k": "m", "v": month}
    return {"k": "o"}


def _is_past(raw: str, today: date) -> bool:
    try:
        return date.fromisoformat(texts.latin(raw).strip().replace("/", "-")) < today
    except ValueError:
        return False


def _stops(request_type: str, origin: dict, destinations: list[dict]) -> list[dict]:
    pairs: list[dict] = []
    for stop in destinations:
        pair = {"country": stop["country"].upper(), "city": stop["city"]}
        if pair not in pairs:
            pairs.append(pair)
    if request_type == RequestType.DEMAND and len(pairs) > 1:
        raise PostingError("server_rejected", "destinations")
    if len({pair["country"] for pair in pairs}) > 1:
        raise PostingError("stops_one_country", "destinations")
    home = {"country": origin["country"].upper(), "city": origin["city"]}
    if all(pair == home for pair in pairs):
        raise PostingError("same_city", "destinations")
    return pairs


def build_payload(request_type: str, form: dict, *, today: date | None = None) -> dict:
    """The domain payload for a validated form. Raises PostingError."""
    today = today or timezone.localdate()
    origin = form["origin"]
    stops = _stops(request_type, origin, form["destinations"])
    payload = flow.request_payload(
        {
            "role": request_type,
            "o": (origin["country"].upper(), origin["city"]),
            "d": (stops[-1]["country"], stops[-1]["city"]),
            "size": form.get("size") or "1",
            "when": _when(request_type, form.get("when") or {}, today),
            "note": form.get("description") or "",
        },
        today,
    )
    payload["destination_cities"] = stops
    if request_type == RequestType.SUPPLY:
        payload.update(_trip_categories(form))
    elif form.get("category_codes"):
        payload["item_category_codes"] = form["category_codes"]
    return payload


def _trip_categories(form: dict) -> dict:
    """Whatever a traveler does not tick is refused, as in today's form. No list at all: the size's defaults."""
    chosen = form.get("category_codes")
    if chosen is None:
        return {"excluded_other_text": form.get("refused_other") or ""}
    if not chosen:
        raise PostingError("category_required", "category_codes")
    refused = Category.objects.filter(is_active=True).exclude(code__in=chosen).values_list("code", flat=True)
    return {
        "item_category_codes": chosen,
        "excluded_category_codes": list(refused),
        "excluded_other_text": form.get("refused_other") or "",
    }


def find_duplicate(user, payload: dict) -> ItemRequest | None:
    """The same person's active request with the same side, route and last date."""
    return ItemRequest.objects.filter(
        user=user,
        status=RequestStatus.ACTIVE,
        type=payload["type"],
        origin_country=payload["origin_country"],
        origin_city=payload["origin_city"],
        destination_country=payload["destination_country"],
        destination_city=payload["destination_city"],
        date_to=payload.get("date_to") or payload["desired_date"],
    ).first()
