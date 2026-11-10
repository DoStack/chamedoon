"""HogQL reads from PostHog, cached for five minutes. A failed call returns None and the page says the number is missing."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.request

from django.core.cache import cache

logger = logging.getLogger(__name__)
TTL = 300
TIMEOUT = 20


def project_id() -> str:
    return os.environ.get("POSTHOG_PROJECT_ID", "709366")


def app_origin() -> str:
    return os.environ.get("POSTHOG_APP_ORIGIN", "https://us.posthog.com").rstrip("/")


def person_url(telegram_id: int) -> str:
    return f"{app_origin()}/project/{project_id()}/person/tg_{telegram_id}"


def replay_url(session_id: str) -> str:
    return f"{app_origin()}/project/{project_id()}/replay/{session_id}"


def project_url(path: str = "") -> str:
    return f"{app_origin()}/project/{project_id()}/{path.lstrip('/')}"


def hogql(query: str, ttl: int = TTL) -> list[list] | None:
    key = os.environ.get("POSTHOG_PERSONAL_KEY", "")
    if not key:
        return None
    cache_key = "ph:" + hashlib.sha1(query.encode()).hexdigest()
    found = cache.get(cache_key)
    if found is not None:
        return found
    host = os.environ.get("POSTHOG_API_HOST", app_origin()).rstrip("/")
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": query}, "name": "pulse"}).encode()
    request = urllib.request.Request(
        f"{host}/api/projects/{project_id()}/query/",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            result = json.load(response).get("results") or []
    except Exception:
        logger.warning("pulse_posthog_failed", exc_info=True)
        return None
    cache.set(cache_key, result, ttl)
    return result


# Events a person makes on purpose. Job events and passive ones (vitals, lifecycle) are left out.
ACTIVE_EVENTS = ("app_opened", "bot_message", "bot_command", "bot_button", "bot_chat", "feed_view", "listing_open", "contact_revealed", "deeplink_open")
NOISE_EVENTS = ("$web_vitals", "$autocapture", "header_collapse", "visibility_changed", "$pageleave", "$set", "tg_lifecycle", "api_request", "review_cycle", "ingest_cycle", "publish_cycle")


def _in(names) -> str:
    return "(" + ",".join("'" + n.replace("'", "") + "'" for n in names) + ")"


def team_filter(team_ids: list[int]) -> str:
    if not team_ids:
        return ""
    return " and distinct_id not in " + _in([f"tg_{t}" for t in team_ids])


def active_per_day(days: int, team_ids: list[int]) -> dict[str, int] | None:
    res = hogql(
        f"select toString(toDate(timestamp)) d, count(distinct person_id) from events where timestamp > now() - interval {int(days)} day "
        f"and event in {_in(ACTIVE_EVENTS)}{team_filter(team_ids)} group by d order by d"
    )
    return None if res is None else {r[0]: r[1] for r in res}


def active_counts(team_ids: list[int]) -> dict | None:
    res = hogql(
        "select count(distinct if(timestamp > now() - interval 1 day, person_id, null)), count(distinct if(timestamp > now() - interval 7 day, person_id, null)), "
        f"count(distinct person_id) from events where timestamp > now() - interval 30 day and event in {_in(ACTIVE_EVENTS)}{team_filter(team_ids)}"
    )
    if not res:
        return None
    d1, d7, d30 = res[0]
    return {"d1": d1, "d7": d7, "d30": d30}


def funnel(steps: list[str], days: int, team_ids: list[int]) -> list[int] | None:
    conds = ", ".join(f"event='{s}'" for s in steps)
    res = hogql(
        f"select level, count() from (select person_id, windowFunnel(86400)(toDateTime(timestamp), {conds}) as level from events "
        f"where timestamp > now() - interval {int(days)} day{team_filter(team_ids)} group by person_id) group by level"
    )
    if res is None:
        return None
    by_level = {int(r[0]): r[1] for r in res}
    return [sum(v for lvl, v in by_level.items() if lvl >= i + 1) for i in range(len(steps))]


def people_per_event(events: list[str], days: int, team_ids: list[int]) -> dict[str, int] | None:
    res = hogql(
        f"select event, count(distinct person_id) from events where timestamp > now() - interval {int(days)} day and event in {_in(events)}"
        f"{team_filter(team_ids)} group by event"
    )
    return None if res is None else {r[0]: r[1] for r in res}


def top_events(days: int, team_ids: list[int], limit: int = 15) -> list[list] | None:
    return hogql(
        f"select event, count(distinct person_id) p from events where timestamp > now() - interval {int(days)} day and event not in {_in(NOISE_EVENTS)} "
        f"and not startsWith(event, '$') {team_filter(team_ids)} group by event order by p desc limit {int(limit)}"
    )


def entry_kinds(days: int, team_ids: list[int]) -> list[list] | None:
    return hogql(
        f"select coalesce(properties.kind, 'none') k, count(distinct person_id) from events where event='deeplink_open' and timestamp > now() - interval {int(days)} day"
        f"{team_filter(team_ids)} group by k order by 2 desc limit 8"
    )


def stickiness(team_ids: list[int]) -> dict[int, int] | None:
    res = hogql(
        "select days, count() from (select person_id, count(distinct toDate(timestamp)) days from events where timestamp > now() - interval 7 day "
        f"and event in {_in(ACTIVE_EVENTS)}{team_filter(team_ids)} group by person_id) group by days order by days"
    )
    return None if res is None else {int(r[0]): r[1] for r in res}


def first_touch() -> dict[str, dict] | None:
    """Per distinct_id: the first launch source, link kind and city the app saw."""
    res = hogql(
        "select distinct_id, argMin(properties.launch_source, timestamp), argMin(properties.kind, timestamp), argMin(properties.city, timestamp), "
        "toString(min(timestamp)) from events where event in ('deeplink_open','app_opened') and startsWith(distinct_id, 'tg_') group by distinct_id"
    )
    if res is None:
        return None
    return {r[0]: {"launch": r[1] or "", "kind": r[2] or "", "city": (r[3] or "").title(), "at": r[4]} for r in res}


def person_events(telegram_id: int, limit: int = 300) -> list[dict] | None:
    res = hogql(
        f"select toString(timestamp), event, properties.source, properties.kind, `$session_id`, properties.city from events where distinct_id = 'tg_{int(telegram_id)}' "
        f"and event not in {_in(NOISE_EVENTS)} order by timestamp desc limit {int(limit)}",
        ttl=60,
    )
    if res is None:
        return None
    return [{"at": r[0], "event": r[1], "source": r[2] or "", "kind": r[3] or "", "session": r[4] or "", "city": r[5] or ""} for r in res]


def last_job_runs() -> dict[str, str] | None:
    res = hogql("select event, toString(max(timestamp)) from events where event in ('review_cycle','ingest_cycle','publish_cycle') and timestamp > now() - interval 3 day group by event")
    return None if res is None else {r[0]: r[1] for r in res}
