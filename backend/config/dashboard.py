from __future__ import annotations

import json
from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDate
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from item_requests.models import ChannelStatus, ItemRequest, RequestStatus, RequestType
from matching.models import Match, MatchRating, MatchStatus
from market.models import MarketIngestState, MarketPost, MarketRole
from users.models import User

TREND_DAYS = 14
STALE_WAITING_DAYS = 2
EXPIRING_DAYS = 3


def dashboard_callback(request, context: dict) -> dict:
    return build_dashboard_context(request, context)


def build_dashboard_context(_request, context: dict) -> dict:
    now = timezone.now()
    since = now - timedelta(days=TREND_DAYS)
    active = RequestStatus.ACTIVE
    requests = ItemRequest.objects.all()
    matches = Match.objects.all()
    posts = MarketPost.objects.all()

    active_demand = requests.filter(status=active, type=RequestType.DEMAND).count()
    active_supply = requests.filter(status=active, type=RequestType.SUPPLY).count()
    imported_active = requests.filter(status=active, imported=True).count()
    live_active = requests.filter(status=active, imported=False).count()
    suggested = matches.filter(status=MatchStatus.SUGGESTED).count()
    connected = matches.filter(status=MatchStatus.CONNECTED).count()
    completed = matches.filter(status=MatchStatus.COMPLETED).count()
    waiting = matches.filter(
        status__in={MatchStatus.ACCEPTED_BY_DEMAND, MatchStatus.ACCEPTED_BY_SUPPLY}
    ).count()
    channel_failed = requests.filter(channel_status=ChannelStatus.FAILED).count()
    pending_posts = posts.filter(item_request__isnull=True).exclude(
        skip_reason__in={"noise", "ad"}
    ).count()
    skipped_ads = posts.filter(Q(role=MarketRole.NOISE) | Q(skip_reason__in={"noise", "ad"})).count()
    avg_rating = MatchRating.objects.aggregate(avg=Avg("score"))["avg"]
    new_users = User.objects.filter(created_at__gte=since).count()
    completions_14d = matches.filter(status=MatchStatus.COMPLETED, updated_at__gte=since).count()
    connected_14d = matches.filter(
        status__in={MatchStatus.CONNECTED, MatchStatus.COMPLETED},
        updated_at__gte=since,
    ).count()
    requests_14d = requests.filter(created_at__gte=since).count()
    decided = matches.filter(
        status__in={
            MatchStatus.CONNECTED,
            MatchStatus.COMPLETED,
            MatchStatus.REJECTED,
            MatchStatus.EXPIRED,
        }
    ).count()
    handed_off = connected + completed
    connect_rate = _pct(handed_off, decided) if decided else "—"

    unmatched_demand_qs = (
        requests.filter(status=active, type=RequestType.DEMAND)
        .annotate(match_n=Count("demand_matches"))
        .filter(match_n=0)
    )
    unmatched_demand = unmatched_demand_qs.count()

    kpis = [
        _kpi(
            "Active demand",
            active_demand,
            "People who need a traveler",
            "package_2",
            _changelist("item_requests", "itemrequest", status="ACTIVE", type="DEMAND"),
        ),
        _kpi(
            "Active supply",
            active_supply,
            "Travelers who can carry",
            "luggage",
            _changelist("item_requests", "itemrequest", status="ACTIVE", type="SUPPLY"),
        ),
        _kpi(
            "Unmatched demand",
            unmatched_demand,
            "Active senders with no match yet",
            "search_off",
            _changelist("item_requests", "itemrequest", status="ACTIVE", type="DEMAND"),
        ),
        _kpi(
            "Suggested matches",
            suggested,
            "Waiting for someone to request",
            "handshake",
            _changelist("matching", "match", status="SUGGESTED"),
        ),
        _kpi(
            "Waiting / connected",
            f"{waiting} / {connected}",
            "One-side accept vs live handovers",
            "link",
            _changelist("matching", "match", status="CONNECTED"),
        ),
        _kpi(
            "Connect rate",
            connect_rate,
            "Connected or completed vs decided matches",
            "percent",
            _changelist("matching", "match"),
        ),
        _kpi(
            "Live / imported",
            f"{live_active} / {imported_active}",
            "Active listings by origin",
            "campaign",
            _changelist("item_requests", "itemrequest", imported="1", status="ACTIVE"),
        ),
        _kpi(
            "New users (14d)",
            new_users,
            f"{User.objects.filter(is_active=True).count()} active Telegram users",
            "group",
            reverse("admin:users_user_changelist"),
        ),
        _kpi(
            "Completions (14d)",
            completions_14d,
            f"{connected_14d} connected in the same window",
            "task_alt",
            _changelist("matching", "match", status="COMPLETED"),
        ),
        _kpi(
            "Channel failed",
            channel_failed,
            "Koolbar channel publish errors",
            "error",
            _changelist("item_requests", "itemrequest", channel_status="FAILED"),
        ),
        _kpi(
            "Market queue",
            pending_posts,
            f"{skipped_ads} ads/noise already skipped",
            "inbox",
            reverse("admin:market_marketpost_changelist"),
        ),
        _kpi(
            "New requests (14d)",
            requests_14d,
            "Demand and supply created recently",
            "trending_up",
            reverse("admin:item_requests_itemrequest_changelist"),
        ),
    ]

    context.update(
        {
            "kpis": kpis,
            "kpi_groups": [
                {"title": "Live marketplace", "cards": [kpis[0], kpis[1], kpis[2], kpis[6]]},
                {"title": "Matching", "cards": [kpis[3], kpis[4], kpis[5], kpis[8]]},
                {"title": "Operations", "cards": [kpis[7], kpis[11], kpis[9], kpis[10]]},
            ],
            "funnel_table": _funnel_table(requests, matches, since),
            "match_table": _match_pipeline(matches),
            "route_table": _top_routes(requests.filter(status=active)),
            "channel_table": _channel_status(requests),
            "unmatched_table": _unmatched_demand(unmatched_demand_qs),
            "expiring_table": _expiring_soon(requests, now),
            "stale_table": _stale_waiting(matches, now),
            "skip_table": _skip_reasons(posts),
            "ingest_table": _ingest_health(),
            "requests_chart": _requests_trend_chart(requests),
            "match_chart": _match_pipeline_chart(matches),
            "match_trend_chart": _match_trend_chart(matches),
            "route_chart": _top_routes_chart(requests.filter(status=active)),
            "skip_chart": _skip_reasons_chart(posts),
            "users_chart": _users_trend_chart(),
            "rating_avg": f"{avg_rating:.1f}" if avg_rating else "—",
            "rating_count": MatchRating.objects.count(),
            "now_label": now.strftime("%Y-%m-%d %H:%M UTC"),
        }
    )
    return context


