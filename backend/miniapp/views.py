from __future__ import annotations

from datetime import date, timedelta

from django.conf import settings
from django.contrib import messages as django_messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from item_requests.explore import (
    LIST_LIMIT,
    explore_query,
    open_request_facets,
    open_requests_queryset,
    parse_explore_filters,
)
from item_requests.models import ItemRequest, RequestStatus, RequestType
from item_requests.services import (
    cancel_item_request,
    close_item_request,
    create_item_request,
    expire_user_requests,
    parse_package_sent,
    update_item_request,
)
from matching.acceptance import accept_match, reject_match
from matching.completion import complete_match, rate_match, rating_state
from matching.contact import contact_for_match
from matching.manual import propose_user_match
from matching.models import OPEN_MATCH_STATUSES, USER_MATCH_STATUSES, VISIBLE_MATCH_STATUSES, Match, MatchStatus, matches_for_user
from miniapp.auth import (
    get_miniapp_user,
    login_miniapp_user,
    logout_miniapp_user,
    miniapp_login_required,
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
    format_kg,
    format_month_day,
    locations_payload,
    localized_name,
    route_label,
)
from miniapp.i18n import LOCALE_COOKIE, locale_from_request, messages_for, t
from users.exceptions import TelegramAuthError
from users.services import upsert_telegram_user
from users.telegram import parse_and_validate_init_data, parse_dev_user


def _ctx(request: HttpRequest, **extra) -> dict:
    locale = locale_from_request(request)
    messages = messages_for(locale)
    user = extra.pop("user", None)
    if user is None:
        user = getattr(request, "koolbar_user", None) or get_miniapp_user(request)
    bot = (settings.TELEGRAM_BOT_USERNAME or "").lstrip("@")
    short_name = getattr(settings, "TELEGRAM_MINI_APP_SHORT_NAME", "app") or "app"
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
        **extra,
    }


def _catalog():
    return locations_payload(), categories_payload()


def _qs(filters: dict) -> str:
    query = explore_query(filters)
    return f"?{query}" if query else ""


def _explore_filter_chips(filters: dict, locations, categories, locale: str) -> list[str]:
    chips: list[str] = []
    if filters["origin_country"] or filters["origin_city"]:
        chips.append(_place_chip(locations, filters["origin_country"], filters["origin_city"], locale))
    if filters["destination_country"] or filters["destination_city"]:
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
        "route": route_label(
            locations,
            item.origin_country,
            item.origin_city,
            item.destination_country,
            item.destination_city,
            locale,
        ),
        "kg": _baggage_kg(item.weight_kg if is_demand else item.capacity_kg, locale),
        "flight": "" if is_demand else format_flight_line(item.flight_date),
        "desired": format_desired_line(item.desired_date or item.date_from) if is_demand else "",
        "carry": "" if is_demand else _carry_from_to(item.date_from, item.date_to, locale),
        "desired_date": format_day(item.desired_date or item.date_from) if is_demand else "",
        "flight_date": (format_flight_line(item.flight_date) or "—") if not is_demand else "",
        "carry_window": _carry_from_to(item.date_from, item.date_to, locale) if not is_demand else "",
        "categories": [
            format_category_line(categories, code, locale)
            for code in item.item_categories.values_list("code", flat=True)
        ],
        "exclusions": [
            format_category_line(categories, code, locale)
            for code in item.excluded_categories.values_list("code", flat=True)
        ],
        "category_emojis": [
            format_category_emoji(code) for code in item.item_categories.values_list("code", flat=True)
        ],
        "exclusion_emojis": [
            format_category_emoji(code) for code in item.excluded_categories.values_list("code", flat=True)
        ],
        "description": item.description,
        "owner": t(messages_for(locale), "explore.owner", name=item.user.first_name) if owner else "",
    }


