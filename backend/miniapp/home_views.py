"""Chamedoon Home in the Mini App: the housing feed and a listing page, in Persian and English.

Built from the same pieces as Explore (header, segmented control, filter sheet, card
list, list loader) so it looks and behaves like the rest of Chamedoon.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.db.models import Count, F, Prefetch, Q
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_GET, require_http_methods

from home.models import (
    AlertTerm,
    HomeAlert,
    HomeListing,
    HomeMedia,
    HomeSource,
    ListerType,
    ListingKind,
    ListingStatus,
    MediaStatus,
    PostStatus,
    PropertyType,
    RentalMode,
    ReportReason,
)
from home import conf, deeplink, freshness
from home import search as home_search
from home.alerts import (
    ALL_FIELDS,
    CURRENCY_BY_COUNTRY,
    MAX_ALERTS_PER_USER,
    SEARCH_DAYS,
    TYPE_CHOICES,
    active_count,
    city_slug,
    is_paused,
    shown_offers,
    term_q,
    user_alerts,
)
from home.alerts import matches as alert_matches
from home.alerts import metro_channel as named_metro_channel
from home.models import HomeChannel
from home.storage import get_storage
from home.submit import MAX_PHOTOS, SubmitError, close_listing, create_listing, user_listings
from item_requests.models import City
from matching.contact import country_flag
from miniapp.auth import miniapp_login_required
from miniapp.catalog import format_month_day, ltr_embed
from miniapp.i18n import locale_from_request, messages_for, t
from miniapp.views import _ctx, _list_fragment, _wants_list_fragment

FEED_LIMIT = 60
FEED_DAYS = 30
NEW_DAYS = 2
COUNTRY_SESSION_KEY = "home_country"
CITY_SESSION_KEY = "home_city"
CURRENCY_SYMBOL = {"EUR": "€", "CAD": "CA$", "USD": "US$", "GBP": "£"}
# Rented and reported listings keep opening from a link, with a banner; the feeds show active ones only.
SHOWN_STATUSES = (ListingStatus.ACTIVE, ListingStatus.EXPIRED, ListingStatus.RENTED, ListingStatus.REPORTED)
REPORT_REASONS = ("scam", "wrong", "duplicate", "other")
# Rental mode filter (how it is let), separate from the property type filter (what it is).
TERMS = ("lease", "sublet", "lease_transfer", "short")
# Feed links made before rental_mode existed carry the mode as a type (?type=sublet).
LEGACY_TYPE_TERMS = {"sublet": "sublet", "lease_transfer": "lease_transfer", "short_stay": "short"}
AREA_OPTIONS = 12
MAX_AREAS = 6
NEW_LIMIT = 300
# Rental modes offered per country: Germany has no separate short stay (Zwischenmiete covers it).
TERMS_BY_COUNTRY = {"CA": ("lease", "sublet", "lease_transfer", "short"), "DE": ("lease", "sublet", "lease_transfer")}
PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def countries() -> list[str]:
    found = list(HomeSource.objects.filter(enabled=True).values_list("country", flat=True).distinct())
    return sorted(set(found)) or ["CA", "DE"]


def city_names() -> dict[str, str]:
    """English city name, lower-cased, to its Persian name from the Chamedoon catalog."""
    return {name_en.casefold(): name_fa for name_en, name_fa in City.objects.values_list("name_en", "name_fa") if name_fa}


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


def photos_for(listing: HomeListing) -> list[dict]:
    storage = get_storage()
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
        part for part in (city_label(listing.city, locale, names), listing.area) if part
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


def remembered_city(request: HttpRequest, country: str) -> str:
    saved = request.session.get(CITY_SESSION_KEY) or {}
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


def parse_filters(request: HttpRequest, available: list[str]) -> dict:
    get = request.GET
    country = (get.get("country") or request.session.get(COUNTRY_SESSION_KEY) or "").upper()
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
    city = (get.get("city") or "").strip()[:64] if get.get("country") or "city" in get else remembered_city(request, country)
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


def feed_count(request: HttpRequest, filters: dict) -> int:
    """The number of listings for these filters, counted once per request."""
    key = _query(filters)
    cache = getattr(request, "_home_counts", None)
    if cache is None:
        cache = request._home_counts = {}
    if key not in cache:
        cache[key] = feed_queryset(filters).count()
    return cache[key]


def ordered(listings, filters: dict):
    if filters.get("sort") == "price":
        return listings.order_by(F("price_amount").asc(nulls_last=True), "-last_seen", "-id")
    return listings.order_by("-last_seen", "-id")


CLEARED = {
    "city": "",
    "types": [],
    "max": None,
    "min": None,
    "areas": [],
    "term": "",
    "q": "",
    "gender": "",
    "move": None,
    "by": None,
    **{key: False for key in home_search.TOGGLES},
}


def _query(filters: dict, **changes) -> str:
    merged = {**CLEARED, "sort": "new", **filters, **changes}
    pairs = [("country", merged["country"]), ("kind", merged["kind"])]
    if merged["city"]:
        pairs.append(("city", merged["city"]))
    pairs += [("type", value) for value in merged["types"]]
    if merged.get("min"):
        pairs.append(("min", merged["min"]))
    if merged["max"]:
        pairs.append(("max", merged["max"]))
    pairs += [("area", value) for value in merged.get("areas") or []]
    if merged.get("term"):
        pairs.append(("term", merged["term"]))
    if merged.get("q"):
        pairs.append(("q", merged["q"]))
    for key in home_search.TOGGLES:
        if merged.get(key):
            pairs.append((key, "1"))
    if merged.get("gender"):
        pairs.append(("gender", merged["gender"]))
    if merged.get("move"):
        pairs.append(("move", merged["move"].isoformat()))
    if merged["by"]:
        pairs.append(("by", merged["by"].isoformat()))
    if merged.get("sort") and merged["sort"] != "new":
        pairs.append(("sort", merged["sort"]))
    return "?" + urlencode(pairs)


def feed_href(filters: dict, **changes) -> str:
    return f"/app/home/list/{_query(filters, **changes)}"


def term_label(term: str, country: str, m: dict) -> str:
    """Rental mode names per country: Germany says Zwischenmiete and Nachmieter."""
    if country == "DE" and term in {"sublet", "lease_transfer"}:
        return t(m, f"homes.termsDE.{term}")
    return t(m, f"homes.terms.{term}")


def term_choices(country: str) -> tuple[str, ...]:
    return TERMS_BY_COUNTRY.get(country, TERMS)


def price_label(filters: dict, currency: str, m: dict) -> str:
    low, high = filters.get("min"), filters.get("max")
    if low and high:
        return t(m, "homes.chipPriceRange", low=money(Decimal(low), currency), high=money(Decimal(high), currency))
    if high:
        return t(m, "homes.chipPriceMax", price=money(Decimal(high), currency))
    return t(m, "homes.chipPriceMin", price=money(Decimal(low), currency))


def filter_chips(filters: dict, locale: str, m: dict, names: dict[str, str]) -> list[dict]:
    """One removable chip per filter that is set; each one links to the feed without it."""
    currency = CURRENCY_BY_COUNTRY.get(filters["country"], "")
    chips: list[dict] = []

    def add(key: str, label: str, href: str) -> None:
        chips.append({"key": key, "label": label, "remove_href": href, "aria": t(m, "homes.chipRemove", label=label)})

    if filters["city"]:
        add("city", city_label(filters["city"], locale, names), feed_href(filters, city="", areas=[]))
    for value in filters["types"]:
        add("type", t(m, f"homes.types.{value}"), feed_href(filters, types=[item for item in filters["types"] if item != value]))
    if filters.get("min") or filters["max"]:
        add("price", price_label(filters, currency, m), feed_href(filters, min=None, max=None))
    for value in filters.get("areas") or []:
        add("area", value, feed_href(filters, areas=[item for item in filters["areas"] if item != value]))
    if filters.get("term"):
        add("term", term_label(filters["term"], filters["country"], m), feed_href(filters, term=""))
    for key in ("furnished", "bills", "warm", "reg", "nowbs", "owner"):
        if filters.get(key):
            add(key, t(m, f"homes.chipLabels.{key}"), feed_href(filters, **{key: False}))
    if filters.get("gender"):
        add("gender", t(m, f"homes.roommateValues.{filters['gender']}"), feed_href(filters, gender=""))
    if filters.get("move"):
        add("move", t(m, "homes.moveChip", date=format_month_day(filters["move"], locale)), feed_href(filters, move=None))
    if filters["by"]:
        add("by", f"{t(m, 'homes.availableBy')} {format_month_day(filters['by'], locale)}", feed_href(filters, by=None))
    if filters.get("q"):
        add("q", f"«{filters['q']}»", feed_href(filters, q=""))
    return chips


def digits(value, locale: str) -> str:
    text = str(value)
    return text.translate(PERSIAN_DIGITS) if locale == "fa" else text


def count_label(count: int, locale: str, m: dict) -> str:
    return t(m, "homes.showCount", n=digits(count, locale))


def price_bars(filters: dict) -> list[dict]:
    """Bars for the budget section: monthly prices of this week's offers in the city (one query)."""
    if filters["kind"] != ListingKind.OFFER:
        return []
    rows = base_queryset(filters).filter(
        price_amount__isnull=False,
        price_period="month",
        first_seen__gte=timezone.now() - timedelta(days=home_search.HISTOGRAM_DAYS),
    )
    if filters["city"]:
        rows = rows.filter(city_q(filters))
    prices = list(rows.order_by("-first_seen").values_list("price_amount", flat=True)[: home_search.HISTOGRAM_LIMIT])
    return home_search.histogram(prices, filters.get("min"), filters.get("max"))


