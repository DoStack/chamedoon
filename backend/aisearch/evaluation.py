"""The evaluation of F08 over `testdata/eval_queries.json`, with no real model: a query's `llm_reply` stands in for one.

Rule share is measured. Tokens are an upper bound (`llm.estimate_tokens`, with a full reply), until a real gateway reports
usage in `HomeSearchQuery.tokens`. `tests/test_aisearch.py` fails when this report and the committed one differ.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

from aisearch import llm, service
from aisearch.models import SearchCache
from aisearch.rules import normalize
from aisearch.spec import SearchSpec
from home.models import HomeArea

DATA = Path(__file__).resolve().parent / "testdata"
QUERIES = DATA / "eval_queries.json"
REPORT = DATA / "eval_report.json"
TODAY = date(2026, 11, 3)


def _miss(row: dict, answer: dict) -> list[str]:
    """Where an answer differs from what a row expects. An expected list must match; a key not named must stay empty."""
    misses = [f"status {answer['status']} != {row['status']}"] if answer["status"] != row["status"] else []
    spec = dict(answer["spec"])
    expect = dict(row["expect"])
    names = expect.pop("areas", [])
    canonical = dict(HomeArea.objects.filter(pk__in=spec["area_ids"]).values_list("pk", "canonical"))
    got_names = [canonical.get(pk) for pk in spec.pop("area_ids")]
    if sorted(got_names, key=str) != sorted(names):
        misses.append(f"areas {got_names} != {names}")
    blank = SearchSpec().to_dict()
    for key, value in spec.items():
        wanted = expect.get(key, blank[key])
        if (sorted(value) if isinstance(value, list) else value) != (sorted(wanted) if isinstance(wanted, list) else wanted):
            misses.append(f"{key} {value!r} != {wanted!r}")
    return misses


def run(telegram_user_id: int) -> tuple[dict, list[str]]:
    """Every query once, with the cache emptied so that each counts as new. Returns the report and the misses."""
    rows = json.loads(QUERIES.read_text(encoding="utf-8"))
    misses: list[str] = []
    calls = 0
    tokens = 0
    by_status: dict[str, int] = {}
    by_language = {"fa": 0, "en": 0, "mixed": 0}

    for row in rows:
        SearchCache.objects.all().delete()
        reply = row.get("llm_reply")
        asked: list[str] = []

        def fake_ask(text: str, *, today: date, country: str, limit: float = 6, model: str = "", _reply=reply, _asked=asked) -> llm.ModelResult:
            _asked.append(text)
            if _reply is None:
                return llm.ModelResult(ok=False, error="no_reply_in_eval", tokens=llm.estimate_tokens(text, today, country))
            return llm.ModelResult(ok=True, data=_reply, tokens=llm.estimate_tokens(text, today, country))

        with patch("aisearch.llm.ask", fake_ask), patch("aisearch.llm.available", return_value=True), patch("aisearch.service.timezone.localdate", return_value=TODAY):
            answer = service.parse(row["q"], country=row.get("country", "CA"), city=row["city"], telegram_user_id=telegram_user_id)
        if asked:
            calls += 1
            tokens += llm.estimate_tokens(normalize(row["q"]), TODAY, row.get("country", "CA"))
        by_status[answer["status"]] = by_status.get(answer["status"], 0) + 1
        latin = any(ch.isascii() and ch.isalpha() for ch in row["q"])
        persian = any("؀" <= ch <= "ۿ" for ch in row["q"])
        by_language["mixed" if latin and persian else "en" if latin else "fa"] += 1
        misses.extend(f"{row['q']}: {miss}" for miss in _miss(row, answer))

    total = len(rows)
    report = {
        "queries": total,
        "languages": by_language,
        "statuses": dict(sorted(by_status.items())),
        "model_calls": calls,
        "rule_share": round((total - calls) / total, 3),
        "tokens_per_model_call_max": round(tokens / calls) if calls else 0,
        "tokens_per_query_max": round(tokens / total, 1),
        "wrong": len(misses),
    }
    return report, misses
