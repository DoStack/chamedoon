"""Sends people with the new Mini App (HomeSettings.miniapp_v2 or the allowlist) from the housing template pages to the
SPA. Everyone else stays on the templates, and post, mine and alerts stay there for all until P3 and P4.

The redirect keeps the URL fragment, where Telegram puts initData. The start param stays in initData, so `src` still
reaches the exchange, and the shell resolves it from there.
"""

from __future__ import annotations

from functools import wraps
from urllib.parse import urlencode

from django.shortcuts import redirect

from home import conf, deeplink

SHELL = "/app/v2/"
HOME_BOT_SESSION_KEY = "miniapp_home_bot"


def has_shell(user) -> bool:
    return conf.miniapp_v2_for(user.telegram_user_id)


def _telegram_params(request) -> list[tuple[str, str]]:
    return [(key, value) for key, value in request.GET.items() if key == "startapp" or key.startswith("tgWebApp")]


def shell_url(path: str, request, pairs: list[tuple[str, str]] | None = None) -> str:
    query = urlencode([*(pairs or []), *_telegram_params(request)])
    return f"{SHELL}{path}" + (f"?{query}" if query else "")


def shell_home(request, *args, **kwargs) -> str:
    return shell_url("", request)


def shell_list(request, *args, **kwargs) -> str:
    """The feed's filters as the shell spells them. A country alone has nothing to carry: the start param handles it."""
    pairs: list[tuple[str, str]] = []
    if city := request.GET.get("city", "").strip():
        pairs.append(("city", deeplink.slug(city)))
    for value in [*request.GET.getlist("type"), *request.GET.getlist("types")]:
        pairs.append(("types", value))
    for key in ("min", "max", "kind", "q"):
        if value := request.GET.get(key, "").strip():
            pairs.append((key, value))
    return shell_url("", request, pairs)


def shell_detail(request, pk: int, *args, **kwargs) -> str:
    return shell_url(f"listing/{pk}", request)


def to_shell(target):
    """Goes under miniapp_login_required, which sets request.chamedoon_user. Writes stay on the template."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.method in ("GET", "HEAD") and has_shell(request.chamedoon_user):
                return redirect(target(request, *args, **kwargs))
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