def alert_link(filters: dict) -> str:
    """The bot deep link that sets up an alert with these filters:
    alert_home_<cc>_<city>, plus _m<max>, _t<type> and _s<term> when the feed has them."""
    channel = metro_channel(filters["country"], filters["city"]) if filters["city"] else None
    parts = ["alert_home", filters["country"].lower()]
    if channel is not None:
        parts.append(city_slug(channel.city))
    elif filters["city"]:
        parts.append(city_slug(filters["city"]))
    if len(parts) == 3:
        if filters["max"]:
            parts.append(f"m{filters['max']}")
        chosen = set(filters["types"])
        for code, (types, _label) in TYPE_CHOICES.items():
            if types and chosen and chosen <= set(types):
                parts.append(f"t{code}")
                break
        if filters.get("term") == "lease":
            parts.append("sl")
        elif filters.get("term") == "sublet":
            parts.append("ss")
        elif filters.get("term") == "short":
            parts.append("st")
    return f"https://t.me/{conf.bot_username()}?start={'_'.join(parts)[:64]}"


def sheet_options(filters: dict, locale: str, m: dict, names: dict[str, str]) -> dict:
    """Choices of the filter sheet, with counts: cities, types and neighbourhoods (three grouped queries)."""
    base = base_queryset(filters)
    city_options = [
        {"value": row["city"], "label": city_label(row["city"], locale, names), "count": row["n"]}
        for row in base.exclude(city="").values("city").annotate(n=Count("id")).order_by("-n", "city")[:30]
    ]
    type_options = [
        {"value": row["property_type"], "label": t(m, f"homes.types.{row['property_type']}"), "count": row["n"]}
        for row in base.values("property_type").annotate(n=Count("id")).order_by("-n")
    ]
    area_base = base.filter(city_q(filters)) if filters["city"] else base
    chosen = {area.casefold() for area in filters.get("areas") or []}
    area_options = [
        {"value": row["area"], "count": row["n"], "on": row["area"].casefold() in chosen}
        for row in area_base.exclude(area="").values("area").annotate(n=Count("id")).order_by("-n", "area")[:AREA_OPTIONS]
    ]
    listed = {option["value"].casefold() for option in area_options}
    # Neighbourhoods typed in (or from a saved search) stay visible as chips even with no listing today.
    area_options = [{"value": area, "count": "", "on": True} for area in filters.get("areas") or [] if area.casefold() not in listed] + area_options
    type_values = {option["value"] for option in type_options}
    type_options += [{"value": value, "label": t(m, f"homes.types.{value}"), "count": 0} for value in filters["types"] if value not in type_values]
    return {
        "city_options": city_options,
        "type_options": type_options,
        "area_options": area_options,
        "term_options": [{"value": value, "label": term_label(value, filters["country"], m)} for value in term_choices(filters["country"])],
        "gender_options": [{"value": value, "label": t(m, f"homes.roommateValues.{value}")} for value in home_search.GENDERS],
    }


