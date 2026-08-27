from __future__ import annotations

import json
import urllib.error
import urllib.request

from config import API_BASE_URL, BOT_SERVICE_SECRET


class BotApiError(Exception):
    pass


def sync_user(
    *,
    telegram_user_id: int,
    first_name: str,
    last_name: str | None = None,
    telegram_username: str | None = None,
) -> dict:
    return _request(
        "POST",
        "/api/bot/sync-user/",
        {
            "telegram_user_id": telegram_user_id,
            "first_name": first_name,
            "last_name": last_name or "",
            "telegram_username": telegram_username or "",
        },
    )


def user_summary(telegram_user_id: int) -> dict:
    return _request("GET", f"/api/bot/users/{telegram_user_id}/summary/")


def fetch_categories() -> list:
    payload = _request("GET", "/api/categories/")
    return payload if isinstance(payload, list) else []


def fetch_locations() -> dict:
    payload = _request("GET", "/api/locations/")
    return payload if isinstance(payload, dict) else {"countries": []}


def create_request(telegram_user_id: int, payload: dict) -> dict:
    return _request("POST", "/api/bot/requests/", {"telegram_user_id": telegram_user_id, **payload})


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{API_BASE_URL}{path}",
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-Bot-Secret": BOT_SERVICE_SECRET,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        raise BotApiError(detail or f"Backend {exc.code} for {path}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise BotApiError(f"Backend unavailable for {path}") from exc
