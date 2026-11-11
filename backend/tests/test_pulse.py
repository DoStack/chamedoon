from types import SimpleNamespace
from unittest import mock

from django.test import RequestFactory, override_settings

from pulse import people, posthog, views


def _user(**kw):
    base = {"out_account": "", "out_member": False, "out_city": "", "picked": "", "verified_city": ""}
    return {**base, **kw}


def test_outreach_wins_over_first_touch():
    touch = {"launch": "home_other", "kind": "listing", "city": "Berlin"}
    assert people._source(_user(out_account="sina", out_member=True, out_city="Montreal"), touch) == ("Outreach", "Niloofar", "Montreal")


def test_outreach_without_member_falls_through_to_first_touch():
    touch = {"launch": "home_other", "kind": "listing", "city": "Berlin"}
    assert people._source(_user(out_account="sara", out_member=False), touch) == ("Post link", "listing", "Berlin")


def test_landing_direct_bot_unknown():
    assert people._source(_user(), {"launch": "home", "kind": "landing", "city": "Toronto"})[0] == "Landing"
    assert people._source(_user(), {"launch": "direct", "kind": "", "city": "Montreal"})[:1] == ("Direct",)
    assert people._source(_user(picked="Montreal"), None) == ("Bot only", "", "Montreal")
    assert people._source(_user(), None) == ("Unknown", "", "")


def test_account_label_never_shows_the_old_name():
    assert people.account_label("sina") == "Niloofar"
    assert people.account_label("monis") == "Monis"


def test_price_doubt_counts_distinct_amounts():
    text = "اتاق ماهی ۸۵۰ دلار، بیسمنت هر اتاق ۷۵۰ دلار، کل بیسمنت ۹۵۰ دلار. J3Y 3Z9"
    assert views.price_doubt(text, 1) == [750, 850, 950]
    assert views.price_doubt("room 850 CAD", 1) == []
    assert views.price_doubt(text, 3) == []


def test_funnel_levels_are_cumulative():
    with mock.patch.object(posthog, "hogql", return_value=[[0, 32], [1, 4], [2, 5], [3, 9], [4, 1], [5, 11]]):
        assert posthog.funnel(["a", "b", "c", "d", "e"], 30, []) == [30, 26, 21, 12, 11]


def test_hogql_without_key_returns_none(monkeypatch):
    monkeypatch.delenv("POSTHOG_PERSONAL_KEY", raising=False)
    assert posthog.hogql("select 1") is None


@override_settings(PULSE_ADMIN_ORIGIN="https://admin.example")
def test_non_staff_is_sent_to_the_admin_login():
    request = RequestFactory().get("/admin/pulse/posts/?o=ai")
    request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_staff=False)
    response = views.overview(request)
    assert response.status_code == 302
    assert response["Location"].startswith("https://admin.example/admin/login/?next=")
    assert "%2Fadmin%2Fpulse%2Fposts%2F" in response["Location"]


def test_dt_reads_posthog_text_as_toronto():
    dt = views._dt("2026-11-10 12:04:11.914000")
    assert dt.utcoffset().total_seconds() == -5 * 3600


def test_proxy_forwards_get_with_cookie_and_keeps_redirects(monkeypatch):
    from config import pulse_proxy

    seen = {}

    class Upstream:
        status = 302
        headers = {"Location": "https://chamedoon-pulse.bigde.li/admin/pulse/posts/", "Content-Type": "text/html"}

        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout):
        seen["url"], seen["cookie"] = req.full_url, req.headers.get("Cookie")
        return Upstream()

    monkeypatch.setattr(pulse_proxy._opener, "open", fake_open)
    request = RequestFactory().get("/admin/pulse/posts", {"o": "ai"}, HTTP_COOKIE="sessionid=abc")
    response = pulse_proxy.pulse_proxy(request, "posts")
    assert seen["url"] == "https://chamedoon-pulse.bigde.li/admin/pulse/posts?o=ai"
    assert seen["cookie"] == "sessionid=abc"
    assert response.status_code == 302
    assert response["Location"] == "/admin/pulse/posts/"