@miniapp_login_required
@xframe_options_exempt
@require_GET
def homes(request: HttpRequest) -> HttpResponse:
    locale = locale_from_request(request)
    m = messages_for(locale)
    available = countries()
    filters = parse_filters(request, available)
    request.session[COUNTRY_SESSION_KEY] = filters["country"]
    request.session[CITY_SESSION_KEY] = {"country": filters["country"], "city": filters["city"]}
    list_count = feed_count(request, filters)
    names = city_names()
    chips = filter_chips(filters, locale, m, names)
    clear_url = feed_href({"country": filters["country"], "kind": filters["kind"], "sort": filters["sort"]})
    ctx = _ctx(
        request,
        back_href="/app/home/",
        filters=filters,
        chips=chips,
        filter_count=len(chips),
        list_count=list_count,
        homes_title=t(m, "homes.title", count=list_count),
        clear_filters_url=clear_url,
    )
    if _wants_list_fragment(request):
        stored = HomeMedia.objects.filter(status=MediaStatus.STORED).order_by("position")
        items = ordered(
            feed_queryset(filters).select_related("post").prefetch_related(Prefetch("post__media", queryset=stored, to_attr="stored_media")),
            filters,
        )[:FEED_LIMIT]
        ctx["rows"] = [card(item, locale, m, names) for item in items]
        return _list_fragment(request, "miniapp/includes/homes_list.html", ctx)
    telegram_id = request.chamedoon_user.telegram_user_id
    saved = None
    if request.GET.get("saved"):
        saved = HomeAlert.objects.filter(pk=_int(request.GET.get("saved")) or 0, telegram_user_id=telegram_id).first() if telegram_id else None
    ctx.update(
        sheet_options(filters, locale, m, names),
        country_options=[
            {
                "code": code,
                "label": t(m, f"homes.countries.{code}"),
                "flag": country_flag(code),
                "href": feed_href({"country": code, "kind": filters["kind"]}),
                "on": code == filters["country"],
            }
            for code in available
        ],
        current_country={"label": t(m, f"homes.countries.{filters['country']}"), "flag": country_flag(filters["country"])},
        kind_links={
            "offer": feed_href(filters, kind=ListingKind.OFFER),
            "wanted": feed_href(filters, kind=ListingKind.WANTED),
        },
        sort_links=[
            {"value": value, "label": t(m, f"homes.sorts.{value}"), "href": feed_href(filters, sort=value), "on": filters["sort"] == value}
            for value in home_search.SORTS
        ],
        price_bars=price_bars(filters),
        currency_symbol=CURRENCY_SYMBOL.get(CURRENCY_BY_COUNTRY.get(filters["country"], ""), ""),
        count_label=count_label(list_count, locale, m),
        count_url=f"/app/home/list/count/{_query(filters)}",
        alert_url=alert_link(filters),
        save_url=f"/app/home/alerts/save/{_query(filters)}",
        can_save=filters["kind"] == ListingKind.OFFER and bool(filters["city"]),
        save_note=_alert_note(request.GET.get("note") or "", m, locale),
        results_label=t(m, "homes.resultsCount", n=digits(list_count, locale)),
        saved_alert=saved,
        home_bot_url=f"https://t.me/{conf.bot_username()}",
        move_window_days=home_search.MOVE_WINDOW_DAYS,
    )
    return render(request, "miniapp/homes.html", ctx)


