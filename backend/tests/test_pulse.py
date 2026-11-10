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
