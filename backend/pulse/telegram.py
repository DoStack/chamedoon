"""Member counts Telegram reports for our channels and groups, cached for an hour. Read-only bot calls."""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request

from django.core.cache import cache

logger = logging.getLogger(__name__)
TTL = 3600


def member_count(chat) -> int | None:
    token = os.environ.get("HOME_BOT_TOKEN", "")
    if not token or not chat:
        return None
    key = f"tgcount:{chat}"
    found = cache.get(key)
    if found is not None:
        return found if found >= 0 else None
    url = f"https://api.telegram.org/bot{token}/getChatMemberCount?" + urllib.parse.urlencode({"chat_id": chat})
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            count = json.load(response).get("result")
    except Exception:  # noqa: BLE001
        logger.info("pulse_tg_count_failed chat=%s", chat)
        count = None
    cache.set(key, count if isinstance(count, int) else -1, TTL if isinstance(count, int) else 600)
    return count if isinstance(count, int) else None


def bot_username() -> str:
    return os.environ.get("HOME_BOT_USERNAME", "chamedoon_home_bot").lstrip("@")


def mini_app_link(listing_id: int) -> str:
    return f"https://t.me/{bot_username()}?startapp=home_{int(listing_id)}"