@miniapp_login_required
@xframe_options_exempt
@require_GET
def homes_count(request: HttpRequest) -> JsonResponse:
    """«نمایش N آگهی» on the sheet button, live while filters change."""
    locale = locale_from_request(request)
    m = messages_for(locale)
    filters = parse_filters(request, countries())
    count = feed_count(request, filters)
    response = JsonResponse({"count": count, "label": count_label(count, locale, m)})
    response["Cache-Control"] = "private, no-store"
    return response


# ---------------------------------------------------------------------------
# «گوش به زنگ» from the Mini App: the same HomeAlert rows the housing bot makes and lists.


def save_search(user, filters: dict) -> tuple[HomeAlert | None, str]:
    """Save the feed's filters as an alert. Returns (alert, "") or (None, reason): city, cap, account or kind.

    Every question counts as answered: the filters set here are the answers, and the ones left
    alone are «مهم نیست», since the person saw them all on the sheet (bot semantics, home/alerts.py)."""
    telegram_id = getattr(user, "telegram_user_id", None)
    if not telegram_id:
        return None, "account"
    if filters["kind"] != ListingKind.OFFER:
        return None, "kind"
    if not filters["city"]:
        return None, "city"
    channel = named_metro_channel(filters["country"], filters["city"])
    city = channel.city if channel else filters["city"]
    search = home_search.to_search(filters)
    same = next(
        (
            alert
            for alert in HomeAlert.objects.filter(telegram_user_id=telegram_id, country=filters["country"], city=city, wanted_listing__isnull=True)
            if alert.search == search
        ),
        None,
    )
    if same is not None:
        if not same.active:
            if active_count(telegram_id) >= MAX_ALERTS_PER_USER:
                return None, "cap"
            same.active = True
            same.paused_until = None
            same.save(update_fields=["active", "paused_until"])
        return same, ""
    if active_count(telegram_id) >= MAX_ALERTS_PER_USER:
        return None, "cap"
    term = {"lease": AlertTerm.LEASE, "sublet": AlertTerm.SUBLET, "short": AlertTerm.SHORT}.get(filters.get("term") or "", AlertTerm.ANY)
    alert = HomeAlert.objects.create(
        telegram_user_id=telegram_id,
        chat_id=telegram_id,
        user=user,
        country=filters["country"],
        city=city,
        max_price=Decimal(filters["max"]) if filters["max"] else None,
        currency=CURRENCY_BY_COUNTRY.get(filters["country"], ""),
        property_types=list(filters["types"]),
        term=term,
        area=", ".join(filters.get("areas") or []),
        gender=filters.get("gender") or "any",
        needs_registration=bool(filters.get("reg")),
        answered=list(ALL_FIELDS),
        complete=True,
        search=search,
        viewed_at=timezone.now(),
    )
    return alert, ""


