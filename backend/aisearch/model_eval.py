"""Score a gateway model on `testdata/model_eval.json` with the production prompt and `spec.from_model`'s cleaning.

Run: OMNIROUTE_URL=... OMNIROUTE_KEY=... python model_eval.py model-a model-b [--runs 3]
A query is correct when kind, types, min, max (and term, gender, furnished, bills where the row names them) all match.
Needs Django importable for `aisearch.spec`; set DJANGO_SETTINGS_MODULE=config.settings and run from `backend/`.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

import django

django.setup()

from aisearch import llm  # noqa: E402
from aisearch.spec import from_model  # noqa: E402

ROWS = json.loads((Path(__file__).parent / "testdata" / "model_eval.json").read_text(encoding="utf-8"))
TODAY = date(2026, 11, 10)


def ask(model: str, text: str, country: str, timeout: float = 20) -> tuple[object, float]:
    body = {"model": model, "messages": llm._messages(text, TODAY, country), "temperature": 0, "max_tokens": llm.MAX_TOKENS}
    request = urllib.request.Request(
        os.environ["OMNIROUTE_URL"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + os.environ["OMNIROUTE_KEY"], "Content-Type": "application/json"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text_out = json.loads(response.read())["choices"][0]["message"]["content"] or ""
        return llm._first_object(text_out), time.monotonic() - started
    except Exception as exc:  # noqa: BLE001
        return {"_error": str(exc)[:80]}, time.monotonic() - started


def score(row: dict, data: object) -> list[str]:
    if isinstance(data, dict) and "_error" in data:
        return [data["_error"]]
    reply = from_model(data, country=row["country"], today=TODAY, text=row["q"])
    if reply is None:
        return ["not an object"]
    got = reply.spec.to_dict()
    blank = type(reply.spec)().to_dict()
    misses = []
    for key in ("kind", "types", "min", "max", "term", "gender", "furnished", "bills"):
        want = row["expect"].get(key, blank[key])
        if got[key] != want:
            misses.append(f"{key} {got[key]!r} != {want!r}")
    return misses


def main() -> None:
    args = sys.argv[1:]
    runs = int(args[args.index("--runs") + 1]) if "--runs" in args else 1
    models = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] != "--runs")]
    for model in models:
        right = total = 0
        times: list[float] = []
        wrong: list[str] = []
        for _ in range(runs):
            for row in ROWS:
                data, seconds = ask(model, row["q"], row["country"])
                times.append(seconds)
                misses = score(row, data)
                total += 1
                right += not misses
                if misses and len(wrong) < 6:
                    wrong.append(f"{row['q']} -> {'; '.join(misses)}")
        times.sort()
        p95 = times[min(len(times) - 1, int(len(times) * 0.95))]
        print(f"{model}: {right}/{total} p50={statistics.median(times):.1f}s p95={p95:.1f}s")
        for line in wrong:
            print("   ", line)


if __name__ == "__main__":
    main()
