from __future__ import annotations

import logging
from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages as django_messages
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from item_requests.explore import (
    LIST_LIMIT,
    apply_open_request_filters,
    explore_query,
    filters_for_item_matches,
    open_request_facets,
    open_requests_queryset,
    parse_explore_filters,
    public_open_requests_queryset,
)
from item_requests.models import (
    ACTIVE_REQUEST_STATUSES,
    ARCHIVE_REQUEST_STATUSES,
    ItemRequest,
    RequestStatus,
    RequestType,
)
from item_requests.services import (
    cancel_item_request,
    close_item_request,
    create_item_request,
    expire_user_requests,
    parse_package_sent,
    update_item_request,
)
from item_requests.weights import category_kg_map
from matching.acceptance import (
    accept_match,
    cancel_match,
    close_listing_after_reject,
    keep_listing_after_reject,
    reject_match,
)
from matching.completion import complete_match, rate_match, rating_state
from matching.contact import contact_for_match
from matching.manual import propose_user_match
from matching.models import (
    ACTIVE_MATCH_STATUSES,
    CREATED_MATCH_LIMIT,
    HISTORY_MATCH_STATUSES,
    OPEN_MATCH_STATUSES,
    TOP_SUGGESTED_MATCHES,
    USER_MATCH_STATUSES,
    VISIBLE_MATCH_STATUSES,
    Match,
    MatchStatus,
    matches_for_user,
)
from miniapp.auth import (
    consume_startapp,
    get_miniapp_user,
    login_miniapp_user,
    logout_miniapp_user,
    miniapp_login_required,
    startapp_already_consumed,
    startapp_from_request,
    startapp_path,
)
from miniapp.catalog import (
    categories_payload,
    category_label,
    city_label,
    format_baggage_kg,
    format_category_emoji,
    format_category_line,
    format_date_range,
    format_day,
    format_desired_line,
    format_flight_line,
    format_item_dates,
    format_month_day,
    locations_payload,
    localized_name,
    item_route_label,
)
from miniapp.i18n import (
    LOCALE_COOKIE,
    locale_from_request,
    messages_for,
    remember_locale,
    safe_next_path,
    t,
)
from support.models import SupportTicket, TicketStatus
from users.exceptions import TelegramAuthError
from users.services import upsert_telegram_user
from users.telegram import parse_and_validate_init_data, parse_dev_user

logger = logging.getLogger(__name__)


def _telegram_public_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.startswith(("https://t.me/", "http://t.me/", "https://telegram.me/")):
        return raw
    if raw.startswith("t.me/"):
        return f"https://{raw}"
    if raw.startswith("+"):
        return f"https://t.me/{raw}"
    return f"https://t.me/{raw.lstrip('@')}"


def _ctx(request: HttpRequest, **extra) -> dict:
    locale = locale_from_request(request)
    messages = messages_for(locale)
    user = extra.pop("user", None)
    if user is None:
        user = getattr(request, "koolbar_user", None) or get_miniapp_user(request)
    bot = (settings.TELEGRAM_BOT_USERNAME or "").lstrip("@")
    short_name = getattr(settings, "TELEGRAM_MINI_APP_SHORT_NAME", "app") or "app"
    channel_url = _telegram_public_url(
        getattr(settings, "TELEGRAM_CHANNEL_URL", "")
        or getattr(settings, "DEFAULT_TELEGRAM_CHANNEL_URL", "https://t.me/+26pUh8_5u0w1MTVk")
    )
    group_url = _telegram_public_url(getattr(settings, "TELEGRAM_GROUP_USERNAME", "") or "")
    return {
        "locale": locale,
        "dir": "rtl" if locale == "fa" else "ltr",
        "m": messages,
        "t": lambda path, **vars: t(messages, path, **vars),
        "user": user,
        "signed_in_as": t(messages, "common.signedInAs", name=user.first_name) if user else "",
        "debug": settings.DEBUG,
        "telegram_bot": bot,
        "telegram_app_short_name": short_name,
        "telegram_app_url": f"https://t.me/{bot}/{short_name}" if bot else "",
        "telegram_bot_url": f"https://t.me/{bot}" if bot else "",
        "telegram_channel_url": channel_url,
        "telegram_group_url": group_url,
        **extra,
    }


def _catalog(request: HttpRequest | None = None):
    locale = locale_from_request(request) if request else "en"
    return locations_payload(locale=locale), categories_payload()


def _qs(filters: dict) -> str:
    query = explore_query(filters)
    return f"?{query}" if query else ""


def _wants_list_fragment(request: HttpRequest) -> bool:
    if request.headers.get("X-Koolbar-List") == "1":
        return True
    return request.GET.get("list") == "1"


def _list_fragment(request: HttpRequest, template: str, ctx: dict) -> HttpResponse:
    response = render(request, template, ctx)
    response["Cache-Control"] = "private, no-store"
    response["X-Koolbar-List"] = "1"
    return response