def alert_filters(alert: HomeAlert) -> dict:
    """Feed filters of an alert: its saved search, or (made in the bot) its type, budget, area and term."""
    filters = {"country": alert.country, "kind": ListingKind.OFFER, "city": alert.city, "sort": "new"}
    if alert.search:
        filters.update(home_search.from_search(alert.search))
        return filters
    filters.update(home_search.from_search({}))
    filters["types"] = [value for value in alert.property_types or [] if value in {choice.value for choice in PropertyType}]
    filters["max"] = int(alert.max_price) if alert.max_price else None
    filters["areas"] = [part.strip() for part in re.split(r",|\s+یا\s+", alert.area or "") if part.strip()][:MAX_AREAS]
    filters["term"] = {AlertTerm.LEASE: "lease", AlertTerm.SUBLET: "sublet", AlertTerm.SHORT: "short"}.get(alert.term, "")
    filters["reg"] = bool(alert.needs_registration) and alert.country == "DE"
    return filters


def new_counts(alerts: list[HomeAlert]) -> dict[int, int]:
    """«n جدید»: listings that matched each alert since it was last opened here (one query per alert)."""
    channels = list(HomeChannel.objects.exclude(city=""))
    since_default = timezone.now() - timedelta(days=SEARCH_DAYS)
    counts = {}
    for alert in alerts:
        if not alert.active:
            counts[alert.pk] = 0
            continue
        since = max(alert.viewed_at or alert.created_at, since_default)
        channel = named_metro_channel(alert.country, alert.city, channels)
        rows = shown_offers().filter(country=alert.country, first_seen__gt=since).order_by("-first_seen")[:NEW_LIMIT]
        counts[alert.pk] = sum(1 for listing in rows if alert_matches(alert, listing, channel))
    return counts


NOTES = ("city", "cap", "account", "kind", "deleted", "off", "on")


def _alert_note(code: str, m: dict, locale: str = "fa") -> str:
    return t(m, f"homes.alerts.notes.{code}", n=digits(MAX_ALERTS_PER_USER, locale)) if code in NOTES else ""


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["POST"])
def alert_save(request: HttpRequest) -> HttpResponse:
    """The floating «خبرم کن از آگهی‌های جدید این جستجو»: the filters ride in the query string."""
    filters = parse_filters(request, countries())
    alert, error = save_search(request.chamedoon_user, filters)
    if alert is None:
        return redirect(feed_href(filters) + f"&note={error}")
    return redirect(feed_href(filters) + f"&saved={alert.pk}")


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def alerts_page(request: HttpRequest) -> HttpResponse:
    """«گوش به زنگ»: the person's alerts (bot and Mini App alike) with on/off, delete and «n جدید»."""
    locale = locale_from_request(request)
    m = messages_for(locale)
    telegram_id = request.chamedoon_user.telegram_user_id
    if request.method == "POST":
        note = ""
        alert = HomeAlert.objects.filter(pk=_int(request.POST.get("alert")) or 0, telegram_user_id=telegram_id).first() if telegram_id else None
        action = request.POST.get("action") or ""
        if alert is not None and action == "delete":
            alert.delete()
            note = "deleted"
        elif alert is not None and action == "toggle":
            if alert.active and not is_paused(alert):
                alert.active = False
                note = "off"
            elif not alert.active and alert.wanted_listing_id is None and active_count(telegram_id) >= MAX_ALERTS_PER_USER:
                note = "cap"
            else:
                alert.active = True
                alert.paused_until = None
                note = "on"
            alert.save(update_fields=["active", "paused_until"])
        return redirect("/app/home/alerts/" + (f"?note={note}" if note else ""))
    alerts = list(user_alerts(telegram_id)) if telegram_id else []
    names = city_names()
    news = new_counts(alerts)
    rows = []
    for number, alert in enumerate(alerts, start=1):
        filters = alert_filters(alert)
        chips = filter_chips(filters, locale, m, names)
        paused = is_paused(alert)
        on = alert.active and not paused
        if not alert.active:
            state = t(m, "homes.alerts.off")
        elif paused:
            state = t(m, "homes.alerts.paused", date=format_month_day(timezone.localtime(alert.paused_until).date(), locale))
        else:
            state = t(m, "homes.alerts.instant")
        rows.append(
            {
                "pk": alert.pk,
                "number": digits(number, locale),
                "labels": [chip["label"] for chip in chips] or [city_label(alert.city, locale, names)],
                "state": state,
                "on": on,
                "new": news.get(alert.pk, 0),
                "new_label": t(m, "homes.alerts.new", n=digits(news.get(alert.pk, 0), locale)),
                "open_href": f"/app/home/alerts/{alert.pk}/open/",
                "from_wanted": bool(alert.wanted_listing_id),
                "from_bot": not alert.search and not alert.wanted_listing_id,
            }
        )
    mine = sum(1 for alert in alerts if alert.active and not alert.wanted_listing_id)
    ctx = _ctx(
        request,
        back_href="/app/home/",
        rows=rows,
        heading=t(m, "homes.alerts.heading", n=digits(mine, locale), max=digits(MAX_ALERTS_PER_USER, locale)),
        note=_alert_note(request.GET.get("note") or "", m, locale),
        new_href=feed_href({"country": request.session.get(COUNTRY_SESSION_KEY) or countries()[0], "kind": ListingKind.OFFER}),
        home_bot_url=f"https://t.me/{conf.bot_username()}",
        no_account=not telegram_id,
    )
    return render(request, "miniapp/homes_alerts.html", ctx)


