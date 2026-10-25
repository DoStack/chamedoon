"""Which features are missing their configuration, and when each job last did its work.

Incident 2026-10-23: a deploy to a new Vercel project lacked CRON_SECRET, the market Telegram
session and the LLM key. Every cron and the housing job silently did nothing for hours. This
module is the one place that answers "what is missing?" for the cron JSON, the logs, the admin
System health page and /api/health.

It reports setting NAMES only. Never put a value, its length or a prefix in anything returned here.
Values are read through the same getters the jobs use, so a value saved in an admin settings row
counts as set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db.models import Max
from django.utils import timezone

logger = logging.getLogger("chamedoon.config")

OK = "ok"
MISSING = "missing"
OFF = "off"

TELEGRAM_READER_NAMES = ("MARKET_TG_API_ID", "MARKET_TG_API_HASH", "MARKET_TG_SESSION")
OUTREACH_SENDER_NAMES = ("OUTREACH_TG_API_ID", "OUTREACH_TG_API_HASH", "OUTREACH_TG_SESSION")
LLM_NAME = "OPENROUTER_API_KEY"
R2_NAMES = {
    "account_id": "R2_ACCOUNT_ID",
    "access_key": "R2_ACCESS_KEY_ID",
    "secret_key": "R2_SECRET_ACCESS_KEY",
    "bucket": "HOME_MEDIA_BUCKET",
    "public_base": "HOME_MEDIA_PUBLIC_BASE",
}


@dataclass
class Feature:
    key: str
    label: str
    state: str = OK
    missing: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def message(self) -> str:
        if self.state == MISSING and self.missing:
            return missing_message(self.missing)
        return self.note

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "state": self.state, "missing": list(self.missing), "note": self.note}


def missing_message(names) -> str:
    return "missing env: " + ", ".join(names)


def report_missing(where: str, names) -> str:
    """Log an ERROR line Vercel shows, and return the text for the JSON `skipped` field."""
    message = missing_message(names)
    logger.error("%s skipped: %s", where, message)
    return message


def _setting(name: str) -> bool:
    value = getattr(settings, name, "")
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def _blank(names) -> list[str]:
    return [name for name in names if not _setting(name)]


def _feature(key: str, label: str, missing: list[str], *, off: str = "", note: str = "") -> Feature:
    if off:
        return Feature(key, label, OFF, [], off)
    if missing:
        return Feature(key, label, MISSING, missing, note)
    return Feature(key, label, OK, [], note)


# --- per feature --------------------------------------------------------------------------


def telegram_reader_missing() -> list[str]:
    """Names to set for the group reader. Empty when the market or the outreach session is complete."""
    from market.telegram_history import history_configured

    if history_configured():
        return []
    return _blank(TELEGRAM_READER_NAMES) or list(TELEGRAM_READER_NAMES)


def llm_missing() -> list[str]:
    from ai.llm import llm_status

    return [] if llm_status()["enabled"] else [LLM_NAME]


def cron_auth() -> Feature:
    return _feature("cron_auth", "Cron auth", _blank(["CRON_SECRET"]))


def market_crawl() -> Feature:
    from market.ingest import market_ingest_enabled

    off = "" if market_ingest_enabled() else "MARKET_INGEST_ENABLED is off or no market channel is listed"
    return _feature(
        "market_crawl",
        "Market crawl (Telegram reader)",
        telegram_reader_missing(),
        off=off,
        note="without the reader only public previews are read; private groups are missed",
    )


def housing_ingest() -> Feature:
    from home import conf

    off = "" if conf.step_enabled("ingest") else "housing ingest is switched off in Home settings"
    return _feature("housing_ingest", "Housing ingest", telegram_reader_missing(), off=off)


def ai_review() -> Feature:
    from home import conf
    from market.ingest import market_ingest_enabled

    used = conf.step_enabled("review") or market_ingest_enabled()
    return _feature(
        "ai_review",
        "AI review",
        llm_missing(),
        off="" if used else "housing review and market ingest are both off",
        note="OPENAI_API_KEY also works instead of OPENROUTER_API_KEY",
    )


def housing_publish() -> Feature:
    from home import conf
    from home.models import HomeChannel

    if not conf.step_enabled("publish"):
        return _feature("housing_publish", "Housing bot / publish", [], off="publishing is switched off in Home settings")
    missing = [] if conf.bot_token() else ["HOME_BOT_TOKEN"]
    feature = _feature("housing_publish", "Housing bot / publish", missing)
    if not HomeChannel.objects.filter(enabled=True).exists():
        feature.state = MISSING
        feature.note = "no housing channel is switched on (admin: Chamedoon Home > Channels)"
    return feature


def photo_storage() -> Feature:
    from home import conf

    label = "Photo storage (R2)"
    if not conf.step_enabled("media"):
        return _feature("photo_storage", label, [], off="photos are switched off in Home settings")
    values = conf.storage_values()
    backend = values["backend"]
    if not backend:
        return _feature("photo_storage", label, [], off="HOME_MEDIA_BACKEND is not set, photos are not stored")
    if backend == "r2":
        return _feature("photo_storage", label, [name for key, name in R2_NAMES.items() if not values[key]])
    if backend == "local":
        return _feature("photo_storage", label, _blank(["HOME_MEDIA_LOCAL_DIR"]), note="local disk backend")
    return _feature("photo_storage", label, ["HOME_MEDIA_BACKEND"], note="HOME_MEDIA_BACKEND must be r2 or local")


def courier_bot() -> Feature:
    missing = _blank(["TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET"])
    note = "" if _setting("TELEGRAM_CHANNEL_ENABLED") else "channel posting is off (TELEGRAM_CHANNEL_ENABLED)"
    return _feature("courier_bot", "Courier bot / channel", missing, note=note)


def outreach_sender() -> Feature:
    from outreach import conf as outreach_conf

    off = "" if outreach_conf.sending_enabled() else "outreach sending is switched off"
    return _feature("outreach_sender", "Outreach sender", _blank(OUTREACH_SENDER_NAMES), off=off)


def alerts_webhook() -> Feature:
    from home import conf

    if not conf.step_enabled("alerts"):
        return _feature("alerts_webhook", "Housing alerts / bot webhook", [], off="alerts are switched off in Home settings")
    missing = []
    if not conf.bot_token():
        missing.append("HOME_BOT_TOKEN")
    if not conf.webhook_secret():
        missing.append("HOME_WEBHOOK_SECRET")
    return _feature("alerts_webhook", "Housing alerts / bot webhook", missing)


def housing_freshness() -> Feature:
    """Expiry needs nothing; the channel edits of rented or reported listings need the housing bot."""
    from home import conf

    label = "Housing freshness (expiry, rented/reported post edits)"
    if not conf.step_enabled("freshness"):
        return _feature("housing_freshness", label, [], off="freshness is switched off in Home settings")
    missing = [] if conf.bot_token() else ["HOME_BOT_TOKEN"]
    return _feature("housing_freshness", label, missing)


def housing_owner_checks() -> Feature:
    from home import conf

    label = "Housing owner checks («هنوز موجوده؟»)"
    if not conf.owner_checks_enabled():
        return _feature("housing_owner_checks", label, [], off="owner checks are switched off in Home settings")
    return _feature("housing_owner_checks", label, [] if conf.bot_token() else ["HOME_BOT_TOKEN"])


FEATURES = (
    cron_auth,
    market_crawl,
    housing_ingest,
    ai_review,
    housing_publish,
    photo_storage,
    courier_bot,
    outreach_sender,
    alerts_webhook,
    housing_freshness,
    housing_owner_checks,
)


def config_report() -> list[Feature]:
    """One row per feature: ok, missing [names] or off (switched off on purpose)."""
    return [check() for check in FEATURES]


def missing_names(report: list[Feature] | None = None) -> list[str]:
    report = config_report() if report is None else report
    names: list[str] = []
    for feature in report:
        if feature.state != MISSING:
            continue
        for name in feature.missing:
            if name not in names:
                names.append(name)
    return names


# --- freshness ----------------------------------------------------------------------------


@dataclass
class Freshness:
    key: str
    label: str
    at: object
    max_age: timedelta
    feature: str
    active: bool = True

    @property
    def stale(self) -> bool:
        if not self.active:
            return False
        return self.at is None or timezone.now() - self.at > self.max_age

    @property
    def max_age_label(self) -> str:
        hours = int(self.max_age.total_seconds() // 3600)
        return f"{hours} h"


def _latest(*values):
    found = [value for value in values if value is not None]
    return max(found) if found else None


def freshness_report(report: list[Feature] | None = None) -> list[Freshness]:
    """When each job last did real work. A row is stale when its feature is on and the time is too old."""
    from home.models import HomeListing, HomePost, HomePublication, HomeSource
    from item_requests.models import ItemRequest
    from market.models import MarketIngestState
    from outreach.models import OutreachMessage

    report = config_report() if report is None else report
    states = {feature.key: feature.state for feature in report}
    channel_on = _setting("TELEGRAM_CHANNEL_ENABLED")

    def on(key: str) -> bool:
        return states.get(key) != OFF

    rows = [
        # Six housing runs a day, at most 6 h apart: 7 h leaves room for a slow run.
        Freshness(
            "housing_ingest",
            "Housing ingest (last clean source run)",
            HomeSource.objects.filter(enabled=True, last_error="").aggregate(at=Max("last_run_at"))["at"],
            timedelta(hours=7),
            "housing_ingest",
        ),
        Freshness(
            "housing_posts",
            "Newest housing post ingested",
            HomePost.objects.aggregate(at=Max("ingested_at"))["at"],
            timedelta(hours=24),
            "housing_ingest",
        ),
        Freshness(
            "housing_review",
            "Last housing AI review",
            HomePost.objects.aggregate(at=Max("reviewed_at"))["at"],
            timedelta(hours=24),
            "ai_review",
        ),
        Freshness(
            "housing_publish",
            "Last housing publish",
            _latest(
                HomeListing.objects.aggregate(at=Max("published_at"))["at"],
                HomePublication.objects.aggregate(at=Max("published_at"))["at"],
            ),
            timedelta(hours=24),
            "housing_publish",
        ),
        Freshness(
            "courier_channel",
            "Last courier channel post",
            ItemRequest.objects.aggregate(at=Max("channel_published_at"))["at"],
            timedelta(hours=48),
            "courier_bot",
            active=channel_on,
        ),
        Freshness(
            "market_extract",
            "Last market extract (clean run)",
            MarketIngestState.objects.filter(last_error="").aggregate(at=Max("last_run_at"))["at"],
            timedelta(hours=26),
            "market_crawl",
        ),
        Freshness(
            "market_publish",
            "Last market publish to channel",
            ItemRequest.objects.filter(imported=True).aggregate(at=Max("channel_published_at"))["at"],
            timedelta(hours=48),
            "market_crawl",
            active=channel_on,
        ),
        Freshness(
            "outreach_send",
            "Last outreach send",
            OutreachMessage.objects.aggregate(at=Max("sent_at"))["at"],
            timedelta(hours=48),
            "outreach_sender",
        ),
    ]
    for row in rows:
        row.active = row.active and on(row.feature)
    return rows


def health_summary() -> dict:
    """Everything the System health page and the dashboard banner need."""
    report = config_report()
    fresh = freshness_report(report)
    missing = [feature for feature in report if feature.state == MISSING]
    stale = [row for row in fresh if row.stale]
    return {
        "features": report,
        "freshness": fresh,
        "missing": missing,
        "missing_names": missing_names(report),
        "stale": stale,
        "problem": bool(missing or stale),
    }