def _explore_filter_chips(filters: dict, locations, categories, locale: str) -> list[str]:
    chips: list[str] = []
    if filters["origin_country"] or filters["origin_city"]:
        chips.append(_place_chip(locations, filters["origin_country"], filters["origin_city"], locale))
    dest_stops = filters.get("destination_stops") or []
    if dest_stops:
        for stop in dest_stops:
            if isinstance(stop, (list, tuple)) and len(stop) == 2:
                country, city = stop
            elif isinstance(stop, str) and ":" in stop:
                country, city = stop.split(":", 1)
            else:
                continue
            chips.append(_place_chip(locations, country, city, locale))
    elif filters["destination_country"] or filters["destination_city"]:
        chips.append(_place_chip(locations, filters["destination_country"], filters["destination_city"], locale))
    start = _parse_chip_date(filters["date_from"])
    end = _parse_chip_date(filters["date_to"])
    if start and end:
        chips.append(format_date_range(start, end))
    elif start or end:
        chips.append(format_day(start or end))
    elif filters["date_from"] or filters["date_to"]:
        chips.append(filters["date_from"] or filters["date_to"])
    for code in filters["categories"]:
        chips.append(category_label(categories, code, locale))
    return [chip for chip in chips if chip]


def _place_chip(locations, country_code: str, city_slug: str, locale: str) -> str:
    if city_slug:
        return city_label(locations, country_code, city_slug, locale)
    for country in locations:
        if country["code"] == country_code:
            return localized_name(country, locale)
    return country_code


def _place_flag(locations, country_code: str) -> str:
    for country in locations:
        if country["code"] == country_code:
            return country.get("flag") or ""
    return ""


def _place_facet_options(
    pairs,
    locations,
    locale: str,
    selected_country: str,
    selected_city: str,
) -> list[dict]:
    options = []
    seen: set[tuple[str, str]] = set()
    selected = (selected_country, selected_city)
    rows = list(pairs)
    if (selected_country or selected_city) and selected not in rows:
        rows.append(selected)
    for country_code, city_slug in rows:
        key = (country_code, city_slug)
        if key in seen:
            continue
        seen.add(key)
        options.append(
            {
                "country": country_code,
                "city": city_slug,
                "flag": _place_flag(locations, country_code),
                "label": _place_chip(locations, country_code, city_slug, locale),
                "on": country_code == selected_country and city_slug == selected_city,
            }
        )
    return options


