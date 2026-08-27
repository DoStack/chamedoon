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

from item_requests.explore import LIST_LIMIT, open_requests_queryset
from item_requests.models import ItemRequest, RequestStatus, RequestType
from item_requests.services import cancel_item_request, create_item_request, expire_user_requests, update_item_request
from matching.acceptance import accept_match, reject_match
from matching.completion import complete_match, rate_match, rating_state
from matching.contact import telegram_dm_contact
from matching.manual import propose_user_match
from matching.models import USER_MATCH_STATUSES, VISIBLE_MATCH_STATUSES, Match, MatchStatus, matches_for_user
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
    format_date_range,
    format_kg,
    format_score_percent,
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
    return {
        "locale": locale,
        "dir": "rtl" if locale == "fa" else "ltr",
        "m": messages,
        "t": lambda path, **vars: t(messages, path, **vars),
        "user": user,
        "signed_in_as": t(messages, "common.signedInAs", name=user.first_name) if user else "",
        "debug": settings.DEBUG,
        "telegram_bot": bot,
        "telegram_app_url": f"https://t.me/{bot}/app" if bot else "",
        **extra,
    }


def _catalog():
    return locations_payload(), categories_payload()


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
    if match.status not in {MatchStatus.CONNECTED, MatchStatus.COMPLETED}:
        return None
    other_request = match.counterpart_request(user)
    if other_request is None:
        return None
    return telegram_dm_contact(other_request.user)


