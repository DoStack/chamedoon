"""Telegram webhooks of both bots: read the status, set, re-secret and delete them.

Shared by the admin page (Admin → Telegram webhooks) and the `courier_webhook` command, so nobody
has to paste a bot token into a setWebhook URL by hand.

Two bots, two tokens, two secrets:
- courier @Chamed0on_bot: TELEGRAM_BOT_TOKEN, webhook /api/telegram/webhook/,
  secret Courier settings → webhook_secret, else TELEGRAM_WEBHOOK_SECRET.
- housing @chamedoon_home_bot: Home settings bot token (or HOME_BOT_TOKEN), webhook /api/home/bot/webhook/,
  secret Home settings → webhook_secret, else HOME_WEBHOOK_SECRET.

A token or a secret is never returned, rendered or logged. Every Telegram call has a short timeout
and its failure comes back as text, never as an exception.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from typing import Callable
from urllib.parse import urlsplit

from django.conf import settings

STATUS_TIMEOUT = 5
ACTION_TIMEOUT = 10
SECRET_LENGTH = 48
SECRET_ALPHABET = string.ascii_letters + string.digits + "_-"

COURIER = "courier"
HOUSING = "housing"


class WebhookError(RuntimeError):
    pass


@dataclass
class Step:
    method: str
    ok: bool
    description: str = ""

    def as_dict(self) -> dict:
        return {"method": self.method, "ok": self.ok, "description": self.description}


@dataclass
class Outcome:
    bot: str
    action: str
    ok: bool
    steps: list[Step] = field(default_factory=list)
    error: str = ""

    @property
    def summary(self) -> str:
        parts = [self.error] if self.error else []
        for step in self.steps:
            text = f"{step.method}: {'ok' if step.ok else 'failed'}"
            parts.append(f"{text} ({step.description})" if step.description else text)
        return "; ".join(parts)


# --- the two bots -------------------------------------------------------------------------


def _courier_token() -> str:
    return (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()


def _courier_secret() -> str:
    from courier.conf import webhook_secret

    return webhook_secret()


def _courier_call(method: str, payload: dict | None, timeout: float):
    from notifications import telegram

    body, error = telegram.call_telegram_api_detailed(method, payload or {}, timeout=timeout)
    if body is None:
        raise WebhookError(str((error or {}).get("description") or "Telegram refused the request"))
    return body.get("result")


def _courier_commands() -> list[dict]:
    from courier.flow import COMMANDS

    return list(COMMANDS)


def _courier_save_secret(value: str) -> None:
    from courier.models import CourierSettings

    row = CourierSettings.load()
    row.webhook_secret = value
    row.save(update_fields=["webhook_secret", "updated_at"])


def _housing_token() -> str:
    from home import conf

    return conf.bot_token()


def _housing_secret() -> str:
    from home import conf

    return conf.webhook_secret()


def _housing_call(method: str, payload: dict | None, timeout: float):
    from home import bot

    try:
        return bot.call(method, payload or {}, timeout=timeout)
    except bot.BotError as exc:
        raise WebhookError(exc.description) from exc


def _housing_commands() -> list[dict]:
    from home.botchat import COMMANDS

    return list(COMMANDS)


def _housing_save_secret(value: str) -> None:
    from home.models import HomeSettings

    row = HomeSettings.load()
    row.webhook_secret = value
    row.save(update_fields=["webhook_secret", "updated_at"])


@dataclass(frozen=True)
class Bot:
    key: str
    label: str
    handle: str
    path: str
    allowed_updates: tuple[str, ...]
    token_name: str
    secret_name: str
    token: Callable[[], str]
    secret: Callable[[], str]
    raw_call: Callable
    commands: Callable[[], list[dict]]
    save_secret: Callable[[str], None]

    def call(self, method: str, payload: dict | None = None, *, timeout: float = ACTION_TIMEOUT):
        """One Bot API call. Errors come back as WebhookError with the token scrubbed from the text."""
        token = self.token()
        if not token:
            raise WebhookError("token missing")
        try:
            return self.raw_call(method, payload, timeout)
        except WebhookError as exc:
            raise WebhookError(scrub(str(exc), token, self.secret())) from None
        except Exception as exc:  # noqa: BLE001 - the page shows the error, it never crashes
            raise WebhookError(scrub(f"{type(exc).__name__}: {exc}", token, self.secret())) from None


BOTS: dict[str, Bot] = {
    COURIER: Bot(
        key=COURIER,
        label="Courier bot",
        handle="@Chamed0on_bot",
        path="/api/telegram/webhook/",
        # api/telegram_webhook.py reads messages (/start, the route flow) and button taps; nothing else.
        allowed_updates=("message", "callback_query"),
        token_name="TELEGRAM_BOT_TOKEN",
        secret_name="TELEGRAM_WEBHOOK_SECRET",
        token=_courier_token,
        secret=_courier_secret,
        raw_call=_courier_call,
        commands=_courier_commands,
        save_secret=_courier_save_secret,
    ),
    HOUSING: Bot(
        key=HOUSING,
        label="Housing bot",
        handle="@chamedoon_home_bot",
        path="/api/home/bot/webhook/",
        allowed_updates=("message", "callback_query"),
        token_name="HOME_BOT_TOKEN",
        secret_name="HOME_WEBHOOK_SECRET",
        token=_housing_token,
        secret=_housing_secret,
        raw_call=_housing_call,
        commands=_housing_commands,
        save_secret=_housing_save_secret,
    ),
}


def get_bot(key: str) -> Bot:
    try:
        return BOTS[key]
    except KeyError:
        raise WebhookError(f"unknown bot {key!r}") from None


# --- helpers ------------------------------------------------------------------------------


def scrub(text: str, *values: str) -> str:
    """Remove any token or secret from text before it is shown or logged."""
    text = str(text or "")
    for value in values:
        if value and len(value) >= 4:
            text = text.replace(value, "***")
    return text


def new_secret() -> str:
    return "".join(secrets.choice(SECRET_ALPHABET) for _ in range(SECRET_LENGTH))


def site_base(request=None, url: str = "") -> str:
    """https://<host> of this site: an explicit url, SITE_URL if set, else the request's host. Always https."""
    base = (url or getattr(settings, "SITE_URL", "") or "").strip()
    if not base and request is not None:
        base = request.get_host()
    if not base:
        raise WebhookError("no site address: give --url https://<site>")
    parts = urlsplit(base if "://" in base else f"https://{base}")
    if not parts.netloc:
        raise WebhookError("no site address: give --url https://<site>")
    return f"https://{parts.netloc}"