def test_proxy_refuses_writes():
    from config import pulse_proxy

    assert pulse_proxy.pulse_proxy(RequestFactory().post("/admin/pulse/"), "").status_code == 405


def test_signed_pass_roundtrip_and_rejections(monkeypatch):
    from config import pulse_proxy

    monkeypatch.setenv("PULSE_PROXY_SECRET", "s3cret")
    ts, sig = pulse_proxy.sign(7, now=1000)
    assert pulse_proxy.verify("7", ts, sig, now=1050)
    assert not pulse_proxy.verify("8", ts, sig, now=1050)  # another user id
    assert not pulse_proxy.verify("7", ts, sig, now=1000 + pulse_proxy.MAX_SKEW + 1)  # expired
    assert not pulse_proxy.verify("7", ts, "", now=1050)
    monkeypatch.setenv("PULSE_PROXY_SECRET", "")
    assert not pulse_proxy.verify("7", ts, sig, now=1050)  # no secret configured means no pass


def test_proxy_adds_signed_headers_for_staff_only(monkeypatch):
    from config import pulse_proxy

    monkeypatch.setenv("PULSE_PROXY_SECRET", "s3cret")
    seen = {}

    class Upstream:
        status = 200
        headers = {"Content-Type": "text/html"}

        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(pulse_proxy._opener, "open", lambda req, timeout: seen.update(h=dict(req.header_items())) or Upstream())
    staff = SimpleNamespace(pk=5, is_authenticated=True, is_active=True, is_staff=True)
    request = RequestFactory().get("/admin/pulse/")
    request.user = staff
    pulse_proxy.pulse_proxy(request, "")
    assert seen["h"]["X-pulse-user"] == "5"
    assert pulse_proxy.verify("5", seen["h"]["X-pulse-ts"], seen["h"]["X-pulse-sig"])
    request.user = SimpleNamespace(pk=6, is_authenticated=True, is_active=True, is_staff=False)
    pulse_proxy.pulse_proxy(request, "")
    assert "X-pulse-user" not in seen["h"]


def test_window_presets_and_custom_dates():
    from datetime import date

    from django.utils import timezone

    rf = RequestFactory()
    w = views.Window(rf.get("/admin/pulse/"))
    assert (w.key, w.label, w.custom) == ("7", "7d", False)
    assert views.Window(rf.get("/admin/pulse/", {"r": "90"})).label == "90d"
    assert views.Window(rf.get("/admin/pulse/"), default="all").label == "All"
    c = views.Window(rf.get("/admin/pulse/", {"from": "2026-11-01", "to": "2026-11-07"}))
    assert c.custom and c.label.startswith("Nov 1") and c.from_date == "2026-11-01"
    assert c.start.isoformat().startswith("2026-11-01T00:00:00-0")  # Toronto midnight
    assert (c.end - c.start).days in (7, 8)  # to-day included (DST may shift an hour)
    swapped = views.Window(rf.get("/admin/pulse/", {"from": "2026-11-07", "to": "2026-11-01"}))
    assert swapped.from_date == "2026-11-01"
    bad = views.Window(rf.get("/admin/pulse/", {"from": "nope"}))
    assert not bad.custom and bad.key == "7"
    future = views.Window(rf.get("/admin/pulse/", {"from": "2026-11-01", "to": "2999-01-01"}))
    assert future.to_date <= timezone.now().astimezone(views.TZ).date().isoformat()
    assert len(c.day_list(7)) >= 7 and c.day_list(7)[0] <= "2026-11-01"
    del date


def test_posthog_window_filter():
    from datetime import datetime, timezone as tz

    span = (datetime(2026, 11, 1, 4, 0, tzinfo=tz.utc), datetime(2026, 11, 8, 5, 0, tzinfo=tz.utc))
    assert posthog._when(span) == "timestamp >= toDateTime('2026-11-01 04:00:00', 'UTC') and timestamp < toDateTime('2026-11-08 05:00:00', 'UTC')"
    assert posthog._when(7) == "timestamp > now() - interval 7 day"
