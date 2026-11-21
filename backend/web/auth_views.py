"""Browser sign-in: «ورود با تلگرام» through the housing bot, and email + 6-digit code.

    GET  /api/v1/auth/web/session/   who is signed in here, and which ways to sign in are on (sets the CSRF cookie)
    POST /api/v1/auth/web/start/     {code, match, poll_token, bot_url, expires_in}
    GET  /api/v1/auth/web/poll/      header X-Poll-Token -> pending | ok (+ session cookie) | expired | denied
    POST /api/v1/auth/web/logout/    needs X-CSRFToken
    POST /api/v1/auth/email/start/   {email}           -> {sent: true} whatever the address (no account probing)
    POST /api/v1/auth/email/verify/  {email, code}     -> {status: ok, user} + session cookie

Every body is JSON and every POST must be `Content-Type: application/json`, so another site cannot send one without a
CORS preflight, which this API never grants. The poll token travels only in a header, never in a link or a URL.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.http import HttpRequest, JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from home import conf
from users.identity import has_telegram
from users.models import User
from web import accounts, mail, session
from web.models import EmailLoginCode, WebLoginCode, WebLoginStatus
from web.tokens import hash_secret, new_code, new_email_code, new_match, new_poll_token, same

logger = logging.getLogger("web")

CODE_TTL = timedelta(minutes=5)
START_WINDOW = timedelta(minutes=10)
START_PER_IP = 10
POLL_PER_MINUTE = 90
EMAIL_TTL = timedelta(minutes=10)
EMAIL_TRIES = 5
EMAIL_PER_ADDRESS = 3  # per 10 minutes
EMAIL_PER_ADDRESS_DAY = 10
EMAIL_PER_IP = 10  # per 10 minutes
VERIFY_PER_IP = 30  # per 10 minutes
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[A-Za-z]{2,}$")


def _json(body: dict, status: int = 200, **headers) -> JsonResponse:
    response = JsonResponse(body, status=status, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "no-store"
    for name, value in headers.items():
        response[name.replace("_", "-")] = value
    return response


def _error(code: str, status: int, detail: str = "", **extra) -> JsonResponse:
    headers = {"Retry_After": str(extra.pop("retry_after"))} if "retry_after" in extra else {}
    return _json({"code": code, "detail": detail or code, **extra}, status, **headers)


def _body(request: HttpRequest) -> dict | None:
    if (request.content_type or "") != "application/json":
        return None
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _bump(key: str, limit: int, seconds: int) -> bool:
    """A per-process counter. True while under the limit."""
    cache.add(key, 0, seconds)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, seconds)
        count = 1
    return count <= limit


def user_payload(user: User) -> dict:
    return {
        "id": user.pk,
        "telegram_user_id": user.telegram_user_id if has_telegram(user) else None,
        "first_name": user.first_name,
        "telegram_username": user.telegram_username,
        "has_telegram": has_telegram(user),
        "email": user.email,
    }


def _prefs(user: User) -> dict:
    from home_api.views import PrefsSerializer, _prefs_for

    return PrefsSerializer(_prefs_for(user)).data


def _maintenance() -> JsonResponse | None:
    return _error("maintenance", 503) if conf.row().maintenance_on else None


@require_GET
def session_view(request: HttpRequest) -> JsonResponse:
    get_token(request)  # sets the csrftoken cookie the page sends back as X-CSRFToken
    user = session.current_user(request)
    body = {
        "web_login": conf.web_login_on(),
        "email_login": conf.email_login_on() and mail.ready(),
        "bot_username": conf.bot_username(),
        "user": user_payload(user) if user else None,
        "prefs": _prefs(user) if user else None,
    }
    response = _json(body)
    if user is None and request.COOKIES.get(session.COOKIE):
        session.clear_session(response)  # expired or deactivated: forget it
    return response


@csrf_exempt
@require_POST
def start_view(request: HttpRequest) -> JsonResponse:
    if (blocked := _maintenance()) is not None:
        return blocked
    if not conf.web_login_on():
        return _error("feature_off", 403)
    if _body(request) is None:
        return _error("bad_request", 400, "Send Content-Type: application/json.")
    if not conf.bot_username():
        return _error("auth_unavailable", 503)
    ip = session.client_ip(request)
    now = timezone.now()
    if WebLoginCode.objects.filter(ip=ip, created_at__gte=now - START_WINDOW).count() >= START_PER_IP:
        return _error("rate_limited", 429, retry_after=int(START_WINDOW.total_seconds()))
    code, poll_token, match = new_code(), new_poll_token(), new_match()
    current = session.current_user(request)
    WebLoginCode.objects.create(
        code_hash=hash_secret(code),
        poll_hash=hash_secret(poll_token),
        match=match,
        ip=ip,
        expires_at=now + CODE_TTL,
        link_user=current if current is not None and not has_telegram(current) else None,
    )
    return _json(
        {
            "code": code,
            "match": match,
            "poll_token": poll_token,
            "bot_url": f"https://t.me/{conf.bot_username()}?start=weblogin_{code}",
            "expires_in": int(CODE_TTL.total_seconds()),
        }
    )


@require_GET
def poll_view(request: HttpRequest) -> JsonResponse:
    token = (request.headers.get("X-Poll-Token") or "").strip()
    if not token or len(token) > 128:
        return _error("bad_request", 400, "Send the poll token in X-Poll-Token.")
    if not _bump(f"web:poll:{session.client_ip(request)}", POLL_PER_MINUTE, 60):
        return _error("rate_limited", 429, retry_after=60)
    now = timezone.now()
    with transaction.atomic():
        row = WebLoginCode.objects.select_for_update().filter(poll_hash=hash_secret(token)).first()
        if row is None:
            return _json({"status": "invalid"}, 404)
        if row.status == WebLoginStatus.DEAD:
            return _json({"status": "denied"}, 410)
        if row.status == WebLoginStatus.USED:
            return _json({"status": "expired"}, 410)
        if row.expires_at <= now:
            row.status = WebLoginStatus.DEAD
            row.save(update_fields=["status"])
            return _json({"status": "expired"}, 410)
        if row.status != WebLoginStatus.CONFIRMED or row.user is None:
            return _json({"status": "pending", "opened": row.status == WebLoginStatus.OPENED})
        user = row.user
        if not user.is_active:
            row.status = WebLoginStatus.DEAD
            row.save(update_fields=["status"])
            return _json({"status": "denied"}, 410)
        row.status = WebLoginStatus.USED
        row.used_at = now
        row.save(update_fields=["status", "used_at"])
        if row.link_user_id and row.link_user_id != user.pk:
            accounts.merge_into(row.link_user, user)
            user.refresh_from_db()
    response = _json({"status": "ok", "user": user_payload(user), "prefs": _prefs(user)})
    session.set_session(request, response, user)
    get_token(request)
    return response


@require_POST
def logout_view(request: HttpRequest) -> JsonResponse:
    # CsrfViewMiddleware checks this view (no csrf_exempt): a page of another site cannot sign anyone out.
    response = _json({"status": "ok"})
    session.clear_session(response)
    return response


@csrf_exempt
@require_POST
def email_start_view(request: HttpRequest) -> JsonResponse:
    if (blocked := _maintenance()) is not None:
        return blocked
    if not (conf.email_login_on() and mail.ready()):
        return _error("feature_off", 403)
    body = _body(request)
    if body is None:
        return _error("bad_request", 400, "Send Content-Type: application/json.")
    email = accounts.normal_email(str(body.get("email") or ""))
    if not EMAIL_RE.match(email):
        return _error("validation_failed", 400, fields={"email": ["invalid"]})
    ip = session.client_ip(request)
    now = timezone.now()
    recent = EmailLoginCode.objects.filter(created_at__gte=now - START_WINDOW)
    if (
        recent.filter(ip=ip).count() >= EMAIL_PER_IP
        or recent.filter(email=email).count() >= EMAIL_PER_ADDRESS
        or EmailLoginCode.objects.filter(email=email, created_at__gte=now - timedelta(days=1)).count() >= EMAIL_PER_ADDRESS_DAY
    ):
        return _error("rate_limited", 429, retry_after=int(START_WINDOW.total_seconds()))
    code = new_email_code()
    EmailLoginCode.objects.filter(email=email, used_at__isnull=True, dead=False).update(dead=True)
    EmailLoginCode.objects.create(email=email, code_hash=hash_secret(f"{email}:{code}"), ip=ip, expires_at=now + EMAIL_TTL)
    if not mail.send_code(email, code):
        return _error("auth_unavailable", 503, "The email could not be sent.")
    return _json({"sent": True, "expires_in": int(EMAIL_TTL.total_seconds())})


@csrf_exempt
@require_POST
def email_verify_view(request: HttpRequest) -> JsonResponse:
    if (blocked := _maintenance()) is not None:
        return blocked
    if not (conf.email_login_on() and mail.ready()):
        return _error("feature_off", 403)
    body = _body(request)
    if body is None:
        return _error("bad_request", 400, "Send Content-Type: application/json.")
    if not _bump(f"web:verify:{session.client_ip(request)}", VERIFY_PER_IP, int(START_WINDOW.total_seconds())):
        return _error("rate_limited", 429, retry_after=int(START_WINDOW.total_seconds()))
    email = accounts.normal_email(str(body.get("email") or ""))
    code = str(body.get("code") or "").strip()
    now = timezone.now()
    with transaction.atomic():
        row = (
            EmailLoginCode.objects.select_for_update()
            .filter(email=email, used_at__isnull=True, dead=False, expires_at__gt=now)
            .order_by("-created_at")
            .first()
        )
        if row is None:
            return _error("code_invalid", 400)
        EmailLoginCode.objects.filter(pk=row.pk).update(attempts=F("attempts") + 1)
        row.refresh_from_db()
        if row.attempts > EMAIL_TRIES:
            row.dead = True
            row.save(update_fields=["dead"])
            return _error("code_invalid", 400)
        if not (len(code) == 6 and code.isdigit() and same(f"{email}:{code}", row.code_hash)):
            if row.attempts >= EMAIL_TRIES:
                row.dead = True
                row.save(update_fields=["dead"])
            return _error("code_invalid", 400)
        row.used_at = now
        row.save(update_fields=["used_at"])
        current = session.current_user(request)
        owner = User.objects.filter(email=email).first()
        if current is not None and has_telegram(current) and (owner is None or owner.pk != current.pk):
            # A Telegram account adds this email: an email-only account of the same address joins it.
            if owner is not None and not has_telegram(owner):
                accounts.merge_into(owner, current)
            if owner is None or not has_telegram(owner):
                current.email, current.email_verified_at = email, now
                current.save(update_fields=["email", "email_verified_at", "updated_at"])
            user = current
        else:
            user = accounts.email_user(email)
        if not user.is_active:
            return _error("user_blocked", 403)
    response = _json({"status": "ok", "user": user_payload(user), "prefs": _prefs(user)})
    session.set_session(request, response, user)
    get_token(request)
    return response
