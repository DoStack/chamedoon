"""Back office operations: run a cron job now from the admin, and per-city daily numbers.

Run-now calls the same functions the cron views in api/cron_views.py call, in the admin request,
with a time budget that fits the function limit. Numbers are plain counts, no charts.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, datetime, timedelta

from django.utils import timezone

ADMIN_RUN_SECONDS = 240  # the function limit is 300 s; leave room to render the page
DASHBOARD_DAYS = 14
MAX_CITY_ROWS = 30


def _market_extract() -> dict:
    from api.cron_views import _cron_run_payload
    from item_requests.services import expire_due_requests
    from market.extract import extract_channels_only
    from market.ingest import market_ingest_enabled
    from market.job import DAILY_LOOKBACK_DAYS
    from support.services import auto_close_stale_tickets

    closed = auto_close_stale_tickets()
    if not market_ingest_enabled():
        return {"ok": False, "step": "extract", "error": "market ingest disabled", "expired": expire_due_requests(), "closed_tickets": closed}
    return _cron_run_payload(extract_channels_only(days=DAILY_LOOKBACK_DAYS), "extract", closed_tickets=closed)


def _market_convert() -> dict:
    from api.cron_views import _cron_run_payload
    from market.extract import convert_stored_posts
    from market.ingest import market_ingest_enabled
    from market.job import DAILY_LOOKBACK_DAYS

    if not market_ingest_enabled():
        return {"ok": False, "step": "convert", "error": "market ingest disabled"}
    return _cron_run_payload(convert_stored_posts(days=DAILY_LOOKBACK_DAYS), "convert")


def _market_publish() -> dict:
    from api.cron_views import _cron_run_payload
    from market.extract import publish_stored_requests

    return _cron_run_payload(publish_stored_requests(), "publish")


def _outreach_build() -> dict:
    from outreach.services import build_outreach_queue, building_enabled

    if not building_enabled():
        return {"ok": False, "step": "outreach-build", "error": "outreach disabled"}
    return {"ok": True, "step": "outreach-build", **build_outreach_queue().as_dict()}


def _home(step: str):
    def run() -> dict:
        from home.job import run_home_job

        return run_home_job(step=step, stop_at=time.monotonic() + ADMIN_RUN_SECONDS)

    return run


# name -> (button label, group, function)
RUN_JOBS: dict[str, tuple[str, str, object]] = {
    "market_extract": ("Extract", "Courier market", _market_extract),
    "market_convert": ("Convert", "Courier market", _market_convert),
    "market_publish": ("Publish", "Courier market", _market_publish),
    "outreach_build": ("Build queue", "Outreach", _outreach_build),
    "home_all": ("Whole job", "Housing", _home("all")),
    "home_ingest": ("Read sources", "Housing", _home("ingest")),
    "home_review": ("AI review", "Housing", _home("review")),
    "home_media": ("Store photos", "Housing", _home("media")),
    "home_vision": ("Read photos", "Housing", _home("vision")),
    "home_publish": ("Post to channels", "Housing", _home("publish")),
    "home_alerts": ("Send alerts", "Housing", _home("alerts")),
}


def run_job(name: str) -> dict:
    """Run one job by name. Errors come back as {"ok": False, "error": ...}, never as an exception."""
    entry = RUN_JOBS.get(name)
    if entry is None:
        return {"ok": False, "error": f"unknown job {name!r}"}
    started = time.monotonic()
    try:
        result = entry[2]()
    except Exception as exc:  # noqa: BLE001 - shown to the operator instead of a 500
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
    return {"job": name, "seconds": round(time.monotonic() - started, 1), **result}


def job_groups() -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for name, (label, group, _func) in RUN_JOBS.items():
        groups.setdefault(group, []).append({"name": name, "label": label})
    return [{"title": title, "jobs": jobs} for title, jobs in groups.items()]


# ---------------------------------------------------------------- numbers


def _city_label(city: str, country: str = "") -> str:
    city = (city or "").strip() or "(no city)"
    return f"{city} ({country.upper()})" if country else city


def _table(pairs, days: list[date]) -> dict:
    """pairs: (city label, aware datetime). Returns rows of per-day counts, busiest city first."""
    index = {day: i for i, day in enumerate(days)}
    counts: dict[str, list[int]] = defaultdict(lambda: [0] * len(days))
    for city, moment in pairs:
        if moment is None:
            continue
        position = index.get(timezone.localtime(moment).date())
        if position is not None:
            counts[city][position] += 1
    rows = sorted(({"city": city, "counts": values, "total": sum(values)} for city, values in counts.items()), key=lambda row: (-row["total"], row["city"]))
    if len(rows) > MAX_CITY_ROWS:
        rest = rows[MAX_CITY_ROWS - 1 :]
        other = [sum(row["counts"][i] for row in rest) for i in range(len(days))]
        rows = rows[: MAX_CITY_ROWS - 1] + [{"city": f"{len(rest)} other cities", "counts": other, "total": sum(other)}]
    totals = [sum(row["counts"][i] for row in rows) for i in range(len(days))]
    return {"rows": rows, "totals": totals, "total": sum(totals)}


def city_daily_counts(days: int = DASHBOARD_DAYS, now=None) -> dict:
    """Per city, per day (UTC), for the last `days` days including today."""
    from home.models import HomeAlert, HomeListing, HomePublication, ListingStatus
    from item_requests.models import ItemRequest
    from matching.models import Match

    now = now or timezone.now()
    today = timezone.localtime(now).date()
    day_list = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    since = timezone.make_aware(datetime.combine(day_list[0], datetime.min.time()))

    accepted = (
        (_city_label(city, country), created)
        for city, country, created in HomeListing.objects.filter(created_at__gte=since)
        .exclude(status=ListingStatus.DUPLICATE)
        .values_list("city", "country", "created_at")
    )
    city_posts = [
        (_city_label(city, country), published)
        for city, country, published in HomePublication.objects.filter(published_at__gte=since).values_list(
            "channel__city", "channel__country", "published_at"
        )
    ]
    country_posts = [
        (_city_label(city, country), published)
        for city, country, published in HomeListing.objects.filter(published_at__gte=since, channel__isnull=False).values_list(
            "city", "country", "published_at"
        )
    ]
    alerts = (
        (_city_label(city, country), created)
        for city, country, created in HomeAlert.objects.filter(created_at__gte=since).values_list("city", "country", "created_at")
    )
    requests = (
        (_city_label(city, country), created)
        for city, country, created in ItemRequest.objects.filter(created_at__gte=since).values_list(
            "origin_city", "origin_country", "created_at"
        )
    )
    matches = (
        (_city_label(city, country), created)
        for city, country, created in Match.objects.filter(created_at__gte=since).values_list(
            "demand_request__origin_city", "demand_request__origin_country", "created_at"
        )
    )
    return {
        "days": day_list,
        "tables": [
            {"key": "housing_accepted", "title": "Housing listings accepted", "note": "New listings the AI accepted, merged reposts left out, by listing city.", **_table(accepted, day_list)},
            {"key": "housing_published", "title": "Housing posts to channels", "note": "City channel posts plus country channel posts, by city.", **_table(city_posts + country_posts, day_list)},
            {"key": "alerts_saved", "title": "Housing alerts saved", "note": "New alerts (گوش به زنگ), by alert city.", **_table(alerts, day_list)},
            {"key": "courier_requests", "title": "Courier requests", "note": "New requests, senders and travelers, imported or not, by origin city.", **_table(requests, day_list)},
            {"key": "courier_matches", "title": "Courier matches", "note": "New matches, by the origin city of the demand.", **_table(matches, day_list)},
        ],
    }