def _pct(numerator: int, denominator: int) -> str:
    if not denominator:
        return "—"
    return f"{round(100 * numerator / denominator)}%"


def _kpi(title: str, metric, footer: str, icon: str, href: str) -> dict:
    return {"title": title, "metric": metric, "footer": footer, "icon": icon, "href": href}


def _changelist(app: str, model: str, **filters) -> str:
    url = reverse(f"admin:{app}_{model}_changelist")
    if not filters:
        return url
    query = "&".join(f"{key}__exact={value}" for key, value in filters.items())
    return f"{url}?{query}"


def _funnel_table(requests, matches, since) -> dict:
    created = requests.filter(created_at__gte=since).count()
    suggested = matches.filter(created_at__gte=since).count()
    waiting = matches.filter(
        status__in={MatchStatus.ACCEPTED_BY_DEMAND, MatchStatus.ACCEPTED_BY_SUPPLY},
        updated_at__gte=since,
    ).count()
    connected = matches.filter(status=MatchStatus.CONNECTED, updated_at__gte=since).count()
    completed = matches.filter(status=MatchStatus.COMPLETED, updated_at__gte=since).count()
    rejected = matches.filter(status=MatchStatus.REJECTED, updated_at__gte=since).count()
    rows = [
        ["Requests created", created, "—"],
        ["Matches created", suggested, _pct(suggested, created)],
        ["Waiting on one side", waiting, _pct(waiting, suggested)],
        ["Connected", connected, _pct(connected, suggested)],
        ["Completed", completed, _pct(completed, suggested)],
        ["Rejected", rejected, _pct(rejected, suggested)],
    ]
    return {"headers": ["Step (14 days)", "Count", "Rate"], "rows": rows}