def _match_action_state(match: Match, user) -> dict:
    role = match.role_for(user)
    already_accepted = (role == "demand" and match.status == MatchStatus.ACCEPTED_BY_DEMAND) or (
        role == "supply" and match.status == MatchStatus.ACCEPTED_BY_SUPPLY
    )
    waiting_you = (role == "demand" and match.status == MatchStatus.ACCEPTED_BY_SUPPLY) or (
        role == "supply" and match.status == MatchStatus.ACCEPTED_BY_DEMAND
    )
    return {
        "already_accepted": already_accepted,
        "waiting_you": waiting_you,
        "can_decide": match.status == MatchStatus.SUGGESTED or waiting_you,
        "connected": match.status == MatchStatus.CONNECTED,
        "finished": match.status == MatchStatus.COMPLETED,
    }


def _pdp_match_rows(item: ItemRequest, user, locations, categories, locale: str) -> list[dict]:
    matches = (
        Match.objects.filter(
            Q(demand_request=item) | Q(supply_request=item),
            status__in=VISIBLE_MATCH_STATUSES,
        )
        .select_related("demand_request__user", "supply_request__user")
        .prefetch_related(
            "demand_request__item_categories",
            "demand_request__excluded_categories",
            "supply_request__item_categories",
            "supply_request__excluded_categories",
        )
    )
    rows = []
    for match in matches:
        other = match.counterpart_request(user)
        if other is None:
            continue
        row = _listing_row(other, locations, categories, locale, owner=True)
        row.update(_match_action_state(match, user))
        row["match"] = match
        row["status_label"] = t(messages_for(locale), f"status.{match.status}")
        rows.append(row)
    return rows


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
    startapp = request.GET.get("startapp") or request.POST.get("startapp") or ""
    existing = get_miniapp_user(request)
    if existing and request.method == "GET" and not request.GET.get("switch"):
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