@miniapp_login_required
@xframe_options_exempt
@require_GET
def alert_open(request: HttpRequest, pk: int) -> HttpResponse:
    """Tapping an alert reopens the feed with its filters; its «n جدید» starts again from now."""
    telegram_id = request.chamedoon_user.telegram_user_id
    alert = HomeAlert.objects.filter(pk=pk, telegram_user_id=telegram_id).first() if telegram_id else None
    if alert is None:
        raise Http404()
    alert.viewed_at = timezone.now()
    alert.save(update_fields=["viewed_at"])
    return redirect(feed_href(alert_filters(alert)))


def _contact(listing: HomeListing) -> dict:
    post = listing.post
    source_link = post.source_url if post.source_url and "/c/" not in post.source_url else ""
    phone = listing.contact_phone
    return {
        "telegram": f"https://t.me/{listing.contact_telegram}" if listing.contact_telegram else "",
        "telegram_handle": listing.contact_telegram,
        "phone": phone,
        "phone_href": f"tel:{phone}" if phone else "",
        "original": source_link,
    }


def detail_rows(listing: HomeListing, locale: str, m: dict) -> list[dict]:
    rows = []

    def add(label_key: str, value: str) -> None:
        if value:
            rows.append({"label": t(m, f"homes.fields.{label_key}"), "value": value})

    price = price_line(listing, m)
    add("budget" if listing.kind == ListingKind.WANTED else "price", price)
    if listing.deposit_amount:
        add("deposit", money(listing.deposit_amount, listing.price_currency))
    elif listing.deposit_months:
        add("deposit", t(m, "homes.fields.depositMonths", n=plain(listing.deposit_months)))
    if listing.size_m2:
        add("size", t(m, "homes.fields.sizeValue", n=plain(listing.size_m2)))
    if listing.rooms:
        add("rooms", plain(listing.rooms))
    if listing.furnished in {"yes", "partial", "no"}:
        add("furnished", t(m, f"homes.furnishedValues.{listing.furnished}"))
    if listing.registration in {"yes", "no"}:
        add("registration", t(m, f"homes.{listing.registration}"))
    if listing.available_from:
        add("from", f"{format_month_day(listing.available_from, locale)} {listing.available_from.year}")
    if listing.available_to:
        add("until", f"{format_month_day(listing.available_to, locale)} {listing.available_to.year}")
    if listing.min_term_months:
        add("minTerm", t(m, "homes.fields.minTermValue", n=listing.min_term_months))
    if listing.roommate_gender in {"female", "male", "couple"}:
        add("roommate", t(m, f"homes.roommateValues.{listing.roommate_gender}"))
    if listing.kind == ListingKind.OFFER and listing.lister_type in {"owner", "agent"}:
        add("lister", t(m, f"homes.listerValues.{listing.lister_type}"))
    summary = listing.summary_en if locale == "en" and listing.summary_en else listing.summary_fa
    add("summary", summary)
    return rows