def _match_pipeline(matches) -> dict:
    rows = []
    for status, label in MatchStatus.choices:
        count = matches.filter(status=status).count()
        rows.append(
            [
                label,
                count,
                _link(_changelist("matching", "match", status=status), "Open"),
            ]
        )
    return {"headers": ["Status", "Count", ""], "rows": rows}


def _top_routes(queryset) -> dict:
    rows = []
    grouped = (
        queryset.values("origin_country", "origin_city", "destination_country", "destination_city")
        .annotate(
            n=Count("id"),
            demand=Count("id", filter=Q(type=RequestType.DEMAND)),
            supply=Count("id", filter=Q(type=RequestType.SUPPLY)),
        )
        .order_by("-n")[:8]
    )
    for row in grouped:
        route = (
            f"{row['origin_city']}, {row['origin_country']} → "
            f"{row['destination_city']}, {row['destination_country']}"
        )
        rows.append([route, row["demand"], row["supply"], row["n"]])
    return {"headers": ["Route", "Demand", "Supply", "Total"], "rows": rows}


def _channel_status(queryset) -> dict:
    rows = []
    grouped = queryset.values("channel_status").annotate(n=Count("id")).order_by("-n")
    labels = dict(ChannelStatus.choices)
    for row in grouped:
        status = row["channel_status"]
        rows.append(
            [
                labels.get(status, status),
                row["n"],
                _link(_changelist("item_requests", "itemrequest", channel_status=status), "Open"),
            ]
        )
    return {"headers": ["Channel status", "Requests", ""], "rows": rows}


def _unmatched_demand(queryset) -> dict:
    rows = []
    for item in queryset.select_related("user").order_by("-created_at")[:10]:
        rows.append(
            [
                _link(
                    reverse("admin:item_requests_itemrequest_change", args=[item.pk]),
                    f"#{item.pk}",
                ),
                item.user.first_name or str(item.user.telegram_user_id),
                f"{item.origin_city} → {item.destination_city}",
                item.created_at.strftime("%Y-%m-%d"),
                "Imported" if item.imported else "Live",
            ]
        )
    return {"headers": ["Request", "Sender", "Route", "Created", "Origin"], "rows": rows}


def _expiring_soon(queryset, now) -> dict:
    until = now + timedelta(days=EXPIRING_DAYS)
    rows = []
    items = (
        queryset.filter(status=RequestStatus.ACTIVE, expires_at__gt=now, expires_at__lte=until)
        .order_by("expires_at")[:10]
    )
    for item in items:
        rows.append(
            [
                _link(
                    reverse("admin:item_requests_itemrequest_change", args=[item.pk]),
                    f"#{item.pk}",
                ),
                item.type,
                f"{item.origin_city} → {item.destination_city}",
                item.expires_at.strftime("%Y-%m-%d %H:%M"),
            ]
        )
    return {"headers": ["Request", "Type", "Route", "Expires"], "rows": rows}


def _stale_waiting(matches, now) -> dict:
    cutoff = now - timedelta(days=STALE_WAITING_DAYS)
    rows = []
    items = (
        matches.filter(
            status__in={MatchStatus.ACCEPTED_BY_DEMAND, MatchStatus.ACCEPTED_BY_SUPPLY},
            updated_at__lte=cutoff,
        )
        .select_related("demand_request", "supply_request")
        .order_by("updated_at")[:10]
    )
    for match in items:
        rows.append(
            [
                _link(reverse("admin:matching_match_change", args=[match.pk]), f"#{match.pk}"),
                match.get_status_display(),
                f"{match.demand_request.origin_city} → {match.demand_request.destination_city}",
                str(match.score),
                match.updated_at.strftime("%Y-%m-%d"),
            ]
        )
    return {"headers": ["Match", "Status", "Route", "Score", "Last update"], "rows": rows}


def _skip_reasons(posts) -> dict:
    rows = []
    grouped = (
        posts.exclude(skip_reason="")
        .values("skip_reason")
        .annotate(n=Count("id"))
        .order_by("-n")[:8]
    )
    for row in grouped:
        reason = row["skip_reason"]
        rows.append(
            [
                reason,
                row["n"],
                _link(f"{reverse('admin:market_marketpost_changelist')}?skip_reason={reason}", "Open"),
            ]
        )
    return {"headers": ["Skip reason", "Posts", ""], "rows": rows}


