from __future__ import annotations

from django.shortcuts import redirect

from users.models import User

SESSION_USER_KEY = "koolbar_user_id"
START_ROUTES = {
    "demand": "/app/demand/new/",
    "supply": "/app/supply/new/",
    "requests": "/app/requests/",
    "matches": "/app/matches/",
    "explore": "/app/explore/",
}


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
    return "/app/"


def miniapp_login_required(view):
    def wrapped(request, *args, **kwargs):
        user = get_miniapp_user(request)
        if user is None:
            startapp = request.GET.get("startapp", "")
            login_url = "/app/login/"
            if startapp:
                login_url = f"{login_url}?startapp={startapp}"
            return redirect(login_url)
        request.koolbar_user = user
        return view(request, *args, **kwargs)

    return wrapped