@require_POST
def set_locale(request: HttpRequest) -> HttpResponse:
    locale = request.POST.get("locale")
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/app/"
    response = redirect(next_url)
    if locale in {"en", "fa"}:
        response.set_cookie(LOCALE_COOKIE, locale, max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


@miniapp_login_required
@xframe_options_exempt
def home(request: HttpRequest) -> HttpResponse:
    startapp = request.GET.get("startapp")
    if startapp:
        return redirect(startapp_path(startapp))
    return render(request, "miniapp/home.html", _ctx(request))


def _request_form_page(request: HttpRequest, request_type: str, existing: ItemRequest | None = None):
    locale = locale_from_request(request)
    messages = messages_for(locale)
    locations, categories = _catalog()
    error = ""
    today = date.today()
    existing_desired = existing.desired_date or existing.date_from if existing else today
    existing_flight = existing.flight_date or existing.date_from if existing else today
    defaults = {
        "origin_country": existing.origin_country if existing else "",
        "origin_city": existing.origin_city if existing else "",
        "destination_country": existing.destination_country if existing else "",
        "destination_city": existing.destination_city if existing else "",
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
        payload = {
            "type": request_type,
            "origin_country": request.POST.get("origin_country"),
            "origin_city": request.POST.get("origin_city"),
            "destination_country": request.POST.get("destination_country"),
            "destination_city": request.POST.get("destination_city"),
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
            }
        )
        try:
            if existing:
                saved = update_item_request(existing, payload)
            else:
                saved = create_item_request(request.koolbar_user, payload)
            return redirect(f"/app/requests/{saved.pk}/")
        except ValidationError as exc:
            error = _validation_message(exc)

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
def requests_list(request: HttpRequest) -> HttpResponse:
    expire_user_requests(request.koolbar_user)
    locale = locale_from_request(request)
    messages = messages_for(locale)
    locations, _categories = _catalog()
    items = list(
        ItemRequest.objects.filter(user=request.koolbar_user)
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
                "route": route_label(
                    locations,
                    item.origin_country,
                    item.origin_city,
                    item.destination_country,
                    item.destination_city,
                    locale,
                ),
                "flight": "" if is_demand else format_flight_line(item.flight_date),
                "desired": format_desired_line(item.desired_date or item.date_from) if is_demand else "",
                "carry": "" if is_demand else _carry_from_to(item.date_from, item.date_to, locale),
                "kg": _baggage_kg(item.weight_kg if is_demand else item.capacity_kg, locale),
                "match_count": match_count,
                "match_label": t(messages, "requests.matches", count=str(match_count)),
                "status_label": t(messages, f"status.{item.status}"),
            }
        )
    return render(request, "miniapp/requests.html", _ctx(request, rows=rows))


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
        elif action in {"accept", "reject"}:
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
                .select_related("demand_request", "supply_request")
                .first()
            )
            if match is None or match.role_for(request.koolbar_user) is None:
                raise Http404()
            try:
                if action == "accept":
                    accept_match(match, request.koolbar_user)
                else:
                    reject_match(match, request.koolbar_user)
                return redirect(f"/app/requests/{item.pk}/")
            except ValidationError as exc:
                django_messages.error(request, _validation_message(exc))
        else:
            return _request_form_page(request, item.type, existing=item)
    if request.GET.get("edit") and item.status == RequestStatus.ACTIVE:
        return _request_form_page(request, item.type, existing=item)

    locale = locale_from_request(request)
    locations, categories = _catalog()
    return render(
        request,
        "miniapp/request_detail.html",
        _ctx(
            request,
            item=item,
            route=route_label(
                locations,
                item.origin_country,
                item.origin_city,
                item.destination_country,
                item.destination_city,
                locale,
            ),
            dates=format_item_dates(item),
            desired_date=format_day(item.desired_date or item.date_from) if item.type == RequestType.DEMAND else "",
            flight_date=(format_flight_line(item.flight_date) or "—") if item.type == RequestType.SUPPLY else "",
            carry_window=_carry_from_to(item.date_from, item.date_to, locale) if item.type == RequestType.SUPPLY else "",
            kg=_baggage_kg(item.weight_kg if item.type == RequestType.DEMAND else item.capacity_kg, locale),
            listing=_listing_row(item, locations, categories, locale),
            match_rows=_pdp_match_rows(item, request.koolbar_user, locations, categories, locale),
            match_count=_match_count(item),
            status_label=t(messages_for(locale), f"status.{item.status}"),
            order_rows=_order_rows_for_request(item, request.koolbar_user),
            closing=request.GET.get("close") == "1"
            and item.type == RequestType.SUPPLY
            and item.status == RequestStatus.ACTIVE,
        ),
    )


