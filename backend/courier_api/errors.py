"""Every error of /api/v1/courier/ has one stable `code`. The client switches on it; `detail` is for logs only."""

from __future__ import annotations

import logging

from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response

logger = logging.getLogger("courier_api")

# code: (status, what it means)
CODES: dict[str, tuple[int, str]] = {
    "bad_request": (400, "The body or a parameter could not be read."),
    "validation_failed": (400, "A field is wrong. `fields` maps each field to its codes."),
    "start_invalid": (400, "The start value of a link is malformed."),
    "same_city": (400, "Origin and destination are the same."),
    "city_invalid": (400, "The typed city cannot be used."),
    "route_required": (400, "This needs both origin and destination."),
    "date_invalid": (400, "The date cannot be read, or is past or too far for a package."),
    "date_past": (400, "The flight date has passed."),
    "flight_date_required": (400, "A trip needs a flight date."),
    "category_required": (400, "A trip needs at least one category."),
    "stops_one_country": (400, "All stops of a trip are in one country."),
    "server_rejected": (400, "The domain refused the request. `fields` maps each field to its messages, for logs."),
    "rating_invalid": (400, "No score from 1 to 5, or the comment is over 500 characters."),
    "ticket_invalid": (400, "The subject or message is empty or invalid."),
    "auth_required": (401, "This call needs a token, or initData is missing: opened outside Telegram."),
    "auth_invalid": (401, "initData or the token failed its check."),
    "session_expired": (401, "initData is over 24 hours old: reopen the app."),
    "token_expired": (401, "The token is expired: exchange initData again."),
    "user_blocked": (403, "This account is deactivated."),
    "flag_off": (403, "The new courier app is off for this user."),
    "contact_not_accepted": (403, "No contact before the other side accepts. `intro_id` says where to go."),
    "not_found": (404, "No such route or resource, or it is not this user's."),
    "alert_not_found": (404, "The alert was deleted, for example in the bot."),
    "method_not_allowed": (405, "This method is not supported here."),
    "own_request": (409, "This is the caller's own request. `request_id` opens it."),
    "duplicate_request": (409, "The same request is already active. `request_id` opens it."),
    "not_editable": (409, "The request is closed or expired, so it cannot change."),
    "alert_cap": (409, "Five alerts already."),
    "alert_exists": (409, "Another alert already has this route."),
    "already_rated": (409, "This match already has the caller's rating."),
    "match_not_open": (409, "The match is not in a state that allows this."),
    "ticket_closed": (409, "The ticket is closed."),
    "intro_closed": (409, "This intro was declined or expired, and does not reopen for this request."),
    "intro_expired": (409, "The intro expired before this answer."),
    "intro_unreachable": (409, "Imported post, author never joined. Nothing was sent. `published` is what they published."),
    "intro_no_public_contact": (409, "Imported post, author never joined, and they published no contact."),
    "intro_undelivered": (409, "The bot cannot message the target. Nothing is pending."),
    "request_inactive": (410, "The request was closed, cancelled or expired. `route` offers the same route."),
    "request_date_passed": (410, "The request's date passed. `route` offers the same route."),
    "match_expired": (410, "The match expired."),
    "version_mismatch": (426, "This bundle is older than `min_api_version` in /meta/: reload."),
    "rate_limited": (429, "Too many calls. Retry after the `Retry-After` header."),
    "intro_cap_self": (429, "This user sent the daily maximum of intros."),
    "intro_cap_target": (429, "The target got the daily maximum of intros."),
    "server_error": (500, "Something failed on our side. Safe to retry."),
    "auth_unavailable": (503, "The server cannot check initData right now."),
    "maintenance": (503, "Maintenance is on. See /meta/."),
    "intros_off": (503, "The intro switch is off. No contact is shown."),
    "matches_failed": (503, "The request is saved, but the other side's list could not be built."),
}

# Rows of the design's matrices that are not errors of this API. A 2xx field carries them, or the client makes them itself.
IN_RESPONSE_FIELDS = {
    "ok": "2xx.",
    "ok_empty": "2xx with an empty list.",
    "contact_no_username": "`contact.no_username` is true.",
    "intro_declined": "`status` of the intro is DECLINED.",
    "intro_send_retry": "`status` of the intro stays PENDING while the server retries.",
    "channel_off": "`channel_on` of the request is false.",
    "alerts_sending_off": "`sending_on` of the alert response is false.",
    "outreach_other_person": "`read_only` of the deep link is true.",
    "counts_failed": "`counts` of /me/ is null.",
    "request_expired": "`status` EXPIRED and `date_passed` true on the caller's own request.",
}
CLIENT_ONLY = {
    "pending": "Waiting on a call.",
    "network_error": "No response.",
    "feature_missing": "An old Telegram client.",
    "unknown_code": "A code this bundle does not know.",
    "copy_failed": "The clipboard refused.",
    "link_failed": "Telegram did not open the link.",
    "pref_save_failed": "PATCH /me/prefs/ failed; the screen carries on.",
    "locations_failed": "GET /locations/ failed.",
    "facets_failed": "GET /requests/facets/ failed.",
}


class ApiError(exceptions.APIException):
    """Raise this from a view. `extra` keys join the error body, for example `request_id`."""

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
    elif isinstance(exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)):
        code = "auth_required"
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
        logger.exception("courier_api_server_error path=%s", getattr(request, "path", ""))
        return Response(_body("server_error", CODES["server_error"][1]), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    detail = getattr(exc, "detail", CODES[code][1])
    if not isinstance(detail, str):
        detail = CODES[code][1]
    logger.info("courier_api_error code=%s path=%s", code, getattr(context.get("request"), "path", ""))
    return Response(_body(code, detail, **extra), status=CODES[code][0], headers=headers)
