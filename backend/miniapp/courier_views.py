"""The courier (باربری) Mini App page: route search, two kinds of cards, «🔔 خبرم کن», alert list.

Its own page and tabs (مسیرها · گوش به زنگ · ثبت · من), opened from the courier bot's menu
button (startapp=courier). Separate from the housing sections under /app/home/.
"""

from __future__ import annotations

from decimal import Decimal
from urllib.parse import urlencode

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_GET, require_POST

from courier import alerts, conf, search, texts
from courier.models import CourierRouteAlert
from item_requests.locations import locations_payload
from item_requests.models import RequestType
from miniapp.auth import miniapp_login_required
from miniapp.views import _ctx

ROLES = {"D": RequestType.DEMAND, "S": RequestType.SUPPLY}


def _place(value: str) -> tuple[str, str] | None:
    country, _sep, city = (value or "").partition(":")
    country, city = country.strip().upper()[:2], city.strip()[:64]
    return (country, city) if country and city else None


def parse_filters(params) -> dict:
    role_code = params.get("role") if params.get("role") in ROLES else "D"
    months = {key for key, _start, _end in texts.upcoming_months()}
    return {
        "role": role_code,
        "from": params.get("from", "").strip() if _place(params.get("from", "")) else "",
        "to": params.get("to", "").strip() if _place(params.get("to", "")) else "",
        "month": params.get("month", "") if params.get("month", "") in months else "",
        "size": params.get("size", "") if params.get("size", "") in texts.SIZES else "",
    }


def _href(filters: dict, **changes) -> str:
    merged = {**filters, **changes}
    query = urlencode({key: value for key, value in merged.items() if value})
    return f"/app/courier/?{query}" if query else "/app/courier/"


def _place_options() -> list[dict]:
    groups = []
    for country in locations_payload(locale="fa"):
        options = [{"value": f"{country['code']}:{city['slug']}", "label": city["name_fa"]} for city in country["cities"]]
        if options:
            groups.append({"label": f"{country['flag']} {country['name_fa']}", "options": options})
    return groups


def card(item) -> dict:
    day = search.request_date(item)
    supply = item.type == RequestType.SUPPLY
    if supply:
        title = f"✈️ {texts.day_month(day)}" if day else "✈️ تاریخ پرواز نامشخص"
        size = f"🧳 جا: {texts.kg_text(item.capacity_kg)}" if item.capacity_kg else "🧳 جا: نامشخص"
    else:
        title = f"📦 {texts.day_month(item.desired_date)}" if item.desired_date else (f"📦 تا {texts.day_month(day)}" if day else "📦 بدون عجله")
        size = f"⚖️ {texts.kg_text(item.weight_kg)}" if item.weight_kg else "⚖️ وزن نامشخص"
    what = "، ".join(category.name_fa for category in item.item_categories.all())
    refused = "، ".join(category.name_fa for category in item.excluded_categories.all())
    trust = search.completed_count(item.user)
    return {
        "id": item.pk,
        "kind": "supply" if supply else "demand",
        "title": title,
        "route": texts.request_route(item),
        "size": size,
        "what": what,
        "refused": refused if supply else "",
        "trust": f"✓ {texts.fa(trust)} ارسال موفق" if trust else "",
        "age": texts.relative_day(item.created_at),
        "href": f"/app/explore/{item.pk}/",
    }


