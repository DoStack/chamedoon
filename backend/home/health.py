"""Is housing intake alive? One check for the server's health script, the admin and a test.

Every stop has to show up somewhere a person looks: a FloodWait pause, no new post for an hour in the day, a review
queue that stops moving, too many review errors, or most sources failing. `intake_health()` reads the database only
and returns the problems as short lines; `manage.py home_health` prints them and exits 1 when there is one, so the
host's `/opt/chamedoon-db/bin/check.sh` can pass them to its existing Telegram alert.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Max, Q
from django.utils import timezone

from home import conf
from home.models import HomePost, HomeSettings, HomeSource, PostStatus

# Groups in Canada and Germany post around the clock except roughly 04:00 to 06:00 UTC (Vancouver midnight, Berlin dawn).
QUIET_HOURS_UTC = range(4, 6)
INGEST_SILENT_MINUTES = 60
INGEST_SILENT_MINUTES_QUIET = 180
# The worker looks at the queue every 5 minutes; a post still waiting after this long means the review is stuck.
QUEUE_STUCK_MINUTES = 60
ERRORS_24H_MAX = 20
# More than this share of enabled sources failing on their last read.
SOURCES_FAILING_SHARE = 0.25
# Errors that are not a failure of the source: a page that will be read next run, or a lookup that waits its turn.
BENIGN_SOURCE_ERRORS = ("Telegram did not answer in time", "waits for a username lookup")


def intake_health(now=None) -> dict:
    now = now or timezone.now()
    problems: list[str] = []
    metrics: dict[str, object] = {}
    if not conf.home_enabled():
        return {"ok": True, "problems": [], "metrics": {"home": "off"}}

    settings_row = HomeSettings.load()
    if settings_row.ingest_paused_until and settings_row.ingest_paused_until > now:
        problems.append(
            f"ingest paused until {settings_row.ingest_paused_until:%H:%M} UTC ({settings_row.ingest_pause_reason or 'FloodWait'})"
        )

    newest = HomePost.objects.aggregate(at=Max("ingested_at"))["at"]
    silent = int((now - newest).total_seconds() // 60) if newest else None
    metrics["ingest_silent_min"] = silent if silent is not None else "never"
    limit = INGEST_SILENT_MINUTES_QUIET if now.hour in QUIET_HOURS_UTC else INGEST_SILENT_MINUTES
    if conf.step_enabled("ingest") and (silent is None or silent > limit):
        problems.append(f"no new post for {silent if silent is not None else '∞'} min (limit {limit})")

    errors_24h = HomePost.objects.filter(status=PostStatus.ERROR, reviewed_at__gte=now - timedelta(hours=24)).count()
    metrics["review_errors_24h"] = errors_24h
    if errors_24h > ERRORS_24H_MAX:
        problems.append(f"{errors_24h} review errors in 24 h (limit {ERRORS_24H_MAX})")

    stuck = HomePost.objects.filter(status=PostStatus.NEW, ingested_at__lt=now - timedelta(minutes=QUEUE_STUCK_MINUTES)).count()
    metrics["queue_stuck"] = stuck
    if stuck:
        problems.append(f"{stuck} posts waiting for review over {QUEUE_STUCK_MINUTES} min")

    enabled = HomeSource.objects.filter(enabled=True)
    total = enabled.count()
    benign = Q()
    for text in BENIGN_SOURCE_ERRORS:
        benign |= Q(last_error__startswith=text)
    failing = list(enabled.exclude(last_error="").exclude(benign).values_list("username", flat=True))
    metrics["sources_failing"] = f"{len(failing)}/{total}"
    if total and len(failing) > total * SOURCES_FAILING_SHARE:
        problems.append(f"{len(failing)} of {total} sources failing on their last read")

    return {"ok": not problems, "problems": problems, "metrics": metrics}


def health_line(report: dict) -> str:
    metrics = " ".join(f"{key}={value}" for key, value in report["metrics"].items())
    if report["ok"]:
        return f"OK {metrics}"
    return f"PROBLEM [{'; '.join(report['problems'])}] {metrics}"