@miniapp_login_required
@xframe_options_exempt
def matches_list(request: HttpRequest) -> HttpResponse:
    locale = locale_from_request(request)
    locations, _categories = _catalog()
    matches = list(
        matches_for_user(request.koolbar_user)
        .filter(status__in=USER_MATCH_STATUSES)
        .select_related("demand_request", "supply_request")
        .prefetch_related("demand_request__item_categories", "supply_request__item_categories")
    )
    rows = []
    for match in matches:
        demand = match.demand_request
        supply = match.supply_request
        rows.append(
            {
                "match": match,
                "route": route_label(
                    locations,
                    demand.origin_country,
                    demand.origin_city,
                    demand.destination_country,
                    demand.destination_city,
                    locale,
                ),
                "flight": format_flight_line(supply.flight_date),
                "carry": _carry_from_to(supply.date_from, supply.date_to, locale),
                "demand_kg": _baggage_kg(demand.weight_kg, locale),
                "supply_kg": _baggage_kg(supply.capacity_kg, locale),
                "status_label": t(messages_for(locale), f"status.{match.status}"),
            }
        )
    return render(request, "miniapp/matches.html", _ctx(request, rows=rows))


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def match_detail(request: HttpRequest, pk: int) -> HttpResponse:
    match = (
        matches_for_user(request.koolbar_user)
        .filter(pk=pk)
        .select_related(
            "demand_request",
            "demand_request__user",
            "supply_request",
            "supply_request__user",
        )
        .prefetch_related("ratings")
        .first()
    )
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
                return redirect("/app/matches/")
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
    if match.status not in USER_MATCH_STATUSES:
        return redirect("/app/matches/")

    locale = locale_from_request(request)
    locations, _categories = _catalog()
    demand = match.demand_request
    role = match.role_for(request.koolbar_user)
    already_accepted = (role == "demand" and match.status == MatchStatus.ACCEPTED_BY_DEMAND) or (
        role == "supply" and match.status == MatchStatus.ACCEPTED_BY_SUPPLY
    )
    waiting_you = (role == "demand" and match.status == MatchStatus.ACCEPTED_BY_SUPPLY) or (
        role == "supply" and match.status == MatchStatus.ACCEPTED_BY_DEMAND
    )
    can_decide = match.status == MatchStatus.SUGGESTED or waiting_you
    state = rating_state(match, request.koolbar_user)
    return render(
        request,
        "miniapp/match_detail.html",
        _ctx(
            request,
            match=match,
            role=role,
            route=route_label(
                locations,
                demand.origin_country,
                demand.origin_city,
                demand.destination_country,
                demand.destination_city,
                locale,
            ),
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
            already_accepted=already_accepted,
            waiting_you=waiting_you,
            can_decide=can_decide,
            connected=match.status == MatchStatus.CONNECTED,
            finished=match.status == MatchStatus.COMPLETED,
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
    locale = locale_from_request(request)
    locations, categories = _catalog()
    filters = parse_explore_filters(request.GET)
    filter_chips = _explore_filter_chips(filters, locations, categories, locale)
    facets = open_request_facets(request.koolbar_user, filters["type"])
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
    type_links = {
        "all": f"/app/explore/{_qs({**filters, 'type': ''})}",
        "DEMAND": f"/app/explore/{_qs({**filters, 'type': RequestType.DEMAND})}",
        "SUPPLY": f"/app/explore/{_qs({**filters, 'type': RequestType.SUPPLY})}",
    }
    rows = [
        _listing_row(item, locations, categories, locale)
        for item in open_requests_queryset(request.koolbar_user, request.GET)[:LIST_LIMIT]
    ]
    return render(
        request,
        "miniapp/explore.html",
        _ctx(
            request,
            rows=rows,
            filters=filters,
            categories=filter_categories,
            localized_name=localized_name,
            city_label=city_label,
            filter_chips=filter_chips,
            filter_count=len(filter_chips),
            origin_options=origin_options,
            destination_options=destination_options,
            type_links=type_links,
            clear_filters_url=f"/app/explore/{_qs({'type': filters['type']})}",
        ),
    )


def _explore_candidates(user, other: ItemRequest, locations, locale: str) -> list[dict]:
    opposite = RequestType.SUPPLY if other.type == RequestType.DEMAND else RequestType.DEMAND
    mine = ItemRequest.objects.filter(user=user, type=opposite, status=RequestStatus.ACTIVE)
    return [
        {
            "id": candidate.id,
            "label": (
                f"{route_label(locations, candidate.origin_country, candidate.origin_city, candidate.destination_country, candidate.destination_city, locale)}"
                f" · {format_kg(candidate.weight_kg if candidate.type == RequestType.DEMAND else candidate.capacity_kg)}"
            ),
        }
        for candidate in mine
        if not candidate.is_expired()
    ]


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
    locations, categories = _catalog()
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
    existing = _visible_pair_match(request.koolbar_user, item)
    return render(
        request,
        "miniapp/explore_detail.html",
        _ctx(
            request,
            item=item,
            listing=listing,
            route=listing["route"],
            candidates=candidates,
            existing_match=existing,
            error=error,
        ),
    )