def expected_url(bot: Bot, base: str) -> str:
    return f"{base.rstrip('/')}{bot.path}"


def display_url(url: str) -> str:
    """Host + path only: a query string could carry anything, so it is never shown."""
    if not url:
        return ""
    parts = urlsplit(url)
    return f"{parts.netloc}{parts.path}"


def same_url(current: str, expected: str) -> bool:
    if not current:
        return False
    a, b = urlsplit(current), urlsplit(expected)
    return (a.scheme, a.netloc.lower(), a.path, a.query) == (b.scheme, b.netloc.lower(), b.path, "")


def set_payload(bot: Bot, base: str, secret: str) -> dict:
    return {
        "url": expected_url(bot, base),
        "secret_token": secret,
        "allowed_updates": list(bot.allowed_updates),
        "drop_pending_updates": False,
    }


def masked(payload: dict) -> dict:
    shown = dict(payload)
    if "secret_token" in shown:
        shown["secret_token"] = "(set)" if shown["secret_token"] else "(MISSING)"
    return shown


# --- status -------------------------------------------------------------------------------


def bot_status(key: str, base: str) -> dict:
    """What the page shows for one bot. One getMe and one getWebhookInfo; errors are text, never raised."""
    bot = get_bot(key)
    expected = expected_url(bot, base)
    status = {
        "key": bot.key,
        "label": bot.label,
        "handle": bot.handle,
        "path": bot.path,
        "token_name": bot.token_name,
        "secret_name": bot.secret_name,
        "expected": display_url(expected),
        "allowed_expected": list(bot.allowed_updates),
        "token_missing": not bot.token(),
        "has_secret": bool(bot.secret()),
        "username": "",
        "me_error": "",
        "info_error": "",
        "info": None,
    }
    if status["token_missing"]:
        return status
    try:
        me = bot.call("getMe", timeout=STATUS_TIMEOUT)
        me = me if isinstance(me, dict) else {}
        status["username"] = str(me.get("username") or "")
    except WebhookError as exc:
        status["me_error"] = str(exc)
    try:
        info = bot.call("getWebhookInfo", timeout=STATUS_TIMEOUT) or {}
    except WebhookError as exc:
        status["info_error"] = str(exc)
        return status
    info = info if isinstance(info, dict) else {}
    current = str(info.get("url") or "")
    last_error_at = info.get("last_error_date")
    status["info"] = {
        "url": display_url(current),
        "set": bool(current),
        "matches": same_url(current, expected),
        "pending": int(info.get("pending_update_count") or 0),
        "last_error_at": datetime.fromtimestamp(int(last_error_at), tz=dt_timezone.utc) if last_error_at else None,
        "last_error_message": scrub(str(info.get("last_error_message") or ""), bot.token(), bot.secret()),
        "max_connections": info.get("max_connections"),
        "allowed_updates": list(info.get("allowed_updates") or []),
    }
    return status


