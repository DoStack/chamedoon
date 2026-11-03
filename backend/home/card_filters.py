"""Filters, sorts and counts over public cards. The fallback feed runs these; the client runs the same rules in TS.

`home_api/testdata/filter_vectors.json` holds the shared cases: pytest runs them here and vitest runs them in the
client, so a change on one side fails until the other follows. Budget and price sorts read `price_monthly`, never
`price_amount`, so a night or week price compares fairly.
"""

from __future__ import annotations

from datetime import date, timedelta

MOVE_WINDOW_DAYS = 7
SORTS = ("new", "value", "cheap", "dear", "date")
TOGGLES = ("furnished", "owner", "bills", "warm", "reg", "nowbs")
# Tiles that mean an apartment with this many bedrooms. Any other type token is the card's property_type.
BEDROOM_TOKENS = {"bed1": 1, "bed2": 2, "bed3": 3}
FACET_SECTIONS = ("types", "term", "gender", "areas", "area_ids")


def _date(value: str | date | None) -> date | None:
    if isinstance(value, date) or value is None:
        return value
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def type_tokens(card: dict) -> list[str]:
    """Every tile a card counts under: its type, and for an apartment its bedroom tile. No bedroom count: type only."""
    tokens = [card["property_type"]]
    rooms = card.get("rooms")
    if card["property_type"] == "apartment" and rooms is not None and rooms >= 1:
        tokens.append(f"bed{min(int(rooms), 3)}")
    return tokens


def _matches_gender(card: dict, gender: str) -> bool:
    if gender in {"female", "male"}:
        return card["roommate_gender"] == gender
    if gender == "couple":
        whole = card["property_type"] != "room" and card["roommate_gender"] not in {"female", "male"} and card.get("occupants_wanted") != 1
        return card["roommate_gender"] == "couple" or bool(card.get("couples_ok")) or whole
    return True


def _matches_text(card: dict, words: str) -> bool:
    words = words.casefold()
    return any(words in (card.get(key) or "").casefold() for key in ("area", "city", "summary_fa", "summary_en"))


def matches(card: dict, filters: dict, *, skip: str = "") -> bool:
    """Does this card pass the filters? `skip` leaves one section out, for that section's own counts."""
    if card["kind"] != (filters.get("kind") or "offer"):
        return False
    types = filters.get("types") or []
    if types and skip != "types" and not set(types) & set(type_tokens(card)):
        return False
    if card["kind"] == "offer":
        price = card.get("price_monthly")
        low, high = filters.get("min"), filters.get("max")
        if low or high:
            if price is None:
                if not filters.get("unpriced"):
                    return False
            elif (low and price < low) or (high and price > high):
                return False
    areas = filters.get("areas") or []
    if areas and skip != "areas":
        place = (card.get("area") or "").casefold()
        if not any(area.casefold() in place for area in areas):
            return False
    area_ids = filters.get("area_ids") or []
    if area_ids and skip != "area_ids" and card.get("area_id") not in area_ids:
        return False
    term = filters.get("term")
    if term and skip != "term" and card["term"] != term:
        return False
    gender = filters.get("gender")
    if gender and skip != "gender" and not _matches_gender(card, gender):
        return False
    if filters.get("q") and not _matches_text(card, filters["q"]):
        return False
    if filters.get("furnished") and card["furnished"] not in {"yes", "partial"}:
        return False
    if filters.get("owner") and card["lister_type"] == "agent":
        return False
    if filters.get("bills") and not (card["bills_included"] == "yes" or card["price_basis"] == "warm"):
        return False
    if filters.get("warm") and card["price_basis"] == "cold":
        return False
    if filters.get("reg") and card["registration"] != "yes":
        return False
    if filters.get("nowbs") and card.get("wbs_required"):
        return False
    available = _date(card.get("available_from"))
    move = _date(filters.get("move"))
    if move:
        window = timedelta(days=MOVE_WINDOW_DAYS)
        if available is None or not move - window <= available <= move + window:
            return False
    by = _date(filters.get("by"))
    if by and available is not None and available > by:
        return False
    return True


def _newest(cards: list[dict]) -> list[dict]:
    return sorted(cards, key=lambda card: (card["last_seen"], card["id"]), reverse=True)


def _nulls_last(cards: list[dict], value) -> list[dict]:
    """Ascending by `value`, newest first among equals, cards with no value at the end in newest order."""
    known = [card for card in _newest(cards) if value(card) is not None]
    unknown = [card for card in _newest(cards) if value(card) is None]
    return sorted(known, key=value) + unknown


def sort_cards(cards: list[dict], sort: str = "new", *, move: str | date | None = None) -> list[dict]:
    if sort == "cheap":
        return _nulls_last(cards, lambda card: card.get("price_monthly"))
    if sort == "dear":
        return _nulls_last(cards, lambda card: None if card.get("price_monthly") is None else -card["price_monthly"])
    if sort == "value":
        return _nulls_last(cards, lambda card: card.get("value_ratio"))
    wanted = _date(move)
    if sort == "date" and wanted:
        return _nulls_last(cards, lambda card: abs((_date(card.get("available_from")) - wanted).days) if card.get("available_from") else None)
    return _newest(cards)


def facets(cards: list[dict], filters: dict) -> dict[str, dict[str, int]]:
    """Counts per option. Each section is counted from the other sections' filters, so its own choice never moves it."""
    out: dict[str, dict[str, int]] = {section: {} for section in FACET_SECTIONS}

    def add(section: str, option: str) -> None:
        out[section][option] = out[section].get(option, 0) + 1

    for card in cards:
        if matches(card, filters, skip="types"):
            for token in type_tokens(card):
                add("types", token)
        if matches(card, filters, skip="term"):
            add("term", card["term"])
        if matches(card, filters, skip="gender"):
            for gender in ("female", "male", "couple"):
                if _matches_gender(card, gender):
                    add("gender", gender)
        if matches(card, filters, skip="areas") and card.get("area"):
            add("areas", card["area"])
        if matches(card, filters, skip="area_ids") and card.get("area_id"):
            add("area_ids", str(card["area_id"]))
    return out


def add_value_ratio(cards: list[dict]) -> None:
    """«ارزنده‌ترین»: price per m² against the median of its own neighbourhood. Below 1 is cheaper than its area."""
    per_area: dict[str, list[float]] = {}
    for card in cards:
        card["value_ratio"] = None
        if card["kind"] == "offer" and card.get("price_monthly") and card.get("size_m2"):
            per_area.setdefault((card.get("area") or "").casefold(), []).append(card["price_monthly"] / card["size_m2"])
    medians = {area: _median(values) for area, values in per_area.items()}
    for card in cards:
        if card["kind"] == "offer" and card.get("price_monthly") and card.get("size_m2"):
            median = medians[(card.get("area") or "").casefold()]
            card["value_ratio"] = round((card["price_monthly"] / card["size_m2"]) / median, 4)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