def _parse_chip_date(value: str):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _validation_message(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        parts = []
        for value in exc.message_dict.values():
            if isinstance(value, list):
                parts.extend(str(item) for item in value)
            else:
                parts.append(str(value))
        return " ".join(parts)
    if getattr(exc, "messages", None):
        return " ".join(str(item) for item in exc.messages)
    return str(exc)


def _match_count(item: ItemRequest) -> int:
    return Match.objects.filter(
        Q(demand_request=item) | Q(supply_request=item),
        status__in=VISIBLE_MATCH_STATUSES,
    ).count()


def _counterpart(match: Match, user) -> dict | None:
    return contact_for_match(match, user)


def _baggage_kg(value, locale: str) -> str:
    unit = "KG" if locale != "fa" else t(messages_for(locale), "common.kg")
    return format_baggage_kg(value, unit)


def _card_capacity(item: ItemRequest) -> str:
    is_demand = item.type == RequestType.DEMAND
    return format_baggage_kg(item.weight_kg if is_demand else item.capacity_kg)


def _card_when(item: ItemRequest) -> str:
    if item.type == RequestType.DEMAND:
        return format_flight_line(item.desired_date or item.date_from)
    return format_flight_line(item.flight_date)


def _listing_row(
    item: ItemRequest,
    locations,
    categories,
    locale: str,
    *,
    owner: bool = False,
) -> dict:
    is_demand = item.type == RequestType.DEMAND
    return {
        "item": item,
        "route": item_route_label(locations, item, locale, compact=True),
        "kg": _baggage_kg(item.weight_kg if is_demand else item.capacity_kg, locale),
        "card_kg": _card_capacity(item),
        "when": _card_when(item),
        "flight": "" if is_demand else format_flight_line(item.flight_date),
        "desired": format_desired_line(item.desired_date or item.date_from) if is_demand else "",
        "carry": "" if is_demand else _carry_from_to(item.date_from, item.date_to, locale),
        "desired_date": format_day(item.desired_date or item.date_from) if is_demand else "",
        "flight_date": (format_flight_line(item.flight_date) or "—") if not is_demand else "",
        "carry_window": _carry_from_to(item.date_from, item.date_to, locale) if not is_demand else "",
        "categories": [
            format_category_line(categories, category.code, locale)
            for category in item.item_categories.all()
        ],
        "exclusions": [
            format_category_line(categories, category.code, locale)
            for category in item.excluded_categories.all()
        ],
        "category_emojis": [format_category_emoji(category.code) for category in item.item_categories.all()],
        "exclusion_emojis": [
            format_category_emoji(category.code) for category in item.excluded_categories.all()
        ],
        "description": item.description,
        "owner": t(messages_for(locale), "explore.owner", name=item.user.first_name) if owner else "",
    }


def _match_action_state(match: Match, user) -> dict:
    live = match.status in {MatchStatus.PENDING_APPROVAL, MatchStatus.ACCEPTED}
    return {
        "already_accepted": False,
        "waiting_you": False,
        "can_decide": False,
        "can_cancel": False,
        "connected": live,
        "finished": match.status == MatchStatus.COMPLETED,
        "ask_close": match.status == MatchStatus.REJECTED and match.is_owner(user),
    }


def _pdp_match_rows(item: ItemRequest, user, locations, categories, locale: str) -> list[dict]:
    matches = (
        Match.objects.filter(
            Q(demand_request=item) | Q(supply_request=item),
            status__in=VISIBLE_MATCH_STATUSES,
        )
        .select_related("initiated_by", "demand_request__user", "supply_request__user")
        .prefetch_related(
            "demand_request__item_categories",
            "demand_request__excluded_categories",
            "supply_request__item_categories",
            "supply_request__excluded_categories",
        )
        .order_by("-score", "-created_at")
    )
    rows = []
    for match in matches:
        try:
            other = match.counterpart_request(user)
            if other is None:
                continue
            row = _listing_row(other, locations, categories, locale, owner=True)
            row["id"] = match.id
            row["match"] = match
            row["score"] = match.score
            row["score_chip"] = match.score_label.lower()
            row["score_text"] = t(messages_for(locale), f"matches.{match.score_label.lower()}")
            rows.append(row)
        except Exception:
            logger.exception("Failed to render match %s on request %s", match.pk, item.pk)
    return rows


def _split_pdp_matches(rows: list[dict], limit: int = TOP_SUGGESTED_MATCHES) -> tuple[list[dict], list[dict]]:
    suggested = [
        row
        for row in rows
        if row["match"].status in {MatchStatus.PENDING_APPROVAL, MatchStatus.ACCEPTED}
    ]
    others = [
        row
        for row in rows
        if row["match"].status not in {MatchStatus.PENDING_APPROVAL, MatchStatus.ACCEPTED}
    ]
    return suggested[:limit], others


def _carry_from_to(start, end, locale: str) -> str:
    return t(
        messages_for(locale),
        "matches.carryFromTo",
        start=format_month_day(start, locale),
        end=format_month_day(end, locale),
    )


def _order_rows_for_request(item: ItemRequest, user) -> list[dict]:
    matches = (
        Match.objects.filter(Q(demand_request=item) | Q(supply_request=item), status=MatchStatus.COMPLETED)
        .select_related("demand_request__user", "supply_request__user")
        .prefetch_related("ratings")
    )
    rows = []
    for match in matches:
        other = match.counterpart_request(user)
        state = rating_state(match, user)
        rows.append(
            {
                "match": match,
                "name": other.user.first_name if other is not None else "",
                "can_rate": state["can_rate"],
            }
        )
    return rows


@require_GET
def landing(request: HttpRequest) -> HttpResponse:
    return render(request, "miniapp/landing.html", _ctx(request))


@ensure_csrf_cookie
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    startapp = startapp_from_request(request)
    existing = get_miniapp_user(request)
    if existing and request.method == "GET" and not request.GET.get("switch"):
        consume_startapp(request, startapp)
        return redirect(startapp_path(startapp))

    error = ""
    if request.method == "POST":
        init_data = (request.POST.get("init_data") or "").strip()
        try:
            if init_data:
                identity = parse_and_validate_init_data(init_data)
            elif settings.DEBUG:
                identity = parse_dev_user(
                    {
                        "telegram_user_id": request.POST.get("telegram_user_id"),
                        "first_name": request.POST.get("first_name"),
                        "telegram_username": request.POST.get("telegram_username"),
                    }
                )
            else:
                raise TelegramAuthError("init_data is required", status_code=400)
            user = upsert_telegram_user(identity)
            if not user.is_active:
                raise TelegramAuthError("User is deactivated", status_code=403)
            login_miniapp_user(request, user)
            consume_startapp(request, startapp)
            return redirect(startapp_path(startapp))
        except TelegramAuthError as exc:
            error = exc.message

    ctx = _ctx(
        request,
        error=error,
        startapp=startapp,
        signing_in=True,
    )
    return render(request, "miniapp/login.html", ctx)


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    logout_miniapp_user(request)
    return redirect("/app/login/?switch=1")


@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def set_locale(request: HttpRequest) -> HttpResponse:
    locale = (request.POST.get("locale") or request.GET.get("locale") or "").strip()
    next_url = safe_next_path(
        request.POST.get("next") or request.GET.get("next") or request.META.get("HTTP_REFERER")
    )
    if locale in {"en", "fa"}:
        remember_locale(request, locale)
    response = redirect(next_url)
    if locale in {"en", "fa"}:
        samesite = str(getattr(settings, "SESSION_COOKIE_SAMESITE", "Lax") or "Lax").lower()
        secure = bool(getattr(settings, "SESSION_COOKIE_SECURE", False)) or samesite == "none"
        response.set_cookie(
            LOCALE_COOKIE,
            locale,
            max_age=60 * 60 * 24 * 365,
            samesite=samesite,
            secure=secure,
        )
    return response


@xframe_options_exempt
def home(request: HttpRequest) -> HttpResponse:
    startapp = startapp_from_request(request)
    user = get_miniapp_user(request)
    if user is None:
        return login_view(request)
    request.koolbar_user = user
    if startapp and not startapp_already_consumed(request, startapp):
        consume_startapp(request, startapp)
        return redirect(startapp_path(startapp))
    active_match_count = matches_for_user(user).filter(status__in=ACTIVE_MATCH_STATUSES).count()
    open_support_count = SupportTicket.objects.filter(
        user=user,
        status__in=(TicketStatus.OPEN, TicketStatus.IN_QUEUE),
    ).count()
    return render(
        request,
        "miniapp/home.html",
        _ctx(
            request,
            active_match_count=active_match_count,
            open_support_count=open_support_count,
        ),
    )


@xframe_options_exempt
@require_GET
def how_it_works(request: HttpRequest) -> HttpResponse:
    back_href = "/app/" if request.path.startswith("/app/") else "/"
    return render(request, "miniapp/about.html", _ctx(request, back_href=back_href))


def _request_form_page(request: HttpRequest, request_type: str, existing: ItemRequest | None = None):
    locale = locale_from_request(request)
    messages = messages_for(locale)
    locations, categories = _catalog(request)
    error = ""
    today = date.today()
    existing_desired = existing.desired_date or existing.date_from if existing else today
    existing_flight = existing.flight_date or existing.date_from if existing else today
    defaults = {
        "origin_country": existing.origin_country if existing else "",
        "origin_city": existing.origin_city if existing else "",
        "destination_country": existing.destination_country if existing else "",
        "destination_city": existing.destination_city if existing else "",
        "destination_cities": (
            list(existing.destination_cities)
            if existing and existing.destination_cities
            else (
                [{"country": existing.destination_country, "city": existing.destination_city}]
                if existing
                else []
            )
        ),
        "desired_date": existing_desired.isoformat(),
        "flight_date": existing_flight.isoformat(),
        "date_from": (existing.date_from if existing else today).isoformat(),
        "date_to": (
            existing.date_to if existing else today + timedelta(days=14)
        ).isoformat(),
        "weight_kg": str(existing.weight_kg) if existing and existing.weight_kg is not None else "",
        "capacity_kg": str(existing.capacity_kg) if existing and existing.capacity_kg is not None else "",
        "item_category_codes": list(existing.item_categories.values_list("code", flat=True)) if existing else [],
        "excluded_category_codes": list(existing.excluded_categories.values_list("code", flat=True)) if existing else [],
        "excluded_other_text": existing.excluded_other_text if existing else "",
        "description": existing.description if existing else "",
    }
    if request.method == "POST":
        dest_country = (request.POST.get("destination_country") or "").upper().strip()
        dest_slugs = [slug.strip() for slug in request.POST.getlist("destination_cities") if slug.strip()]
        if not dest_slugs:
            single = (request.POST.get("destination_city") or "").strip()
            if single:
                dest_slugs = [single]
        if request_type == RequestType.DEMAND:
            single = (request.POST.get("destination_city") or "").strip() or (dest_slugs[0] if dest_slugs else "")
            dest_slugs = [single] if single else []
        payload = {
            "type": request_type,
            "origin_country": request.POST.get("origin_country"),
            "origin_city": request.POST.get("origin_city"),
            "destination_country": dest_country,
            "destination_city": dest_slugs[-1] if dest_slugs else request.POST.get("destination_city"),
            "destination_cities": [{"country": dest_country, "city": slug} for slug in dest_slugs],
            "description": request.POST.get("description") or "",
            "item_category_codes": request.POST.getlist("item_category_codes"),
        }
        if request_type == RequestType.DEMAND:
            payload["desired_date"] = request.POST.get("desired_date")
            payload["weight_kg"] = request.POST.get("weight_kg")
        else:
            payload["flight_date"] = request.POST.get("flight_date")
            payload["date_from"] = request.POST.get("date_from")
            payload["date_to"] = request.POST.get("date_to")
            payload["capacity_kg"] = request.POST.get("capacity_kg")
            selected = payload["item_category_codes"]
            payload["excluded_category_codes"] = [
                row["code"] for row in categories if row["code"] not in selected
            ]
            payload["excluded_other_text"] = request.POST.get("excluded_other_text") or ""
        defaults.update(
            {
                **payload,
                "weight_kg": request.POST.get("weight_kg") or "",
                "capacity_kg": request.POST.get("capacity_kg") or "",
                "item_category_codes": request.POST.getlist("item_category_codes"),
                "excluded_category_codes": payload.get("excluded_category_codes") or [],
                "excluded_other_text": request.POST.get("excluded_other_text") or "",
                "destination_cities": payload.get("destination_cities") or [],
            }
        )
        try:
            if existing:
                saved = update_item_request(existing, payload)
                return redirect(f"/app/requests/{saved.pk}/")
            saved = create_item_request(request.koolbar_user, payload)
            return redirect(f"/app/requests/{saved.pk}/created/")
        except ValidationError as exc:
            error = _validation_message(exc)
        except Exception:
            logger.exception("Failed to save %s request", request_type)
            error = t(messages, "common.error")

    return render(
        request,
        "miniapp/request_form.html",
        _ctx(
            request,
            request_type=request_type,
            existing=existing,
            form=defaults,
            error=error,
            locations=locations,
            categories=categories,
            category_kg=category_kg_map(),
            origin_flag=_place_flag(locations, defaults["origin_country"]),
            dest_flag=_place_flag(locations, defaults["destination_country"]),
            resume_stage="review" if error else "",
            title=t(messages, "demand.title" if request_type == "DEMAND" else "supply.title"),
        ),
    )


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def demand_new(request: HttpRequest) -> HttpResponse:
    return _request_form_page(request, RequestType.DEMAND)


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def supply_new(request: HttpRequest) -> HttpResponse:
    return _request_form_page(request, RequestType.SUPPLY)


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def request_created(request: HttpRequest, pk: int) -> HttpResponse:
    expire_user_requests(request.koolbar_user)
    item = (
        ItemRequest.objects.filter(pk=pk, user=request.koolbar_user)
        .prefetch_related("item_categories", "excluded_categories")
        .first()
    )
    if item is None:
        raise Http404()
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            match_id = int(request.POST.get("match_id") or "")
        except (TypeError, ValueError):
            raise Http404()
        match = (
            Match.objects.filter(
                Q(demand_request=item) | Q(supply_request=item),
                pk=match_id,
                status__in=OPEN_MATCH_STATUSES,
            )
            .select_related(
                "initiated_by",
                "demand_request",
                "demand_request__user",
                "supply_request",
                "supply_request__user",
            )
            .first()
        )
        if match is None or match.role_for(request.koolbar_user) is None:
            raise Http404()
        try:
            if action == "accept":
                match = accept_match(match, request.koolbar_user)
                if match.status == MatchStatus.ACCEPTED:
                    return redirect(f"/app/matches/{match.pk}/")
            elif action == "reject":
                reject_match(match, request.koolbar_user)
                return redirect(f"/app/matches/{match.pk}/?close=1")
            elif action == "cancel":
                cancel_match(match, request.koolbar_user)
        except ValidationError as exc:
            django_messages.error(request, _validation_message(exc))
        return redirect(f"/app/requests/{item.pk}/created/")

    locale = locale_from_request(request)
    locations, categories = _catalog(request)
    try:
        has_matches = Match.objects.filter(Q(demand_request=item) | Q(supply_request=item)).exists()
    except DatabaseError:
        logger.exception("Failed to check matches for created request %s", item.pk)
        has_matches = True
    if not has_matches:
        try:
            from matching.services import sync_matches_for_request

            sync_matches_for_request(item)
        except Exception:
            logger.exception("Matching failed while opening created page for request %s", item.pk)
    match_filters = filters_for_item_matches(item)
    counterparts = apply_open_request_filters(
        public_open_requests_queryset().exclude(user=request.koolbar_user),
        match_filters,
    ).distinct()
    match_count = counterparts.count()
    preview_rows = []
    for other in counterparts.order_by("-created_at")[:CREATED_MATCH_LIMIT]:
        row = _listing_row(other, locations, categories, locale, owner=True)
        row["href"] = f"/app/explore/{other.id}/"
        preview_rows.append(row)
    try:
        suggested_rows, _other_match_rows = _split_pdp_matches(
            _pdp_match_rows(item, request.koolbar_user, locations, categories, locale)
        )
        for row in suggested_rows:
            row["href"] = f"/app/matches/{row['match'].pk}/"
    except DatabaseError:
        logger.exception("Failed to load created matches for request %s", item.pk)
        suggested_rows = []
    has_preview = match_count > 0 or bool(suggested_rows)
    filtered_explore_url = f"/app/explore/{_qs(match_filters)}" if has_preview else "/app/explore/"
    messages = messages_for(locale)
    return render(
        request,
        "miniapp/request_created.html",
        _ctx(
            request,
            item=item,
            route=item_route_label(locations, item, locale),
            listing=_listing_row(item, locations, categories, locale),
            preview_rows=preview_rows,
            suggested_rows=suggested_rows,
            match_count=match_count,
            created_match_lead=t(messages, "requests.createdMatchLead", count=str(match_count)),
            filtered_explore_url=filtered_explore_url,
            status_label=t(messages, f"status.{item.status}"),
            back_href="/app/requests/",
        ),
    )


@miniapp_login_required
@xframe_options_exempt
def requests_list(request: HttpRequest) -> HttpResponse:
    expire_user_requests(request.koolbar_user)
    archive = request.GET.get("archive") == "1"
    if not _wants_list_fragment(request):
        return render(request, "miniapp/requests.html", _ctx(request, archive=archive))
    locale = locale_from_request(request)
    messages = messages_for(locale)
    locations, _categories = _catalog(request)
    statuses = ARCHIVE_REQUEST_STATUSES if archive else ACTIVE_REQUEST_STATUSES
    items = list(
        ItemRequest.objects.filter(user=request.koolbar_user, status__in=statuses)
        .prefetch_related("item_categories")
        .annotate(
            _demand_match_count=Count(
                "demand_matches",
                filter=Q(demand_matches__status__in=VISIBLE_MATCH_STATUSES),
                distinct=True,
            ),
            _supply_match_count=Count(
                "supply_matches",
                filter=Q(supply_matches__status__in=VISIBLE_MATCH_STATUSES),
                distinct=True,
            ),
        )
    )
    rows = []
    for item in items:
        is_demand = item.type == RequestType.DEMAND
        match_count = int(item._demand_match_count) + int(item._supply_match_count)
        rows.append(
            {
                "item": item,
                "route": item_route_label(locations, item, locale, compact=True),
                "when": _card_when(item),
                "flight": "" if is_demand else format_flight_line(item.flight_date),
                "desired": format_desired_line(item.desired_date or item.date_from) if is_demand else "",
                "carry": "" if is_demand else _carry_from_to(item.date_from, item.date_to, locale),
                "kg": _baggage_kg(item.weight_kg if is_demand else item.capacity_kg, locale),
                "card_kg": _card_capacity(item),
                "match_count": match_count,
                "match_label": t(messages, "requests.matches", count=str(match_count)),
                "status_label": t(messages, f"status.{item.status}"),
            }
        )
    return _list_fragment(
        request,
        "miniapp/includes/requests_list.html",
        _ctx(request, rows=rows, archive=archive),
    )


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    expire_user_requests(request.koolbar_user)
    item = (
        ItemRequest.objects.filter(pk=pk)
        .prefetch_related("item_categories", "excluded_categories")
        .first()
    )
    if item is None:
        raise Http404()
    if item.user_id != request.koolbar_user.id:
        if item.status == RequestStatus.ACTIVE and not item.is_expired():
            return redirect(f"/app/explore/{item.pk}/")
        raise Http404()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "cancel":
            try:
                cancel_item_request(item)
                return redirect(f"/app/requests/{item.pk}/")
            except ValidationError as exc:
                django_messages.error(request, _validation_message(exc))
        elif action == "close":
            try:
                close_item_request(item, package_sent=parse_package_sent(request.POST.get("package_sent")))
                return redirect(f"/app/requests/{item.pk}/")
            except ValidationError as exc:
                django_messages.error(request, _validation_message(exc))
        elif action == "open_match":
            try:
                match_id = int(request.POST.get("match_id") or "")
            except (TypeError, ValueError):
                raise Http404()
            match = (
                Match.objects.filter(
                    Q(demand_request=item) | Q(supply_request=item),
                    pk=match_id,
                    status__in=VISIBLE_MATCH_STATUSES,
                )
                .first()
            )
            if match is None or match.role_for(request.koolbar_user) is None:
                raise Http404()
            return redirect(f"/app/matches/{match.pk}/")
        elif action in {"accept", "reject", "cancel"}:
            try:
                match_id = int(request.POST.get("match_id") or "")
            except (TypeError, ValueError):
                raise Http404()
            match = (
                Match.objects.filter(
                    Q(demand_request=item) | Q(supply_request=item),
                    pk=match_id,
                    status__in=OPEN_MATCH_STATUSES,
                )
                .select_related(
                    "initiated_by",
                    "demand_request",
                    "demand_request__user",
                    "supply_request",
                    "supply_request__user",
                )
                .first()
            )
            if match is None or match.role_for(request.koolbar_user) is None:
                raise Http404()
            try:
                if action == "accept":
                    accept_match(match, request.koolbar_user)
                    return redirect(f"/app/matches/{match.pk}/")
                if action == "reject":
                    reject_match(match, request.koolbar_user)
                    return redirect(f"/app/matches/{match.pk}/?close=1")
                cancel_match(match, request.koolbar_user)
                return redirect(f"/app/requests/{item.pk}/")
            except ValidationError as exc:
                django_messages.error(request, _validation_message(exc))
        else:
            return _request_form_page(request, item.type, existing=item)
    if request.GET.get("edit") and item.status == RequestStatus.ACTIVE:
        return _request_form_page(request, item.type, existing=item)
    if request.GET.get("picks") == "1":
        return redirect(f"/app/requests/{item.pk}/created/")

    locale = locale_from_request(request)
    locations, categories = _catalog(request)
    try:
        match_rows = _pdp_match_rows(item, request.koolbar_user, locations, categories, locale)
        match_count = _match_count(item)
        order_rows = _order_rows_for_request(item, request.koolbar_user)
    except DatabaseError:
        logger.exception("Failed to load matches for request %s", item.pk)
        match_rows, order_rows = [], []
        match_count = 0
    return render(
        request,
        "miniapp/request_detail.html",
        _ctx(
            request,
            item=item,
            route=item_route_label(locations, item, locale),
            dates=format_item_dates(item),
            desired_date=format_day(item.desired_date or item.date_from) if item.type == RequestType.DEMAND else "",
            flight_date=(format_flight_line(item.flight_date) or "—") if item.type == RequestType.SUPPLY else "",
            carry_window=_carry_from_to(item.date_from, item.date_to, locale) if item.type == RequestType.SUPPLY else "",
            kg=_baggage_kg(item.weight_kg if item.type == RequestType.DEMAND else item.capacity_kg, locale),
            listing=_listing_row(item, locations, categories, locale),
            match_rows=match_rows,
            match_count=match_count,
            status_label=t(messages_for(locale), f"status.{item.status}"),
            order_rows=order_rows,
            closing=request.GET.get("close") == "1" and item.status == RequestStatus.ACTIVE,
        ),
    )


@miniapp_login_required
@xframe_options_exempt
def matches_list(request: HttpRequest) -> HttpResponse:
    archive = request.GET.get("archive") == "1" or request.GET.get("history") == "1"
    if not _wants_list_fragment(request):
        return render(request, "miniapp/matches.html", _ctx(request, archive=archive))
    locale = locale_from_request(request)
    locations, _categories = _catalog(request)
    statuses = HISTORY_MATCH_STATUSES if archive else ACTIVE_MATCH_STATUSES
    matches = list(
        matches_for_user(request.koolbar_user)
        .filter(status__in=statuses)
        .select_related("initiated_by", "demand_request", "supply_request")
        .prefetch_related("demand_request__item_categories", "supply_request__item_categories")
    )
    rows = []
    for match in matches:
        demand = match.demand_request
        supply = match.supply_request
        meta_parts = [
            format_flight_line(supply.flight_date),
            format_baggage_kg(demand.weight_kg, emoji="📦"),
            format_baggage_kg(supply.capacity_kg, emoji="🧳"),
        ]
        rows.append(
            {
                "match": match,
                "route": item_route_label(locations, demand, locale, compact=True),
                "meta": " | ".join(part for part in meta_parts if part and part != "—"),
                "status_label": t(messages_for(locale), f"status.{match.status}"),
            }
        )
    return _list_fragment(
        request,
        "miniapp/includes/matches_list.html",
        _ctx(request, rows=rows, archive=archive),
    )


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def match_detail(request: HttpRequest, pk: int) -> HttpResponse:
    try:
        match = (
            matches_for_user(request.koolbar_user)
            .filter(pk=pk)
            .select_related(
                "initiated_by",
                "demand_request",
                "demand_request__user",
                "supply_request",
                "supply_request__user",
            )
            .prefetch_related("ratings")
            .first()
        )
    except DatabaseError:
        logger.exception("Failed to load match %s", pk)
        return redirect("/app/matches/")
    if match is None:
        raise Http404()
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "accept":
                match = accept_match(match, request.koolbar_user)
            elif action == "reject":
                reject_match(match, request.koolbar_user)
                return redirect(f"/app/matches/{match.pk}/?close=1")
            elif action == "cancel":
                cancel_match(match, request.koolbar_user)
                return redirect("/app/matches/")
            elif action == "close_listing":
                close_listing_after_reject(match, request.koolbar_user)
                return redirect("/app/matches/?archive=1")
            elif action == "keep_listing":
                keep_listing_after_reject(match, request.koolbar_user)
                return redirect("/app/matches/?archive=1")
            elif action == "complete":
                match = complete_match(match, request.koolbar_user)
            elif action == "rate":
                rate_match(
                    match,
                    request.koolbar_user,
                    request.POST.get("score"),
                    request.POST.get("comment") or "",
                )
        except ValidationError as exc:
            error = _validation_message(exc)
        else:
            return redirect(f"/app/matches/{match.pk}/")
    viewable = USER_MATCH_STATUSES + HISTORY_MATCH_STATUSES
    if match.status not in viewable:
        return redirect("/app/matches/")

    locale = locale_from_request(request)
    locations, _categories = _catalog(request)
    demand = match.demand_request
    role = match.role_for(request.koolbar_user)
    actions = _match_action_state(match, request.koolbar_user)
    ask_close = (
        request.GET.get("close") == "1"
        and match.status == MatchStatus.REJECTED
        and match.is_owner(request.koolbar_user)
    )
    state = rating_state(match, request.koolbar_user)
    return render(
        request,
        "miniapp/match_detail.html",
        _ctx(
            request,
            match=match,
            role=role,
            route=item_route_label(locations, demand, locale),
            dates=format_item_dates(match.supply_request),
            desired_date=format_day(demand.desired_date or demand.date_from),
            flight_date=format_flight_line(match.supply_request.flight_date) or "—",
            carry_window=_carry_from_to(
                match.supply_request.date_from,
                match.supply_request.date_to,
                locale,
            ),
            demand_kg=_baggage_kg(demand.weight_kg, locale),
            supply_kg=_baggage_kg(match.supply_request.capacity_kg, locale),
            counterpart=_counterpart(match, request.koolbar_user),
            already_accepted=actions["already_accepted"],
            waiting_you=actions["waiting_you"],
            can_decide=actions["can_decide"],
            can_cancel=actions["can_cancel"],
            connected=actions["connected"],
            finished=actions["finished"],
            ask_close=ask_close,
            can_complete=state["can_complete"],
            can_rate=state["can_rate"],
            status_label=t(messages_for(locale), f"status.{match.status}"),
            error=error,
        ),
    )


@miniapp_login_required
@xframe_options_exempt
@require_GET
def explore(request: HttpRequest) -> HttpResponse:
    return _explore_listing(request, path="/app/explore", user=request.koolbar_user, back_href="/app/")


@require_GET
def public_browse(request: HttpRequest) -> HttpResponse:
    return _explore_listing(request, path="/browse", user=None, back_href="/")


def _explore_listing(request: HttpRequest, *, path: str, user=None, back_href: str) -> HttpResponse:
    locale = locale_from_request(request)
    locations, categories = _catalog(request)
    filters = parse_explore_filters(request.GET)
    filter_chips = _explore_filter_chips(filters, locations, categories, locale)
    facets = open_request_facets(user, filters["type"])
    origin_options = _place_facet_options(
        facets["origins"],
        locations,
        locale,
        filters["origin_country"],
        filters["origin_city"],
    )
    destination_options = _place_facet_options(
        facets["destinations"],
        locations,
        locale,
        filters["destination_country"],
        filters["destination_city"],
    )
    filter_category_codes = set(facets["category_codes"]) | set(filters["categories"])
    filter_categories = [category for category in categories if category["code"] in filter_category_codes]
    if user is None:
        listing = apply_open_request_filters(public_open_requests_queryset(), request.GET).distinct()
    else:
        listing = open_requests_queryset(user, request.GET, include_own=True)
    list_count = listing.count()
    ctx = _ctx(
        request,
        back_href=back_href,
        filter_form_action=f"{path}/",
        filters=filters,
        categories=filter_categories,
        localized_name=localized_name,
        city_label=city_label,
        filter_chips=filter_chips,
        filter_count=len(filter_chips),
        origin_options=origin_options,
        destination_options=destination_options,
        type_links={
            "all": f"{path}/{_qs({**filters, 'type': ''})}",
            "DEMAND": f"{path}/{_qs({**filters, 'type': RequestType.DEMAND})}",
            "SUPPLY": f"{path}/{_qs({**filters, 'type': RequestType.SUPPLY})}",
        },
        clear_filters_url=f"{path}/{_qs({'type': filters['type']})}",
        live_filters=user is None,
        list_count=list_count,
        explore_title=t(messages_for(locale), "explore.title", count=list_count),
    )
    if _wants_list_fragment(request):
        items = listing[:LIST_LIMIT]
        rows = []
        for item in items:
            row = _listing_row(item, locations, categories, locale)
            mine = user is not None and item.user_id == user.id
            row["mine"] = mine
            row["href"] = f"/app/requests/{item.id}/" if mine else f"{path}/{item.id}/"
            rows.append(row)
        ctx["rows"] = rows
        return _list_fragment(request, "miniapp/includes/explore_list.html", ctx)
    template = "miniapp/browse.html" if user is None else "miniapp/explore.html"
    return render(request, template, ctx)


def _explore_candidates(user, other: ItemRequest, locations, locale: str) -> list[dict]:
    opposite = RequestType.SUPPLY if other.type == RequestType.DEMAND else RequestType.DEMAND
    mine = ItemRequest.objects.filter(user=user, type=opposite, status=RequestStatus.ACTIVE)
    rows = []
    for candidate in mine:
        if candidate.is_expired():
            continue
        is_demand = candidate.type == RequestType.DEMAND
        rows.append(
            {
                "id": candidate.id,
                "route": item_route_label(locations, candidate, locale, compact=True),
                "kg": _baggage_kg(candidate.weight_kg if is_demand else candidate.capacity_kg, locale),
                "type": candidate.type,
            }
        )
    return rows


def _visible_pair_match(user, other: ItemRequest) -> Match | None:
    return (
        Match.objects.filter(
            Q(demand_request=other) | Q(supply_request=other),
            Q(demand_request__user=user) | Q(supply_request__user=user),
            status__in=VISIBLE_MATCH_STATUSES,
        )
        .select_related("demand_request", "supply_request")
        .first()
    )


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def explore_detail(request: HttpRequest, pk: int) -> HttpResponse:
    locale = locale_from_request(request)
    locations, categories = _catalog(request)
    item = open_requests_queryset(request.koolbar_user).filter(pk=pk).first()
    if item is None:
        own = ItemRequest.objects.filter(user=request.koolbar_user, pk=pk).first()
        if own is not None:
            return redirect(f"/app/requests/{own.pk}/")
        raise Http404()

    error = ""
    if request.method == "POST":
        my_id = request.POST.get("my_request_id")
        mine = ItemRequest.objects.filter(pk=my_id, user=request.koolbar_user).first() if my_id else None
        try:
            match = propose_user_match(request.koolbar_user, item, mine)
            return redirect(f"/app/matches/{match.pk}/")
        except ValidationError as exc:
            error = _validation_message(exc)

    listing = _listing_row(item, locations, categories, locale, owner=True)
    candidates = _explore_candidates(request.koolbar_user, item, locations, locale)
    try:
        existing = _visible_pair_match(request.koolbar_user, item)
    except DatabaseError:
        logger.exception("Failed to load existing match for explore %s", item.pk)
        existing = None
    return render(
        request,
        "miniapp/explore_detail.html",
        _ctx(
            request,
            item=item,
            listing=listing,
            route=item_route_label(locations, item, locale),
            candidates=candidates,
            existing_match=existing,
            error=error,
        ),
    )


@require_GET
def public_browse_detail(request: HttpRequest, pk: int) -> HttpResponse:
    locale = locale_from_request(request)
    locations, categories = _catalog(request)
    item = public_open_requests_queryset().filter(pk=pk).first()
    if item is None:
        raise Http404()
    listing = _listing_row(item, locations, categories, locale, owner=True)
    ctx = _ctx(
        request,
        back_href="/browse/",
        item=item,
        listing=listing,
        route=item_route_label(locations, item, locale),
    )
    startapp = f"explore_{item.id}"
    bot_url = ctx["telegram_bot_url"]
    ctx["match_url"] = f"{bot_url}?startapp={startapp}" if bot_url else f"/app/login/?startapp={startapp}"
    return render(request, "miniapp/browse_detail.html", ctx)