def _ingest_health() -> dict:
    rows = []
    for state in MarketIngestState.objects.order_by("channel_username"):
        when = state.last_run_at.strftime("%Y-%m-%d %H:%M") if state.last_run_at else "—"
        error = (state.last_error or "").strip()[:80] or "ok"
        rows.append([f"@{state.channel_username}", when, state.last_created, error])
    return {"headers": ["Channel", "Last run", "Created", "Status"], "rows": rows}


def _link(url: str, label: str):
    return format_html('<a class="text-primary-600 underline" href="{}">{}</a>', url, label)


def _chart(labels: list[str], datasets: list[dict]) -> str:
    return json.dumps({"labels": labels, "datasets": datasets})


def _series(label: str, data: list[int], color: str, chart_type: str | None = None) -> dict:
    payload = {
        "label": label,
        "data": data,
        "backgroundColor": color,
        "borderColor": color,
        "displayYAxis": True,
    }
    if chart_type:
        payload["type"] = chart_type
    return payload


def _empty_days():
    today = timezone.now().date()
    return [today - timedelta(days=offset) for offset in range(TREND_DAYS - 1, -1, -1)]


def _requests_trend_chart(queryset) -> str:
    days = _empty_days()
    start = timezone.now() - timedelta(days=TREND_DAYS)
    demand = {day: 0 for day in days}
    supply = {day: 0 for day in days}
    rows = (
        queryset.filter(created_at__gte=start)
        .annotate(day=TruncDate("created_at"))
        .values("day", "type")
        .annotate(n=Count("id"))
    )
    for row in rows:
        day = row["day"]
        if day not in demand:
            continue
        if row["type"] == RequestType.DEMAND:
            demand[day] = row["n"]
        elif row["type"] == RequestType.SUPPLY:
            supply[day] = row["n"]
    return _chart(
        [day.strftime("%b %d") for day in days],
        [
            _series("Demand", [demand[day] for day in days], "var(--color-primary-700)"),
            _series("Supply", [supply[day] for day in days], "var(--color-primary-400)"),
        ],
    )


def _users_trend_chart() -> str:
    days = _empty_days()
    start = timezone.now() - timedelta(days=TREND_DAYS)
    counts = {day: 0 for day in days}
    rows = (
        User.objects.filter(created_at__gte=start)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(n=Count("id"))
    )
    for row in rows:
        day = row["day"]
        if day in counts:
            counts[day] = row["n"]
    return _chart(
        [day.strftime("%b %d") for day in days],
        [_series("New users", [counts[day] for day in days], "var(--color-primary-600)")],
    )


def _match_trend_chart(queryset) -> str:
    days = _empty_days()
    start = timezone.now() - timedelta(days=TREND_DAYS)
    created = {day: 0 for day in days}
    rows = (
        queryset.filter(created_at__gte=start)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(n=Count("id"))
    )
    for row in rows:
        day = row["day"]
        if day in created:
            created[day] = row["n"]
    return _chart(
        [day.strftime("%b %d") for day in days],
        [_series("Matches created", [created[day] for day in days], "var(--color-primary-500)")],
    )


def _match_pipeline_chart(matches) -> str:
    labels = []
    values = []
    for status, label in MatchStatus.choices:
        labels.append(label)
        values.append(matches.filter(status=status).count())
    return _chart(labels, [_series("Matches", values, "var(--color-primary-600)")])


def _top_routes_chart(queryset) -> str:
    grouped = (
        queryset.values("origin_city", "destination_city")
        .annotate(n=Count("id"))
        .order_by("-n")[:8]
    )
    labels = [f"{row['origin_city']} → {row['destination_city']}" for row in grouped]
    values = [row["n"] for row in grouped]
    if not labels:
        labels = ["No live routes"]
        values = [0]
    return _chart(labels, [_series("Active requests", values, "var(--color-primary-600)")])


def _skip_reasons_chart(posts) -> str:
    grouped = (
        posts.exclude(skip_reason="")
        .values("skip_reason")
        .annotate(n=Count("id"))
        .order_by("-n")[:8]
    )
    labels = [row["skip_reason"] for row in grouped]
    values = [row["n"] for row in grouped]
    if not labels:
        labels = ["none"]
        values = [0]
    return _chart(labels, [_series("Posts", values, "var(--color-primary-500)")])