def _stars(score: int | None) -> str:
    if not score:
        return ""
    return ("★" * score) + ("☆" * (5 - score))


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
                "my_stars": _stars(state["my_rating"]),
                "their_stars": _stars(state["their_rating"]),
                "my_rating": state["my_rating"],
                "their_rating": state["their_rating"],
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
    defaults = {
        "origin_country": existing.origin_country if existing else "",
        "origin_city": existing.origin_city if existing else "",
        "destination_country": existing.destination_country if existing else "",
        "destination_city": existing.destination_city if existing else "",
        "date_from": (existing.date_from if existing else today).isoformat(),
        "date_to": (
            existing.date_to if existing else today + (timedelta(days=0) if request_type == "SUPPLY" else timedelta(days=14))
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
            "date_from": request.POST.get("date_from"),
            "date_to": request.POST.get("date_to"),
            "description": request.POST.get("description") or "",
            "item_category_codes": request.POST.getlist("item_category_codes"),
        }
        if request_type == RequestType.DEMAND:
            payload["weight_kg"] = request.POST.get("weight_kg")
        else:
            payload["capacity_kg"] = request.POST.get("capacity_kg")
            payload["excluded_category_codes"] = request.POST.getlist("excluded_category_codes")
            payload["excluded_other_text"] = request.POST.get("excluded_other_text") or ""
        defaults.update(
            {
                **payload,
                "weight_kg": request.POST.get("weight_kg") or "",
                "capacity_kg": request.POST.get("capacity_kg") or "",
                "item_category_codes": request.POST.getlist("item_category_codes"),
                "excluded_category_codes": request.POST.getlist("excluded_category_codes"),
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
        rows.append(
            {
                "item": item,
                "route": route_label(
                    locations,
                    item.origin_country,
                    item.origin_city,
                    item.destination_country,
                    item.destination_city,
                    locale_from_request(request),
                ),
                "kg": format_kg(item.weight_kg if item.type == RequestType.DEMAND else item.capacity_kg),
                "dates": format_date_range(item.date_from, item.date_to),
                "match_count": int(item._demand_match_count) + int(item._supply_match_count),
                "status_label": t(messages_for(locale_from_request(request)), f"status.{item.status}"),
            }
        )
    return render(request, "miniapp/requests.html", _ctx(request, rows=rows))


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def request_detail(request: HttpRequest, pk: int) -> HttpResponse:
    expire_user_requests(request.koolbar_user)
    item = (
        ItemRequest.objects.filter(user=request.koolbar_user, pk=pk)
        .prefetch_related("item_categories", "excluded_categories")
        .first()
    )
    if item is None:
        raise Http404()
    if request.method == "POST":
        if request.POST.get("action") == "cancel":
            try:
                cancel_item_request(item)
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
            dates=format_date_range(item.date_from, item.date_to),
            kg=format_kg(item.weight_kg if item.type == RequestType.DEMAND else item.capacity_kg),
            category_names=[
                category_label(categories, code, locale)
                for code in item.item_categories.values_list("code", flat=True)
            ],
            excluded_names=[
                category_label(categories, code, locale)
                for code in item.excluded_categories.values_list("code", flat=True)
            ],
            match_count=_match_count(item),
            status_label=t(messages_for(locale), f"status.{item.status}"),
            order_rows=_order_rows_for_request(item, request.koolbar_user),
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
                "dates": format_date_range(match.supply_request.date_from, match.supply_request.date_to),
                "demand_kg": format_kg(demand.weight_kg),
                "supply_kg": format_kg(match.supply_request.capacity_kg),
                "score": format_score_percent(match.score),
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
        .filter(status__in=USER_MATCH_STATUSES, pk=pk)
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
                match = reject_match(match, request.koolbar_user)
            elif action == "complete":
                match = complete_match(match, request.koolbar_user)
            elif action == "rate":
                rate_match(match, request.koolbar_user, request.POST.get("score"))
        except ValidationError as exc:
            error = _validation_message(exc)
        else:
            return redirect(f"/app/matches/{match.pk}/")

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
            dates=format_date_range(match.supply_request.date_from, match.supply_request.date_to),
            demand_kg=format_kg(demand.weight_kg),
            supply_kg=format_kg(match.supply_request.capacity_kg),
            score=format_score_percent(match.score),
            counterpart=_counterpart(match, request.koolbar_user),
            already_accepted=already_accepted,
            waiting_you=waiting_you,
            can_decide=can_decide,
            connected=match.status == MatchStatus.CONNECTED,
            finished=match.status == MatchStatus.COMPLETED,
            my_stars=_stars(state["my_rating"]),
            their_stars=_stars(state["their_rating"]),
            error=error,
            **state,
        ),
    )


@miniapp_login_required
@xframe_options_exempt
@require_http_methods(["GET", "POST"])
def explore(request: HttpRequest) -> HttpResponse:
    locale = locale_from_request(request)
    locations, categories = _catalog()
    error = ""
    if request.method == "POST":
        other_id = request.POST.get("request_id")
        my_id = request.POST.get("my_request_id")
        other = open_requests_queryset(request.koolbar_user).filter(pk=other_id).first()
        if other is None:
            error = t(messages_for(locale), "common.error")
        else:
            mine = None
            if my_id:
                mine = ItemRequest.objects.filter(pk=my_id, user=request.koolbar_user).first()
            try:
                match = propose_user_match(request.koolbar_user, other, mine)
                return redirect(f"/app/matches/{match.pk}/")
            except ValidationError as exc:
                error = _validation_message(exc)

    filters = {
        "type": request.GET.get("type") or "",
        "origin_country": request.GET.get("origin_country") or "",
        "origin_city": request.GET.get("origin_city") or "",
        "destination_country": request.GET.get("destination_country") or "",
        "destination_city": request.GET.get("destination_city") or "",
        "date_from": request.GET.get("date_from") or "",
        "date_to": request.GET.get("date_to") or "",
        "category": request.GET.get("category") or "",
    }
    items = list(open_requests_queryset(request.koolbar_user, request.GET)[:LIST_LIMIT])
    mine = list(
        ItemRequest.objects.filter(user=request.koolbar_user, status=RequestStatus.ACTIVE).prefetch_related(
            "item_categories"
        )
    )
    rows = []
    for item in items:
        opposite = RequestType.SUPPLY if item.type == RequestType.DEMAND else RequestType.DEMAND
        candidates = [row for row in mine if row.type == opposite]
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
                "kg": format_kg(item.weight_kg if item.type == RequestType.DEMAND else item.capacity_kg),
                "dates": format_date_range(item.date_from, item.date_to),
                "categories": [
                    category_label(categories, code, locale)
                    for code in item.item_categories.values_list("code", flat=True)
                ],
                "exclusions": [
                    category_label(categories, code, locale)
                    for code in item.excluded_categories.values_list("code", flat=True)
                ],
                "owner": t(messages_for(locale), "explore.owner", name=item.user.first_name),
                "candidates": [
                    {
                        "id": candidate.id,
                        "label": (
                            f"{route_label(locations, candidate.origin_country, candidate.origin_city, candidate.destination_country, candidate.destination_city, locale)}"
                            f" · {format_kg(candidate.weight_kg if candidate.type == RequestType.DEMAND else candidate.capacity_kg)}"
                        ),
                    }
                    for candidate in candidates
                ],
            }
        )
    return render(
        request,
        "miniapp/explore.html",
        _ctx(
            request,
            rows=rows,
            filters=filters,
            locations=locations,
            categories=categories,
            error=error,
            localized_name=localized_name,
            city_label=city_label,
        ),
    )
