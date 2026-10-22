"""Webhook of the housing bot (@chamedoon_home_bot). Separate from the courier bot's webhook.

Telegram sends the secret set with setWebhook in X-Telegram-Bot-Api-Secret-Token.
The secret lives in Home settings or HOME_WEBHOOK_SECRET, never in the repository.
Without a secret every update is refused.
"""

from __future__ import annotations

import hmac
import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from home import bot, conf

logger = logging.getLogger(__name__)

PATH = "/api/home/bot/webhook/"


def secret_ok(request: HttpRequest) -> bool:
    expected = conf.webhook_secret()
    if not expected:
        return False
    got = request.headers.get("X-Telegram-Bot-Api-Secret-Token") or ""
    return hmac.compare_digest(got.encode(), expected.encode())


@csrf_exempt
@require_POST
def home_bot_webhook(request: HttpRequest) -> JsonResponse:
    if not secret_ok(request):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)
    if not conf.home_enabled():
        return JsonResponse({"ok": True, "skipped": "home disabled"})
    try:
        update = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": True, "skipped": "bad json"})
    if not isinstance(update, dict):
        return JsonResponse({"ok": True, "skipped": "bad json"})
    from home.botchat import handle_update

    try:
        handle_update(update)
    except bot.BotError as exc:
        # Telegram must get a 200, or it keeps resending the same update.
        logger.warning("Home bot reply failed: %s", exc.description)
    except Exception:  # noqa: BLE001 - one bad update must not block the queue
        logger.exception("Home bot update %s failed", update.get("update_id"))
    return JsonResponse({"ok": True})
