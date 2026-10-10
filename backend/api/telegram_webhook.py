from __future__ import annotations

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from matching.acceptance import (
    accept_match,
    close_listing_after_reject,
    keep_listing_after_reject,
    reject_match,
)
from matching.models import Match
from notifications.telegram import answer_callback_query, edit_telegram_message
from users.models import User
from users.services import record_bot_start
from users.telegram import TelegramIdentity

_START_COMMAND = re.compile(r"^/start(?:@[A-Za-z0-9_]+)?(?:\s+(.*))?$", re.DOTALL)


def _secret_ok(request: Request) -> bool:
    expected = (getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "") or "").strip()
    if not expected:
        return True
    got = (
        request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        or request.META.get("HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN")
        or ""
    )
    return got == expected


def _user_from_callback(callback: dict) -> User | None:
    from_user = callback.get("from") or {}
    telegram_id = from_user.get("id")
    if telegram_id is None:
        return None
    return User.objects.filter(telegram_user_id=int(telegram_id), is_active=True).first()


def _parse_callback(data: str) -> tuple[str, str, int] | None:
    parts = (data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return None
    return parts[0], parts[1], int(parts[2])


def _handle_action(kind: str, action: str, item_id: int, user: User) -> str:
    match = (
        Match.objects.select_related(
            "initiated_by",
            "demand_request",
            "demand_request__user",
            "supply_request",
            "supply_request__user",
        )
        .filter(pk=item_id)
        .first()
    )
    if match is None:
        return "Match not found."
    if kind == "match" and action == "accept":
        accept_match(match, user)
        return "Match accepted."
    if kind == "match" and action == "reject":
        reject_match(match, user)
        return "Match rejected."
    if kind == "listing" and action == "close":
        close_listing_after_reject(match, user)
        return "Listing closed."
    if kind == "listing" and action == "keep":
        keep_listing_after_reject(match, user)
        return "Listing stays open."
    return "Unknown action."


def _start_payload(text: str) -> str | None:
    match = _START_COMMAND.match((text or "").strip())
    if not match:
        return None
    return (match.group(1) or "").strip()[:64]


def _identity_from_telegram(from_user: dict) -> TelegramIdentity | None:
    telegram_id = from_user.get("id")
    if telegram_id is None or from_user.get("is_bot"):
        return None
    first_name = str(from_user.get("first_name") or "").strip() or "Telegram"
    username = from_user.get("username")
    last_name = from_user.get("last_name")
    return TelegramIdentity(
        telegram_user_id=int(telegram_id),
        telegram_username=str(username).strip() if username else None,
        first_name=first_name[:64],
        last_name=str(last_name).strip()[:64] if last_name else None,
    )


def _record_bot_start(data: dict) -> None:
    message = data.get("message")
    if not isinstance(message, dict):
        return
    chat = message.get("chat") or {}
    if chat.get("type") not in {None, "", "private"}:
        return
    payload = _start_payload(str(message.get("text") or ""))
    if payload is None:
        return
    identity = _identity_from_telegram(message.get("from") or {})
    if identity is None:
        return
    record_bot_start(identity, payload=payload)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def telegram_webhook(request: Request) -> Response:
    if not _secret_ok(request):
        return Response({"ok": False}, status=status.HTTP_403_FORBIDDEN)
    payload = request.data if isinstance(request.data, dict) else {}
    callback = payload.get("callback_query")
    if not callback:
        _record_bot_start(payload)
        return Response({"ok": True})
    query_id = str(callback.get("id") or "")
    parsed = _parse_callback(str(callback.get("data") or ""))
    user = _user_from_callback(callback)
    text = "Could not handle this action."
    if parsed and user:
        try:
            text = _handle_action(*parsed, user)
        except ValidationError as exc:
            messages = getattr(exc, "messages", None) or [str(exc)]
            text = messages[0] if messages else "Could not handle this action."
    if query_id:
        answer_callback_query(query_id, text)
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    if chat_id and message_id:
        edit_telegram_message(chat_id, int(message_id), text, reply_markup={"inline_keyboard": []})
    return Response({"ok": True})