@miniapp_login_required
@xframe_options_exempt
@require_GET
def courier_page(request: HttpRequest) -> HttpResponse:
    user = request.chamedoon_user
    filters = parse_filters(request.GET)
    role = ROLES[filters["role"]]
    rows = search.route_search(
        role=role,
        origin=_place(filters["from"]),
        destination=_place(filters["to"]),
        month=filters["month"],
        size=filters["size"],
        exclude_user=user,
    )
    noun = "مسافر" if role == RequestType.DEMAND else "بسته"
    months = [
        {"label": (f"تا آخر {texts.month_name(start)}" if index == 0 else texts.month_name(start)), "on": filters["month"] == key, "href": _href(filters, month="" if filters["month"] == key else key)}
        for index, (key, start, _end) in enumerate(texts.upcoming_months())
    ]
    sizes = [
        {"label": texts.size_label(code, role), "on": filters["size"] == code, "href": _href(filters, size="" if filters["size"] == code else code)}
        for code in texts.SIZES
    ]
    frequent = []
    for route in search.frequent_routes():
        value_from, value_to = f"{route[0]}:{route[1]}", f"{route[2]}:{route[3]}"
        frequent.append(
            {
                "label": texts.route_text(route[0], route[1], [(route[2], route[3])]),
                "on": (filters["from"], filters["to"]) == (value_from, value_to),
                "href": _href(filters, **{"from": value_from, "to": value_to}),
            }
        )
    ctx = _ctx(
        request,
        filters=filters,
        role=role,
        roles=[
            {"label": texts.KB_DEMAND, "on": filters["role"] == "D", "href": _href(filters, role="D")},
            {"label": texts.KB_SUPPLY, "on": filters["role"] == "S", "href": _href(filters, role="S")},
        ],
        swap_href=_href(filters, **{"from": filters["to"], "to": filters["from"]}),
        place_groups=_place_options(),
        months=months,
        sizes=sizes,
        frequent=frequent,
        cards=[card(item) for item in rows],
        count_line=f"{texts.fa(len(rows))} {noun} · نزدیک‌ترین تاریخ اول",
        noun=noun,
        can_alert=bool(filters["from"] and filters["to"]),
        new_href="/app/demand/new/" if role == RequestType.DEMAND else "/app/supply/new/",
        prohibited=texts.prohibited_items(),
        prohibited_footer=texts.PROHIBITED_FOOTER,
        tab="routes",
    )
    return render(request, "miniapp/courier.html", ctx)


@miniapp_login_required
@xframe_options_exempt
@require_POST
def courier_alert_create(request: HttpRequest) -> HttpResponse:
    user = request.chamedoon_user
    filters = parse_filters(request.POST)
    origin, destination = _place(filters["from"]), _place(filters["to"])
    if not origin or not destination or origin == destination:
        return redirect(_href(filters) + ("&" if "?" in _href(filters) else "?") + "need_route=1")
    window = None
    for key, start, end in texts.upcoming_months():
        if key == filters["month"]:
            window = (start, end)
    weight = None
    if filters["size"]:
        _label, package_kg, space_kg, _docs = texts.SIZES[filters["size"]]
        weight = Decimal(package_kg if ROLES[filters["role"]] == RequestType.DEMAND else space_kg)
    try:
        alerts.create_alert(
            telegram_user_id=user.telegram_user_id,
            chat_id=user.telegram_user_id,
            user=user,
            role=ROLES[filters["role"]],
            origin=origin,
            destination=destination,
            date_from=window[0] if window else None,
            date_to=window[1] if window else None,
            weight_kg=weight,
        )
    except alerts.AlertCapError:
        return redirect("/app/courier/alerts/?cap=1")
    return redirect("/app/courier/alerts/?saved=1")


@miniapp_login_required
@xframe_options_exempt
def courier_alerts(request: HttpRequest) -> HttpResponse:
    user = request.chamedoon_user
    if request.method == "POST":
        alert = CourierRouteAlert.objects.filter(pk=request.POST.get("alert") or 0, telegram_user_id=user.telegram_user_id).first()
        action = request.POST.get("action")
        if alert is not None and action == "pause":
            alert.active = not alert.active
            alert.save(update_fields=["active"])
        elif alert is not None and action == "delete":
            alert.delete()
        return redirect("/app/courier/alerts/")
    rows = [
        {"id": alert.pk, "summary": alerts.summary(alert), "active": alert.active}
        for alert in alerts.user_alerts(user.telegram_user_id)
    ]
    note = ""
    if request.GET.get("saved"):
        note = "🔔 ذخیره شد. مسافر یا بسته‌ی تازه‌ی این مسیر که بیاید، در ربات خبرتان می‌کنیم."
    elif request.GET.get("cap"):
        note = f"حداکثر {texts.fa(conf.MAX_ALERTS_PER_USER)} گوش به زنگ دارید. یکی را حذف کنید."
    ctx = _ctx(
        request,
        rows=rows,
        note=note,
        count_line=f"{texts.fa(len(rows))} از {texts.fa(conf.MAX_ALERTS_PER_USER)}",
        sending_on=conf.alerts_enabled(),
        new_href="/app/demand/new/",
        tab="alerts",
    )
    return render(request, "miniapp/courier_alerts.html", ctx)