def _record_report(listing: HomeListing, user, reason: str) -> str:
    """One tap from the listing page: rented, still available, or a report with its reason.
    Returns the outcome (see home.freshness), or an empty string for an unknown reason."""
    telegram_id = user.telegram_user_id
    if reason == ReportReason.TAKEN:
        return freshness.mark_taken(listing, telegram_id, user)
    if reason == ReportReason.AVAILABLE:
        return "confirmed" if freshness.confirm_available(listing, telegram_id, user) else "closed"
    if reason in REPORT_REASONS or reason == ReportReason.PROBLEM:
        return freshness.report(listing, reason, telegram_id, user)
    return ""


def _listing(pk: int) -> HomeListing | None:
    return HomeListing.objects.select_related("post", "post__source", "canonical").filter(pk=pk).first()


def _openable(listing: HomeListing) -> bool:
    """A channel post must keep opening. Other rows open only while they are still a real listing."""
    if listing.published_at:
        return True
    if listing.status not in SHOWN_STATUSES:
        return False
    return listing.post.status == PostStatus.ACCEPTED


@xframe_options_exempt
@require_GET
def home_start(request: HttpRequest, pk: int) -> HttpResponse:
    """startapp=home_<id> is sometimes opened as the path /app/home/home_<id>/, which matched nothing."""
    return redirect(f"/app/home/{pk}/")


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def home_detail(request: HttpRequest, pk: int) -> HttpResponse:
    locale = locale_from_request(request)
    m = messages_for(locale)
    listing = _listing(pk)
    if listing is None:
        raise Http404()
    if listing.status == ListingStatus.DUPLICATE and listing.canonical_id:
        target = listing.canonical or _listing(listing.canonical_id)
        if target is not None and target.pk != listing.pk and _openable(target):
            return redirect(f"/app/home/{target.pk}/")
    if not _openable(listing):
        raise Http404()
    if request.method == "POST":
        outcome = _record_report(listing, request.chamedoon_user, (request.POST.get("reason") or "").strip())
        return redirect(f"/app/home/{listing.pk}/?reported=1" + (f"&outcome={outcome}" if outcome and outcome != "noted" else ""))
    names = city_names()
    warning = ""
    if (listing.flags or {}).get("deposit_warning") and listing.region in {"ON", "QC"}:
        warning = t(m, f"homes.warnings.{listing.region}")
    source = listing.post.source
    info = card(listing, locale, m, names)
    photos = photos_for(listing)
    telegram_id = request.chamedoon_user.telegram_user_id
    contact = _contact(listing)
    has_contact = bool(contact["telegram"] or contact["phone"] or contact["original"])
    # Full scam warning the first time this person looks at a way to contact someone, a short line afterwards.
    safety = freshness.contact_warning(telegram_id, locale=locale, record_seen=has_contact)
    deposit = t(m, f"homes.warnings.{listing.region}") if listing.region in {"ON", "QC"} and not warning else ""
    ctx = _ctx(
        request,
        back_href="/app/home/list/",
        listing=listing,
        info=info,
        photos=photos,
        photo_count_label=t(m, "homes.chips.photos", n=len(photos)),
        seen_line=t(m, "homes.seenLine", when=info["seen"]),
        rows=detail_rows(listing, locale, m),
        contact=contact,
        warning=warning,
        safety=safety,
        deposit_note=deposit,
        owner=freshness.is_owner(listing, telegram_id),
        can_confirm=listing.status == ListingStatus.ACTIVE
        and (freshness.is_owner(listing, telegram_id) or not freshness.is_app_listing(listing)),
        can_report=listing.status == ListingStatus.ACTIVE,
        report_reasons=[{"value": value, "label": t(m, f"freshness.reasons.{value}")} for value in REPORT_REASONS],
        outcome=request.GET.get("outcome") or "",
        reported=request.GET.get("reported") == "1",
        created=request.GET.get("created") == "1",
        source_name=t(m, "homes.name") if source.username.startswith("app_") else (source.title or f"@{source.username}"),
        wanted=listing.kind == ListingKind.WANTED,
    )
    return render(request, "miniapp/homes_detail.html", ctx)


