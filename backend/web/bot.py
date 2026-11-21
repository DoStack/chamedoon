"""The housing bot's part of «ورود با تلگرام»: `/start weblogin_<code>` and the number choice.

The person sees four two-digit numbers; one of them is the number on the browser screen. Only that one confirms the
code for this Telegram account. A wrong number, «من نبودم», a second Telegram account opening the same link, or the
5-minute limit kills the code. The bot replies only to the person who opened the link.
"""

from __future__ import annotations

import logging
import random
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from home import bot
from users.services import is_blocked, upsert_telegram_user
from users.telegram import TelegramIdentity
from web.models import WebLoginCode, WebLoginStatus
from web.tokens import hash_secret

logger = logging.getLogger("web")

PREFIX = "weblogin_"
CALLBACK = "wl:"
USER_WINDOW = timedelta(minutes=10)
USER_LIMIT = 5

ASK = "🔐 ورود به چمدون در مرورگر؟\n\nعددی که روی صفحه می‌بینید را انتخاب کنید.\n\nاگر خودتان در مرورگر «ورود با تلگرام» را نزده‌اید، هیچ عددی را نزنید و «من نبودم» را بزنید."
DONE = "✅ ورود تأیید شد. به مرورگر برگردید؛ صفحه خودش باز می‌شود."
WRONG = "❌ این عدد درست نبود. برای امنیت، این ورود لغو شد. در مرورگر دوباره «ورود با تلگرام» را بزنید."
NOT_ME = "🛡 این ورود لغو شد. کسی با این پیوند وارد حساب شما نمی‌شود."
EXPIRED = "⌛️ این پیوند ورود منقضی شده یا قبلاً استفاده شده است. در مرورگر دوباره «ورود با تلگرام» را بزنید."
TOO_MANY = "⏳ تلاش‌های ورود زیاد بود. چند دقیقه بعد دوباره امتحان کنید."
BLOCKED = "این حساب غیرفعال است."


def _identity(user: dict) -> TelegramIdentity:
    username = user.get("username")
    last = user.get("last_name")
    return TelegramIdentity(
        telegram_user_id=int(user["id"]),
        telegram_username=str(username).strip() if username else None,
        first_name=(str(user.get("first_name") or "").strip() or "کاربر")[:64],
        last_name=str(last).strip()[:64] if last else None,
    )


def _send(chat_id: int, text: str, keyboard: list | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    bot.call("sendMessage", payload)


def _edit(chat_id: int, message_id, text: str) -> None:
    try:
        bot.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text})
    except bot.BotError as exc:
        logger.info("weblogin edit failed: %s", exc.description)


def _answer(callback_id: str, text: str = "") -> None:
    try:
        bot.call("answerCallbackQuery", {"callback_query_id": callback_id, **({"text": text} if text else {})})
    except bot.BotError:
        pass


def choices(match: int) -> list[int]:
    numbers = {match}
    while len(numbers) < 4:
        numbers.add(10 + secrets.randbelow(90))
    out = list(numbers)
    random.SystemRandom().shuffle(out)
    return out


def open_link(code: str, chat_id: int, user: dict) -> None:
    now = timezone.now()
    telegram_id = int(user["id"])
    if WebLoginCode.objects.filter(telegram_user_id=telegram_id, created_at__gte=now - USER_WINDOW).count() >= USER_LIMIT:
        _send(chat_id, TOO_MANY)
        return
    with transaction.atomic():
        row = WebLoginCode.objects.select_for_update().filter(code_hash=hash_secret(code)).first()
        if row is None:
            _send(chat_id, EXPIRED)
            return
        live = row.expires_at > now and row.status in {WebLoginStatus.PENDING, WebLoginStatus.OPENED}
        if live and row.telegram_user_id not in (None, telegram_id):
            live = False  # a second account opened the same link: nobody gets in with it
        if not live:
            if row.status not in {WebLoginStatus.USED, WebLoginStatus.DEAD}:
                row.status = WebLoginStatus.DEAD
                row.save(update_fields=["status"])
            _send(chat_id, EXPIRED)
            return
        row.telegram_user_id = telegram_id
        row.status = WebLoginStatus.OPENED
        row.save(update_fields=["telegram_user_id", "status"])
    keyboard = [[{"text": str(number), "callback_data": f"{CALLBACK}{row.pk}:{number}"} for number in choices(row.match)]]
    keyboard.append([{"text": "من نبودم", "callback_data": f"{CALLBACK}{row.pk}:x"}])
    _send(chat_id, ASK, keyboard)


def choose(callback: dict) -> None:
    data = callback.get("data") or ""
    sender = callback.get("from") or {}
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id") or sender.get("id")
    try:
        _prefix, pk, picked = data.split(":", 2)
        pk = int(pk)
    except ValueError:
        _answer(callback.get("id", ""))
        return
    now = timezone.now()
    reply = EXPIRED
    with transaction.atomic():
        row = WebLoginCode.objects.select_for_update().filter(pk=pk).first()
        if row is None or row.telegram_user_id != sender.get("id"):
            row = None  # not this person's code: change nothing
        elif row.status != WebLoginStatus.OPENED or row.expires_at <= now:
            if row.status in {WebLoginStatus.PENDING, WebLoginStatus.OPENED}:
                row.status = WebLoginStatus.DEAD
                row.save(update_fields=["status"])
        elif picked == "x":
            row.status = WebLoginStatus.DEAD
            row.save(update_fields=["status"])
            reply = NOT_ME
        elif not picked.isdigit() or int(picked) != row.match:
            row.status = WebLoginStatus.DEAD
            row.save(update_fields=["status"])
            reply = WRONG
        else:
            identity = _identity(sender)
            if is_blocked(identity):
                row.status = WebLoginStatus.DEAD
                row.save(update_fields=["status"])
                reply = BLOCKED
            else:
                row.user = upsert_telegram_user(identity)  # exactly as the Mini App exchange does
                row.status = WebLoginStatus.CONFIRMED
                row.confirmed_at = now
                row.save(update_fields=["user", "status", "confirmed_at"])
                reply = DONE
    _answer(callback.get("id", ""))
    if chat_id and message.get("message_id"):
        _edit(chat_id, message["message_id"], reply)


def handle(update: dict) -> bool:
    """True when the update was a web sign-in step (then nothing else in the bot sees it)."""
    callback = update.get("callback_query")
    if callback:
        if (callback.get("data") or "").startswith(CALLBACK):
            choose(callback)
            return True
        return False
    message = update.get("message") or {}
    text = (message.get("text") or "").strip()
    if not text.startswith("/start ") or (message.get("chat") or {}).get("type") != "private":
        return False
    payload = text.split(" ", 1)[1].strip()
    if not payload.startswith(PREFIX):
        return False
    user = message.get("from") or {}
    if not user.get("id"):
        return True
    open_link(payload[len(PREFIX):], int(message["chat"]["id"]), user)
    return True
