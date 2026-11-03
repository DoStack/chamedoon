"""How the error codes get into openapi.yaml: each operation declares them, a hook writes the responses."""

from __future__ import annotations

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import extend_schema

from home_api.errors import CODES


class TelegramJWTScheme(OpenApiAuthenticationExtension):
    target_class = "api.authentication.TelegramJWTAuthentication"
    name = "telegramJwt"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}


BASE_CODES = ("server_error", "method_not_allowed")
AUTH_CODES = ("auth_required", "auth_invalid", "session_expired", "user_blocked")


def doc(*codes: str, me: bool = False, maintenance: bool = True, served: str = "", **schema):
    """extend_schema plus the operation's error codes. `served` names the PR that makes it live."""
    declared = [*BASE_CODES, *codes]
    if me:
        declared += AUTH_CODES
    if maintenance:
        declared.append("maintenance")
    extensions = {"x-error-codes": sorted(set(declared))}
    if served:
        extensions["x-served"] = served
    return extend_schema(extensions=extensions, **schema)


def _error_schema(codes: list[str]) -> dict:
    properties = {
        "code": {"type": "string", "enum": codes},
        "detail": {"type": "string", "description": "For logs only. Never shown or switched on."},
    }
    if "validation_failed" in codes:
        properties["fields"] = {
            "type": "object",
            "description": "Field name to its codes, for `validation_failed`.",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
        }
    if "not_member" in codes:
        properties["join_url"] = {"type": "string", "description": "For `not_member`."}
        properties["channel"] = {"$ref": "#/components/schemas/GateChannel"}
    return {"type": "object", "required": ["code", "detail"], "properties": properties}


def error_codes_hook(result, generator, request, public):
    """Replace each operation's error responses with one per status, listing the codes as an enum."""
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