@miniapp_login_required
@xframe_options_exempt
@require_GET
def home_landing(request: HttpRequest) -> HttpResponse:
    """First page of the housing Mini App: same layout as the Chamedoon home, housing content."""
    locale = locale_from_request(request)
    m = messages_for(locale)
    available = countries()
    chosen = (request.GET.get("country") or request.session.get(COUNTRY_SESSION_KEY) or "").upper()
    if chosen not in available:
        chosen = available[0]
    request.session[COUNTRY_SESSION_KEY] = chosen
    counts = dict(
        HomeListing.objects.filter(
            status=ListingStatus.ACTIVE,
            needs_review=False,
            post__status=PostStatus.ACCEPTED,
            last_seen__gte=timezone.now() - timedelta(days=FEED_DAYS),
        )
        .values_list("country")
        .annotate(n=Count("id"))
    )
    country_rows = [
        {
            "code": code,
            "label": t(m, f"homes.countries.{code}"),
            "flag": country_flag(code),
            "count": t(m, "homes.landing.count", n=counts.get(code, 0)),
            "href": f"/app/home/?country={code}",
            "on": code == chosen,
        }
        for code in available
    ]
    city = remembered_city(request, chosen)
    city_query = f"&{urlencode({'city': city})}" if city else ""
    channel = HomeChannel.objects.filter(country=chosen, city="", enabled=True).first()
    country_label = t(m, f"homes.countries.{chosen}")
    ctx = _ctx(
        request,
        country_rows=country_rows,
        rent_href=f"/app/home/list/?country={chosen}&kind=offer{city_query}",
        seek_href=f"/app/home/list/?country={chosen}&kind=wanted{city_query}",
        channel_url=f"https://t.me/{channel.username}" if channel else "",
        channel_title=t(m, "homes.landing.channel", country=country_label),
        home_bot_url=f"https://t.me/{conf.bot_username()}",
    )
    return render(request, "miniapp/homes_landing.html", ctx)


NEW_CHOICES = {
    "types": [choice.value for choice in PropertyType if choice.value != "other"],
    "rental_modes": [choice.value for choice in RentalMode if choice.value != "unknown"],
    "periods": ["month", "week", "night"],
}


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def home_new(request: HttpRequest) -> HttpResponse:
    locale = locale_from_request(request)
    m = messages_for(locale)
    available = countries()
    error = ""
    form = request.POST if request.method == "POST" else {
        "country": request.session.get(COUNTRY_SESSION_KEY) or available[0],
        "kind": request.GET.get("kind") or "offer",
    }
    if request.method == "POST":
        photos = [upload.read() for upload in request.FILES.getlist("photos")[:MAX_PHOTOS]]
        try:
            listing = create_listing(request.chamedoon_user, request.POST, photos, locale=locale, countries=available)
        except SubmitError as exc:
            error = t(m, f"homes.new.errors.{exc.code}")
        else:
            request.session[COUNTRY_SESSION_KEY] = listing.country
            return redirect(f"/app/home/{listing.pk}/?created=1")
    username = (request.chamedoon_user.telegram_username or "").strip()
    ctx = _ctx(
        request,
        back_href="/app/home/",
        form=form,
        error=error,
        countries=[{"code": code, "label": t(m, f"homes.countries.{code}"), "flag": country_flag(code)} for code in available],
        types=[{"value": value, "label": t(m, f"homes.types.{value}")} for value in NEW_CHOICES["types"]],
        rental_modes=[{"value": value, "label": t(m, f"homes.rentalModes.{value}")} for value in NEW_CHOICES["rental_modes"]],
        periods=[{"value": "month", "label": t(m, "homes.perMonth")}, {"value": "week", "label": t(m, "homes.perWeek")}, {"value": "night", "label": t(m, "homes.perNight")}],
        contact_line=t(m, "homes.new.contactTelegram", username=username) if username else t(m, "homes.new.contactNoUsername"),
        has_username=bool(username),
        max_photos=MAX_PHOTOS,
    )
    return render(request, "miniapp/homes_new.html", ctx)


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def home_mine(request: HttpRequest) -> HttpResponse:
    locale = locale_from_request(request)
    m = messages_for(locale)
    if request.method == "POST":
        try:
            close_listing(request.chamedoon_user, int(request.POST.get("listing") or 0))
        except ValueError:
            pass
        return redirect("/app/home/mine/")
    names = city_names()
    rows = []
    for listing in user_listings(request.chamedoon_user):
        row = card(listing, locale, m, names)
        if listing.status != ListingStatus.ACTIVE:
            row["state"] = t(m, "homes.new.closed")
        elif listing.needs_review:
            row["state"] = t(m, "homes.new.inReview")
        else:
            row["state"] = t(m, "homes.new.active")
        row["can_close"] = listing.status == ListingStatus.ACTIVE
        rows.append(row)
    return render(request, "miniapp/homes_mine.html", _ctx(request, back_href="/app/home/", rows=rows))