# --- actions ------------------------------------------------------------------------------


def _step(bot: Bot, method: str, payload: dict | None) -> Step:
    try:
        result = bot.call(method, payload)
    except WebhookError as exc:
        return Step(method, False, str(exc))
    return Step(method, True, "" if result is True else scrub(str(result), bot.token(), bot.secret())[:200])


def set_webhook(key: str, base: str) -> Outcome:
    """setWebhook to this site with the bot's secret, then setMyCommands."""
    bot = get_bot(key)
    if not bot.token():
        return Outcome(key, "set", False, error=f"token missing: set {bot.token_name}")
    secret = bot.secret()
    if not secret:
        return Outcome(key, "set", False, error="no webhook secret yet: use Generate new secret")
    steps = [_step(bot, "setWebhook", set_payload(bot, base, secret))]
    commands = bot.commands()
    if steps[0].ok and commands:
        steps.append(_step(bot, "setMyCommands", {"commands": commands}))
    # The webhook is what matters; a failed setMyCommands shows in the steps but does not fail the action.
    return Outcome(key, "set", steps[0].ok, steps)


def generate_secret(key: str, base: str) -> Outcome:
    """Save a new random secret and set the webhook with it right away, so the two never drift."""
    bot = get_bot(key)
    if not bot.token():
        return Outcome(key, "generate", False, error=f"token missing: set {bot.token_name}")
    bot.save_secret(new_secret())
    outcome = set_webhook(key, base)
    outcome.action = "generate"
    if not outcome.ok and not outcome.error:
        outcome.error = "new secret saved, but setting the webhook failed: the bot refuses updates until Set webhook succeeds"
    return outcome


def delete_webhook(key: str) -> Outcome:
    bot = get_bot(key)
    if not bot.token():
        return Outcome(key, "delete", False, error=f"token missing: set {bot.token_name}")
    step = _step(bot, "deleteWebhook", {"drop_pending_updates": False})
    return Outcome(key, "delete", step.ok, [step])


def webhook_info(key: str) -> dict:
    """Raw getWebhookInfo for the command line (the response never holds the secret or the token)."""
    bot = get_bot(key)
    info = bot.call("getWebhookInfo", timeout=STATUS_TIMEOUT) or {}
    if isinstance(info, dict) and info.get("url"):
        info = {**info, "url": display_url(str(info["url"]))}
    return info
