"""The housing feed as data: filters, the query behind them, and a listing's card.

Used by the Mini App templates and by /api/v1/. It holds no view and reads no request.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import F, Q
from django.utils import timezone

from home import deeplink, freshness
from home import search as home_search
from home.alerts import metro_channel as named_metro_channel
from home.alerts import term_q
from home.city_names import CITY_FA as CITY_NAMES
from home.contact_text import safe_area, safe_city
from home.models import (
    HomeChannel,
    HomeListing,
    HomeSource,
    ListerType,
    ListingKind,
    ListingStatus,
    MediaStatus,
    PostStatus,
    PropertyType,
    RentalMode,
)
from home.storage import get_storage
from item_requests.models import City
from miniapp.catalog import format_month_day, ltr_embed
from miniapp.i18n import t

FEED_DAYS = 30
NEW_DAYS = 2
CURRENCY_SYMBOL = {"EUR": "€", "CAD": "CA$", "USD": "US$", "GBP": "£"}
# Rental mode filter (how it is let), separate from the property type filter (what it is).
TERMS = ("lease", "sublet", "lease_transfer", "short")
# Feed links made before rental_mode existed carry the mode as a type (?type=sublet).
LEGACY_TYPE_TERMS = {"sublet": "sublet", "lease_transfer": "lease_transfer", "short_stay": "short"}
MAX_AREAS = 6


UNSET = object()


def countries() -> list[str]:
    found = list(HomeSource.objects.filter(enabled=True).values_list("country", flat=True).distinct())
    return sorted(set(found)) or ["CA", "DE"]


def city_names() -> dict[str, str]:
    """English city name, lower-cased, to its Persian name from the Chamedoon catalog."""
    catalog = {name_en.casefold(): name_fa for name_en, name_fa in City.objects.values_list("name_en", "name_fa") if name_fa}
    return {**CITY_NAMES, **catalog}


def city_label(name: str, locale: str, names: dict[str, str]) -> str:
    if locale == "fa":
        return names.get((name or "").casefold(), name)
    return name


def money(amount: Decimal | None, currency: str) -> str:
    """An amount with its currency, kept left-to-right so "1,450 CA$" never flips inside Persian text."""
    if amount is None:
        return ""
    number = f"{amount:,.0f}" if amount == amount.to_integral_value() else f"{amount:,.2f}"
    return ltr_embed(f"{number} {CURRENCY_SYMBOL.get(currency, currency)}")


def plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def seen_label(when, m: dict) -> str:
    days = max(0, (timezone.now().date() - timezone.localtime(when).date()).days)
    if days == 0:
        return t(m, "homes.seen.today")
    if days == 1:
        return t(m, "homes.seen.yesterday")
    return t(m, "homes.seen.days", n=days)


def price_line(listing: HomeListing, m: dict) -> str:
    if listing.kind == ListingKind.WANTED:
        return t(m, "homes.budgetUpTo", price=money(listing.price_max, listing.price_currency)) if listing.price_max else ""
    if listing.price_amount is None:
        return t(m, "homes.askPrice")
    period = {"month": "homes.perMonth", "week": "homes.perWeek", "night": "homes.perNight"}.get(listing.price_period, "")
    parts = [money(listing.price_amount, listing.price_currency)]
    if period:
        parts.append(t(m, period))
    bills = ""
    if listing.price_basis == "warm" or listing.bills_included == "yes":
        bills = t(m, "homes.billsIn")
    elif listing.price_basis == "cold" or listing.bills_included == "no":
        bills = t(m, "homes.billsOut")
    text = " ".join(parts)
    return f"{text} · {bills}" if bills else text


def photos_for(listing: HomeListing, storage=UNSET) -> list[dict]:
    """`storage` lets a loop look it up once: each lookup reads the settings row."""
    storage = get_storage() if storage is UNSET else storage
    if storage is None:
        return []
    items = getattr(listing.post, "stored_media", None)
    if items is None:
        items = listing.post.media.filter(status=MediaStatus.STORED).order_by("position")
    return [
        {"full": storage.url(item.storage_key), "thumb": storage.url(item.thumb_key or item.storage_key)}
        for item in items
        if item.storage_key
    ]


def card(listing: HomeListing, locale: str, m: dict, names: dict[str, str]) -> dict:
    photos = photos_for(listing)
    place = " · ".join(
        part for part in (city_label(safe_city(listing), locale, names), safe_area(listing)) if part
    )
    meta = []
    if listing.size_m2:
        meta.append(t(m, "homes.fields.sizeValue", n=plain(listing.size_m2)))
    if listing.furnished in {"yes", "partial", "no"}:
        meta.append(t(m, f"homes.furnishedValues.{listing.furnished}"))
    if listing.available_from:
        meta.append(f"{t(m, 'homes.fields.from')} {format_month_day(listing.available_from, locale)}")
    if listing.roommate_gender in {"female", "male", "couple"}:
        meta.append(t(m, f"homes.roommateValues.{listing.roommate_gender}"))
    if listing.rental_mode in {RentalMode.SUBLET, RentalMode.LEASE_TRANSFER, RentalMode.SHORT_TERM}:
        meta.insert(0, t(m, f"homes.rentalModes.{listing.rental_mode}"))
    chips = []
    if listing.first_seen and listing.first_seen >= timezone.now() - timedelta(days=NEW_DAYS):
        chips.append({"status": "active", "label": t(m, "homes.chips.new")})
    if listing.kind == ListingKind.OFFER:
        if listing.lister_type == ListerType.OWNER:
            chips.append({"status": "connected", "label": t(m, "homes.chips.owner")})
        elif listing.lister_type == ListerType.AGENT:
            chips.append({"status": "suggested", "label": t(m, "homes.chips.agent")})
        if listing.price_amount is None:
            chips.append({"status": "possible", "label": t(m, "homes.chips.noPrice")})
    return {
        "id": listing.pk,
        "href": f"/app/home/{listing.pk}/",
        "title": t(m, f"homes.types.{listing.property_type}"),
        "place": place,
        "price": price_line(listing, m),
        "meta": " · ".join(meta),
        "thumb": photos[0]["thumb"] if photos else "",
        "photo_count": len(photos),
        "chips": chips,
        "seen": seen_label(listing.last_seen, m),
        "fresh": freshness.freshness_line(listing, locale=locale),
        "expired": listing.status == ListingStatus.EXPIRED,
        "rented": listing.status == ListingStatus.RENTED,
        "reported": listing.status == ListingStatus.REPORTED,
    }


def metro_channel(country: str, city: str) -> HomeChannel | None:
    """The city channel whose metro is this city name, alias or slug ("toronto" → Toronto), if any."""
    wanted = deeplink.slug(city)
    if not wanted:
        return None
    named = named_metro_channel(country, city)
    if named is not None:
        return named
    for channel in HomeChannel.objects.filter(country=country).exclude(city=""):
        if deeplink.slug(channel.city) == wanted:
            return channel
    return None


def remembered_city(saved: dict | None, country: str) -> str:
    saved = saved or {}
    return saved.get("city", "") if isinstance(saved, dict) and saved.get("country") == country else ""


def _int(value: str | None) -> int | None:
    try:
        return int(value or 0) or None
    except (TypeError, ValueError):
        return None


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value or "")
    except ValueError:
        return None


def parse_filters(get, available: list[str], *, saved_country: str = "", saved_city: dict | None = None) -> dict:
    """Filters from a query string. The saved country and city are what this person looked at last, if known."""
    country = (get.get("country") or saved_country or "").upper()
    if country not in available:
        country = available[0]
    kind = get.get("kind") if get.get("kind") in {ListingKind.OFFER, ListingKind.WANTED} else ListingKind.OFFER
    types = [value for value in dict.fromkeys(get.getlist("type")) if value in {choice.value for choice in PropertyType}]
    term = get.get("term") if get.get("term") in TERMS else ""
    legacy = [LEGACY_TYPE_TERMS[value] for value in get.getlist("type") if value in LEGACY_TYPE_TERMS]
    if not term and legacy:
        term = legacy[0]
    areas = []
    for value in get.getlist("area"):
        value = value.strip()[:96]
        if value and value.casefold() not in {area.casefold() for area in areas}:
            areas.append(value)
    # A bare feed link (no country) opens the city last looked at, like the country.
    city = (get.get("city") or "").strip()[:64] if get.get("country") or "city" in get else remembered_city(saved_city, country)
    metro = metro_channel(country, city)
    gender = get.get("gender") if get.get("gender") in home_search.GENDERS and country == "CA" else ""
    filters = {
        "country": country,
        "kind": kind,
        "city": metro.city if metro else city,
        "types": types,
        "min": _int(get.get("min")),
        "max": _int(get.get("max")),
        "areas": areas[:MAX_AREAS],
        "q": (get.get("q") or "").strip()[:80],
        "term": term,
        "gender": gender,
        "move": _date(get.get("move")),
        "by": _date(get.get("by")),
        "sort": get.get("sort") if get.get("sort") in home_search.SORTS else "new",
    }
    for key in home_search.TOGGLES:
        countries_for = home_search.COUNTRY_TOGGLES.get(key)
        filters[key] = get.get(key) == "1" and (countries_for is None or country in countries_for)
    return filters


def base_queryset(filters: dict):
    """Listings people may see in this country and tab: active (freshness rules), trusted, seen this month."""
    return HomeListing.objects.filter(
        status=ListingStatus.ACTIVE,
        needs_review=False,
        post__status=PostStatus.ACCEPTED,
        country=filters["country"],
        kind=filters["kind"],
        last_seen__gte=timezone.now() - timedelta(days=FEED_DAYS),
    )


def city_q(filters: dict) -> Q:
    # A metro with a city channel covers its suburbs, the same names that route posts to that channel.
    metro = metro_channel(filters["country"], filters["city"])
    names = {filters["city"]}
    if metro:
        names |= {metro.city, *(part.strip() for part in re.split(r"[,\n]", metro.aliases or "") if part.strip())}
    place = Q()
    for name in names:
        place |= Q(city__iexact=name) | Q(city="", area__iexact=name)
    return place


def feed_queryset(filters: dict):
    listings = base_queryset(filters)
    if filters["city"]:
        listings = listings.filter(city_q(filters))
    if filters["types"]:
        listings = listings.filter(property_type__in=filters["types"])
    if filters.get("min") and filters["kind"] == ListingKind.OFFER:
        listings = listings.filter(price_amount__isnull=False, price_amount__gte=filters["min"])
    if filters["max"] and filters["kind"] == ListingKind.OFFER:
        listings = listings.filter(price_amount__isnull=False, price_amount__lte=filters["max"])
    if filters.get("areas"):
        place = Q()
        for area in filters["areas"]:
            place |= Q(area__icontains=area)
        listings = listings.filter(place)
    if filters.get("term"):
        listings = listings.filter(term_q(filters["term"]))
    if filters.get("q"):
        words = filters["q"]
        listings = listings.filter(
            Q(area__icontains=words)
            | Q(city__icontains=words)
            | Q(summary_fa__icontains=words)
            | Q(summary_en__icontains=words)
            | Q(post__text__icontains=words)
        )
    return listings.filter(home_search.search_q(filters))


def ordered(listings, filters: dict):
    if filters.get("sort") == "price":
        return listings.order_by(F("price_amount").asc(nulls_last=True), "-last_seen", "-id")
    return listings.order_by("-last_seen", "-id")
