"""How the error codes get into openapi.yaml: each operation declares them, a hook writes the responses.

`manage.py courier_openapi` builds the file from these settings, separate from any other API's contract.
"""

from __future__ import annotations

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import extend_schema

from courier_api.errors import CODES

SETTINGS = {
    "TITLE": "Chamedoon courier API",
    "DESCRIPTION": (
        "The contract of the courier Mini App. Every error is JSON with a stable `code`; the client switches on `code`, "
        "never on `detail`. A non-JSON response (a platform error) counts as `server_error`. "
        "`x-error-codes` lists every code of an operation. `x-served` says when a code is not raised yet."
    ),
    "VERSION": "1",
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/v1/courier",
    "SCHEMA_PATH_PREFIX_TRIM": False,
    "SORT_OPERATIONS": True,
    "ENUM_NAME_OVERRIDES": {
        "RequestTypeEnum": "item_requests.models.RequestType",
        "RequestStatusEnum": "item_requests.models.RequestStatus",
        "IntroStatusEnum": "courier.models.IntroStatus",
        "MatchStatusEnum": "matching.models.MatchStatus",
        "TicketSubjectEnum": "support.models.TicketSubject",
    },
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "courier_api.schema.error_codes_hook",
    ],
}


class CourierJWTScheme(OpenApiAuthenticationExtension):
    target_class = "courier_api.auth.CourierJWTAuthentication"
    name = "telegramJwt"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}


BASE_CODES = ("server_error", "method_not_allowed", "maintenance")
ME_CODES = ("auth_required", "auth_invalid", "token_expired", "user_blocked", "flag_off", "version_mismatch", "rate_limited")
EXTRA_PROPERTIES: dict[str, dict[str, dict]] = {
    "validation_failed": {"fields": {"type": "object", "description": "Field name to its codes.", "additionalProperties": {"type": "array", "items": {"type": "string"}}}},
    "server_rejected": {"fields": {"type": "object", "description": "Field name to its messages, for logs.", "additionalProperties": {"type": "array", "items": {"type": "string"}}}},
    "own_request": {"request_id": {"type": "integer"}},
    "duplicate_request": {"request_id": {"type": "integer"}},
    "contact_not_accepted": {"intro_id": {"type": "integer", "nullable": True}},
    "request_inactive": {"route": {"$ref": "#/components/schemas/RouteKeys"}},
    "request_date_passed": {"route": {"$ref": "#/components/schemas/RouteKeys"}},
    "match_expired": {"route": {"$ref": "#/components/schemas/RouteKeys"}},
}


def doc(*codes: str, me: bool = True, served: str = "", **schema):
    """extend_schema plus the operation's error codes. `served` says which codes are declared but not raised yet."""
    declared = [*BASE_CODES, *codes]
    if me:
        declared += ME_CODES
    extensions = {"x-error-codes": sorted(set(declared))}
    if served:
        extensions["x-served"] = served
    return extend_schema(extensions=extensions, **schema)


def _error_schema(codes: list[str]) -> dict:
    properties: dict[str, dict] = {
        "code": {"type": "string", "enum": codes},
        "detail": {"type": "string", "description": "For logs only. Never shown or switched on."},
    }
    for code in codes:
        properties.update(EXTRA_PROPERTIES.get(code, {}))
    return {"type": "object", "required": ["code", "detail"], "properties": properties}


ERROR_COMPONENTS = {
    "RouteKeys": {
        "type": "object",
        "description": "Enough to offer the same route on a screen whose request is gone.",
        "required": ["side", "origin", "destination"],
        "properties": {"side": {"type": "string"}, "origin": {"type": "string"}, "destination": {"type": "string"}},
    },
}


def error_codes_hook(result, generator, request, public):
    """Replace each operation's error responses with one per status, listing the codes as an enum."""
    result.setdefault("components", {}).setdefault("schemas", {}).update(ERROR_COMPONENTS)
    for methods in result.get("paths", {}).values():
        for operation in methods.values():
            codes = operation.get("x-error-codes")
            if not codes:
                continue
            by_status: dict[int, list[str]] = {}
            for code in codes:
                by_status.setdefault(CODES[code][0], []).append(code)
            for status_code, grouped in sorted(by_status.items()):
                lines = "; ".join(f"`{code}`: {CODES[code][1]}" for code in grouped)
                operation["responses"][str(status_code)] = {
                    "description": lines,
                    "content": {"application/json": {"schema": _error_schema(grouped)}},
                }
    return result
