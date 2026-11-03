"""Every error of /api/v1/ has one stable `code`. The client switches on it; `detail` is for logs only."""

from __future__ import annotations

import logging

from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response

logger = logging.getLogger("home_api")

# code: (status, what it means)
CODES: dict[str, tuple[int, str]] = {
    "bad_request": (400, "The body or a parameter could not be read."),
    "validation_failed": (400, "A field is wrong. `fields` maps each field to its codes."),
    "auth_outside": (400, "No initData: the app was opened outside Telegram."),
    "auth_required": (401, "This call needs a token. Exchange initData first."),
    "auth_invalid": (401, "initData or the token failed its check."),
    "session_expired": (401, "The token is expired: exchange again. From the exchange itself: initData is over 24 hours old, reopen the app."),
    "user_blocked": (403, "This account is deactivated."),
    "not_member": (403, "Not a member of the channel. The error carries `channel` and `join_url`."),
    "miniapp_off": (403, "The new Mini App is off for this person: stay on the old pages."),
    "gate_banned": (403, "Banned from the channel, so joining cannot work."),
    "confirm_not_allowed": (403, "Only the owner can answer for a listing posted in the app."),
    "not_found": (404, "No such route or resource."),
    "listing_gone": (404, "The listing was removed, hidden or merged away."),
    "method_not_allowed": (405, "This method is not supported here."),
    "listing_closed": (409, "The listing was closed since it was cached."),
    "no_channel": (409, "This listing has no city or country channel to ask for, so its contact stays closed."),
    "reveal_cap": (429, "The cap of new listings in a rolling 24 hours is reached. A listing opened in that window still opens."),
    "rate_limited": (429, "Too many calls. Retry after the `Retry-After` header."),
    "server_error": (500, "Something failed on our side. Safe to retry."),
    "not_served_yet": (501, "In the contract, not live yet. See `x-served` on the operation."),
    "auth_unavailable": (503, "The server cannot check initData right now."),
    "maintenance": (503, "Maintenance is on. See /public/meta/."),
    "feed_failed": (503, "The fallback feed could not be built."),
}


class ApiError(exceptions.APIException):
    """Raise this from a view. `extra` keys join the error body, for example `channel`."""

    def __init__(self, code: str, detail: str = "", **extra) -> None:
        self.api_code = code
        self.status_code = CODES[code][0]
        self.extra = extra
        super().__init__(detail or CODES[code][1], code)


def _body(code: str, detail: object, **extra) -> dict:
    return {"code": code, "detail": str(detail), **extra}


def _field_errors(detail: object) -> dict[str, list[str]]:
    if isinstance(detail, dict):
        return {str(name): [str(getattr(item, "code", "invalid")) for item in (value if isinstance(value, list) else [value])] for name, value in detail.items()}
    return {"non_field_errors": [str(getattr(item, "code", "invalid")) for item in (detail if isinstance(detail, list) else [detail])]}


def api_exception_handler(exc: Exception, context: dict) -> Response:
    headers = {}
    extra = {}
    if isinstance(exc, ApiError):
        code, extra = exc.api_code, exc.extra
    elif isinstance(exc, exceptions.ValidationError):
        code, extra = "validation_failed", {"fields": _field_errors(exc.detail)}
    elif isinstance(exc, (exceptions.ParseError, exceptions.UnsupportedMediaType)):
        code = "bad_request"
    elif isinstance(exc, exceptions.NotAuthenticated):
        code = "auth_required"
    elif isinstance(exc, exceptions.AuthenticationFailed):
        got = exc.get_codes()
        code = got if got in {"auth_invalid", "session_expired", "user_blocked"} else "auth_invalid"
    elif isinstance(exc, exceptions.MethodNotAllowed):
        code = "method_not_allowed"
    elif isinstance(exc, exceptions.Throttled):
        code = "rate_limited"
        if exc.wait:
            headers["Retry-After"] = str(int(exc.wait))
    elif isinstance(exc, (Http404, exceptions.NotFound)):
        code = "not_found"
    else:
        request = context.get("request")
        logger.exception("api_server_error path=%s", getattr(request, "path", ""))
        return Response(_body("server_error", CODES["server_error"][1]), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    detail = getattr(exc, "detail", CODES[code][1])
    if not isinstance(detail, str):
        detail = CODES[code][1]
    logger.info("api_error code=%s path=%s", code, getattr(context.get("request"), "path", ""))
    return Response(_body(code, detail, **extra), status=CODES[code][0], headers=headers)
