"""The browser session: the same JWT the Mini App gets, kept in an HttpOnly cookie instead of the page's memory.

- Cookie `chm_web`: HttpOnly, SameSite=Lax, Secure whenever the request is HTTPS (always on the VPS), path /.
- A call authenticated by the cookie that changes state must carry Django's CSRF token (`X-CSRFToken` = the
  `csrftoken` cookie) and pass Django's Origin/Referer check. A Bearer header always wins over the cookie, so the
  Mini App never reads it.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import AuthenticationFailed

from users.models import User
from users.tokens import decode_access_token, issue_access_token

COOKIE = "chm_web"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def max_age() -> int:
    return int(getattr(settings, "JWT_ACCESS_TOKEN_HOURS", 24 * 30)) * 3600


def set_session(request: HttpRequest, response: HttpResponse, user: User) -> None:
    response.set_cookie(
        COOKIE,
        issue_access_token(user),
        max_age=max_age(),
        httponly=True,
        secure=request.is_secure() or not settings.DEBUG,
        samesite="Lax",
        path="/",
    )


def clear_session(response: HttpResponse) -> None:
    response.delete_cookie(COOKIE, path="/", samesite="Lax")


def client_ip(request: HttpRequest) -> str:
    """Behind the proxy (TRUST_PROXY): the address the proxy saw, the last X-Forwarded-For entry (ours sets it to
    $remote_addr; a proxy that appends puts the caller's own entries first). Else REMOTE_ADDR."""
    if getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
        forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[-1].strip()
        if forwarded:
            return forwarded[:64]
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def csrf_reason(request: HttpRequest) -> str | None:
    """Django's own CSRF check (token + Origin/Referer). None when it passes."""
    check = CSRFCheck(lambda _request: None)
    check.process_request(request)
    return check.process_view(request, None, (), {}) and "CSRF check failed"


def user_from_token(token: str) -> User | None:
    try:
        payload = decode_access_token(token)
    except Exception:  # noqa: BLE001 - an old or broken cookie is just "signed out"
        return None
    user = User.objects.filter(id=payload["user_id"], telegram_user_id=payload["telegram_user_id"]).first()
    if user is None or not user.is_active:
        return None
    cutoff = user.web_sessions_valid_after
    if cutoff is not None and int(payload.get("iat") or 0) <= int(cutoff.timestamp()):
        return None
    return user


def cookie_user(request) -> tuple[User, str] | None:
    """For the API's auth classes: the cookie's user when there is no Authorization header. Unsafe methods need CSRF."""
    raw = getattr(request, "_request", request)
    token = raw.COOKIES.get(COOKIE)
    if not token:
        return None
    user = user_from_token(token)
    if user is None:
        return None
    if raw.method not in SAFE_METHODS and csrf_reason(raw):
        raise AuthenticationFailed("CSRF check failed. Send X-CSRFToken.", code="auth_invalid")
    return (user, token)


def current_user(request: HttpRequest) -> User | None:
    token = request.COOKIES.get(COOKIE)
    return user_from_token(token) if token else None
