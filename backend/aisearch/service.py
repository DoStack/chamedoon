"""F08's one call: text in, an answer the client can apply. Rules, then cache, caps and the model, and regex on any failure.

Statuses, each one screen state: parsed #4, simple #8, capped #9, unknown_area #6, no_city #7, not_understood #12,
other_city #13. #10 and #5 are the client's, since it alone holds the listings. Only a call that reaches the model counts
toward a cap, so a person at the cap still gets every query the rules answer.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import timedelta

from django.db import DatabaseError, transaction
from django.db.models import F
from django.utils import timezone

from aisearch import llm
from aisearch.models import SearchCache, SearchDailyCounter, SearchRecentsMark
from aisearch.rules import RULES_VERSION, Metro, RuleResult, extract, normalize
from aisearch.spec import MAX_NAMES, SearchSpec, from_model
from home import conf
from home.areas import _metro_key
from home.feed import metro_channel
from home.models import HomeSearchQuery, SearchQueryStatus

logger = logging.getLogger("aisearch")

GLOBAL_SCOPE = "global"
PURGE_SCOPE = "purge"
RULES_TTL = timedelta(days=1)
MODEL_TTL = timedelta(days=30)
# A failed call is not asked again straight away: free tiers fail in runs, and each try costs a cap slot.
FAILED_TTL = timedelta(hours=1)
RECENT = 5
RECENT_STATUSES = ("parsed", "simple", "unknown_area", "other_city")
# The log's status, shared with the bot. The screen's own is kept in `parsed`.
LOG_STATUS = {"parsed": SearchQueryStatus.OK, "other_city": SearchQueryStatus.OK, "no_city": SearchQueryStatus.LOW, "unknown_area": SearchQueryStatus.LOW, "not_understood": SearchQueryStatus.LOW, "simple": SearchQueryStatus.FALLBACK, "capped": SearchQueryStatus.CAPPED}


@dataclass
class Outcome:
    answer: dict
    tokens: int = 0
    ttl: timedelta | None = None


def _safely(action, default=None):
    """The cache, caps and log are for saving tokens. Their failure must not fail a search that needs none of them."""
    try:
        with transaction.atomic():
            return action()
    except DatabaseError:
        logger.exception("aisearch_db_failed")
        return default


def _status(result: RuleResult, spec: SearchSpec) -> str:
    if result.other_city:
        return "other_city"
    if result.unknown_area:
        return "unknown_area"
    if result.ignored or result.leftover:
        return "simple" if not spec.is_empty() else "not_understood"
    return "parsed" if not spec.is_empty() else "not_understood"


def _answer(result: RuleResult, source: str, status: str | None = None) -> dict:
    spec = result.spec
    return {
        "status": status or _status(result, spec),
        "source": source,
        "spec": spec.to_dict(),
        "area_labels": result.labels,
        "ignored": list(dict.fromkeys([*result.ignored, *result.leftover]))[:MAX_NAMES],
        "unknown_area": result.unknown_area,
        "other_city": result.other_city,
        "cached": False,
    }


def _take_cap(scope: str, limit: int, day) -> bool:
    row, _created = SearchDailyCounter.objects.get_or_create(scope=scope, day=day)
    return bool(SearchDailyCounter.objects.filter(pk=row.pk, count__lt=limit).update(count=F("count") + 1))


def _within_caps(telegram_user_id: int, settings_row) -> bool:
    day = timezone.localdate()
    user_scope = f"user:{telegram_user_id}"
    if not _take_cap(user_scope, settings_row.ai_search_user_cap, day):
        return False
    if _take_cap(GLOBAL_SCOPE, settings_row.ai_search_global_cap, day):
        return True
    SearchDailyCounter.objects.filter(scope=user_scope, day=day).update(count=F("count") - 1)
    return False


def _merge(result: RuleResult, reply, metro: Metro) -> None:
    """Fill in what the rules left empty. The rules win where both spoke, and a name the metro does not know stays unknown."""
    spec = result.spec
    model = reply.spec
    if model.kind == "wanted":
        spec.kind = "wanted"
    spec.types = spec.types or model.types
    spec.min = spec.min or model.min
    spec.max = spec.max or model.max
    spec.size_min = spec.size_min or model.size_min
    spec.size_max = spec.size_max or model.size_max
    spec.term = spec.term or model.term
    spec.gender = spec.gender or model.gender
    spec.move = spec.move or model.move
    spec.by = spec.by or model.by
    for name in ("furnished", "bills", "owner", "warm", "reg", "nowbs"):
        setattr(spec, name, getattr(spec, name) or getattr(model, name))
    for name in reply.areas:
        hit = metro.area_here(normalize(name))
        if hit:
            area, label = hit
            if area.pk not in spec.area_ids:
                spec.area_ids.append(area.pk)
                result.labels.append({"id": area.pk, "label": label, "name": area.fa})
        elif not result.unknown_area:
            result.unknown_area = {"text": name, "suggestions": metro.suggestions(name)}
    result.ignored = list(dict.fromkeys([*result.ignored, *reply.ignored]))
    result.leftover = []


def _resolve(normalized: str, metro: Metro, telegram_user_id: int, *, rules_only: bool) -> Outcome:
    today = timezone.localdate()
    result = extract(normalized, metro, today)
    if not metro.here:
        return Outcome(_answer(result, "rules", "other_city" if result.other_city else "no_city"))
    if not result.leftover:
        return Outcome(_answer(result, "rules"), ttl=RULES_TTL)
    if rules_only:
        return Outcome(_answer(result, "regex"))
    settings_row = _safely(conf.row)
    if not settings_row or settings_row.ai_search_emergency_stop or not llm.available():
        return Outcome(_answer(result, "regex"), ttl=FAILED_TTL)
    if not _safely(lambda: _within_caps(telegram_user_id, settings_row), default=False):
        # F08#9 has no chips and no filters. Never cached: tomorrow the same text gets its call.
        return Outcome(_answer(RuleResult(spec=SearchSpec()), "regex", "capped"))
    reply = llm.ask(normalized, today=today, country=metro.country, limit=settings_row.ai_search_timeout_seconds, model=settings_row.search_model)
    parsed = from_model(reply.data, country=metro.country, today=today, text=normalized) if reply.ok else None
    if parsed is None:
        return Outcome(_answer(result, "regex"), tokens=reply.tokens, ttl=FAILED_TTL)
    _merge(result, parsed, metro)
    dated = result.spec.move or result.spec.by
    return Outcome(_answer(result, "llm"), tokens=reply.tokens, ttl=RULES_TTL if dated else MODEL_TTL)


def cache_key(normalized: str, metro_key: str) -> str:
    return hashlib.sha256(f"{RULES_VERSION}|{metro_key}|{normalized}".encode()).hexdigest()


def _cached(key: str) -> dict | None:
    row = SearchCache.objects.filter(key=key, expires_at__gt=timezone.now()).first()
    return row.payload if row else None


def _store(key: str, answer: dict, ttl: timedelta) -> None:
    SearchCache.objects.update_or_create(key=key, defaults={"payload": answer, "expires_at": timezone.now() + ttl})


def _purge_once_a_day() -> None:
    _row, created = SearchDailyCounter.objects.get_or_create(scope=PURGE_SCOPE, day=timezone.localdate())
    if not created:
        return
    now = timezone.now()
    SearchCache.objects.filter(expires_at__lte=now).delete()
    SearchDailyCounter.objects.filter(day__lt=timezone.localdate() - timedelta(days=2)).delete()


def parse(text: str, *, country: str, city: str, telegram_user_id: int, rules_only: bool = False) -> dict:
    normalized = normalize(text)
    country = (country or "").upper()
    here = metro_channel(country, city) if country and city else None
    here = here if here is not None and here.enabled else None
    metro_key = f"{here.country}:{_metro_key(here.city)}" if here else ""
    key = cache_key(normalized, metro_key)
    if rules_only:
        return _resolve(normalized, Metro.load(here, country), telegram_user_id, rules_only=True).answer

    tokens = 0
    started = time.monotonic()
    hit = _safely(lambda: _cached(key)) if here else None
    if hit:
        answer = {**hit, "cached": True}
    else:
        outcome = _resolve(normalized, Metro.load(here, country), telegram_user_id, rules_only=False)
        answer, tokens = outcome.answer, outcome.tokens
        if outcome.ttl and here:
            _safely(lambda: _store(key, answer, outcome.ttl))
        _safely(_purge_once_a_day)
    _safely(
        lambda: HomeSearchQuery.objects.create(
            telegram_user_id=telegram_user_id,
            raw_text=text,
            country=country,
            city=here.city if here else "",
            parser=answer["source"],
            parsed={**answer, "tokens": tokens},
            latency_ms=round((time.monotonic() - started) * 1000),
            status=LOG_STATUS[answer["status"]],
        )
    )
    logger.info("aisearch_parse status=%s source=%s cached=%s tokens=%s", answer["status"], answer["source"], answer["cached"], tokens)
    return answer


def recent(telegram_user_id: int) -> list[dict]:
    """Latest first, one row per text, only what found something (F08#1). The status key leaves out the bot's rows."""
    rows = HomeSearchQuery.objects.filter(telegram_user_id=telegram_user_id, parsed__status__in=RECENT_STATUSES)
    mark = _safely(lambda: SearchRecentsMark.objects.filter(telegram_user_id=telegram_user_id).first())
    if mark and mark.cleared_at:
        rows = rows.filter(created_at__gt=mark.cleared_at)
    seen: dict[str, dict] = {}
    for row in rows[: RECENT * 8]:
        key = normalize(row.raw_text)
        if key not in seen and len(seen) < RECENT:
            seen[key] = {"text": row.raw_text, "spec": row.parsed.get("spec") or {}, "area_labels": row.parsed.get("area_labels") or []}
    return list(seen.values())


def clear_recent(telegram_user_id: int):
    """F08#11. The stamp names this clear, for its undo."""
    now = timezone.now()
    mark, _created = SearchRecentsMark.objects.get_or_create(telegram_user_id=telegram_user_id)
    SearchRecentsMark.objects.filter(pk=mark.pk).update(previous=mark.cleared_at, cleared_at=now)
    return now


def restore_recent(telegram_user_id: int, cleared_at) -> None:
    """Steps back only if this clear is the latest, so a repeat does nothing."""
    SearchRecentsMark.objects.filter(telegram_user_id=telegram_user_id, cleared_at=cleared_at).update(cleared_at=F("previous"), previous=None)
