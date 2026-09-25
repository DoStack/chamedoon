from __future__ import annotations

import re

from django.shortcuts import redirect

from users.models import User

SESSION_USER_KEY = "koolbar_user_id"
STARTAPP_CONSUMED_KEY = "miniapp_startapp_consumed"
START_ROUTES = {
    "demand": "/app/demand/new/",
    "supply": "/app/supply/new/",
    "requests": "/app/requests/",
    "matches": "/app/matches/",
    "explore": "/app/explore/",
    "support": "/app/support/",
}
_EXPLORE_PATH = re.compile(r"^/app/explore/(\d+)/?$")
_REQUEST_PATH = re.compile(r"^/app/requests/(\d+)/?$")
_SUPPORT_PATH = re.compile(r"^/app/support/(\d+)/?$")
_OUTREACH_PATH = re.compile(r"^/app/o/([A-Za-z0-9_-]{8,32})/?$")
_OUTREACH_TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,32}$")


def get_miniapp_user(request) -> User | None:
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        return None
    return User.objects.filter(pk=user_id, is_active=True).first()


def login_miniapp_user(request, user: User) -> None:
    request.session.cycle_key()
    request.session[SESSION_USER_KEY] = user.pk
    request.session.save()


def logout_miniapp_user(request) -> None:
    request.session.flush()


def consume_startapp(request, startapp: str | None) -> None:
    value = (startapp or "").strip()
    if not value:
        return
    request.session[STARTAPP_CONSUMED_KEY] = value


def startapp_already_consumed(request, startapp: str | None) -> bool:
    value = (startapp or "").strip()
    return bool(value) and request.session.get(STARTAPP_CONSUMED_KEY) == value


def startapp_path(startapp: str | None) -> str:
    value = (startapp or "").strip()
    if value in START_ROUTES:
        return START_ROUTES[value]
    if value.startswith("request_"):
        pk = value.removeprefix("request_")
        if pk.isdigit():
            return f"/app/requests/{int(pk)}/"
    if value.startswith("explore_"):
        pk = value.removeprefix("explore_")
        if pk.isdigit():
            return f"/app/explore/{int(pk)}/"
    if value.startswith("match_"):
        pk = value.removeprefix("match_")
        if pk.isdigit():
            return f"/app/matches/{int(pk)}/"
    if value.startswith("o_") and _OUTREACH_TOKEN.fullmatch(value.removeprefix("o_")):
        return f"/app/o/{value.removeprefix('o_')}/"
    if value.startswith("ticket_"):
        pk = value.removeprefix("ticket_")
        if pk.isdigit():
            return f"/app/support/{int(pk)}/"
    return "/app/"


def startapp_from_request(request) -> str:
    value = (
        request.GET.get("startapp")
        or request.GET.get("tgWebAppStartParam")
        or request.POST.get("startapp")
        or ""
    ).strip()
    if value:
        return value
    explore = _EXPLORE_PATH.match(request.path)
    if explore:
        return f"explore_{explore.group(1)}"
    own = _REQUEST_PATH.match(request.path)
    if own:
        return f"request_{own.group(1)}"
    outreach = _OUTREACH_PATH.match(request.path)
    if outreach:
        return f"o_{outreach.group(1)}"
    ticket = _SUPPORT_PATH.match(request.path)
    if ticket:
        return f"ticket_{ticket.group(1)}"
    if request.path.rstrip("/") == "/app/support":
        return "support"
    return ""


def miniapp_login_required(view):
    def wrapped(request, *args, **kwargs):
        user = get_miniapp_user(request)
        if user is None:
            startapp = startapp_from_request(request)
            login_url = "/app/login/"
            if startapp:
                login_url = f"{login_url}?startapp={startapp}"
            return redirect(login_url)
        request.koolbar_user = user
        return view(request, *args, **kwargs)

    return wrapped
