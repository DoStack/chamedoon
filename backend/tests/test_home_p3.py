"""P3: alerts (F15, F16), inbox (F17), account (F21), and what the send step does with a person's settings (F23)."""

from __future__ import annotations

from datetime import datetime, time as clock, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.utils import timezone

from home import inbox
from home.alerts import MAX_ALERTS_PER_USER, delivery_due, is_quiet, notify_pending
from home.freshness import expire_stale, mark_taken, run_freshness
from home.models import (
    AlertSendStatus,
    HomeAlert,
    HomeAlertSent,
    HomeArea,
    HomeChatState,
    HomeNotification,
    HomeSaved,
    HomeSettings,
    HomeUserPrefs,
    NotificationKind,
)
from home.search import fits
from tests.contract_shape import named
from tests.helpers import bearer_auth, make_user
from tests.test_home_api import ApiCase

TORONTO_NOON = datetime(2026, 11, 3, 17, 0, tzinfo=dt_timezone.utc)
TORONTO_EARLY = datetime(2026, 11, 3, 12, 0, tzinfo=dt_timezone.utc)


class P3Case(ApiCase):
    def setUp(self) -> None:
        super().setUp()
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(alerts_api_on=True, account_api_on=True)
        self.auth = bearer_auth(self.user)

    def get(self, url: str, **query):
        return self.client.get(url, query, **self.auth)

    def post(self, url: str, body: dict | None = None):
        return self.client.post(url, body or {}, content_type="application/json", **self.auth)

    def patch(self, url: str, body: dict):
        return self.client.patch(url, body, content_type="application/json", **self.auth)

    def create(self, **filters):
        return self.post("/api/v1/me/alerts/", {"country": "CA", "city": "Toronto", "filters": filters})

    def make_alert(self, **filters) -> int:
        response = self.create(**filters)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["alert"]["id"]

    def fill_cap(self) -> None:
        for number in range(MAX_ALERTS_PER_USER):
            self.make_alert(max=1000 + number)


class FeatureFlagTests(P3Case):
    def test_every_p3_route_is_closed_while_its_flag_is_off(self) -> None:
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(alerts_api_on=False, account_api_on=False)
        for url, pattern in (
            ("/api/v1/me/alerts/", "/api/v1/me/alerts/"),
            ("/api/v1/me/alerts/1/", "/api/v1/me/alerts/{id}/"),
            ("/api/v1/me/alerts/1/listings/", "/api/v1/me/alerts/{id}/listings/"),
            ("/api/v1/me/account/", "/api/v1/me/account/"),
            ("/api/v1/me/notifications/", "/api/v1/me/notifications/"),
        ):
            with self.subTest(url=url):
                self.assert_error(self.get(url), "feature_off", pattern)

    def test_a_token_is_needed(self) -> None:
        self.assert_error(self.client.get("/api/v1/me/alerts/"), "auth_required", "/api/v1/me/alerts/")

    def test_meta_says_which_are_open(self) -> None:
        body = self.client.get("/api/v1/public/meta/").json()
        self.assertEqual(body["features"], {"alerts": True, "account": True, "posting": False, "smart_posting": False})
        self.assertEqual(named(body, "Meta"), [])


class CreateAlertTests(P3Case):
    def test_saved_in_the_contract_shape(self) -> None:
        response = self.create(types=["room"], max=2000, furnished=True)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(named(body, "AlertSave"), [])
        self.assertEqual(body["save_outcome"], "created")
        self.assertEqual(body["alert"]["filters"]["max"], 2000)
        self.assertEqual(body["alert"]["origin"], "app")
        alert = HomeAlert.objects.get()
        self.assertEqual((alert.max_price, alert.property_types, alert.delivery, alert.user), (2000, ["room"], "now", self.user))

    def test_city_only_is_a_valid_alert(self) -> None:
        self.assertEqual(self.post("/api/v1/me/alerts/", {"country": "CA", "city": "Toronto"}).status_code, 201)

    def test_a_retry_finds_the_same_row(self) -> None:
        self.create(max=2000)
        response = self.create(max=2000)
        self.assertEqual((response.status_code, response.json()["save_outcome"]), (200, "duplicate"))
        self.assertEqual(HomeAlert.objects.count(), 1)

    def test_the_same_one_switched_off_comes_back_on(self) -> None:
        pk = self.make_alert(max=2000)
        self.patch(f"/api/v1/me/alerts/{pk}/", {"alert_state": "off"})
        response = self.create(max=2000)
        self.assertEqual(response.json()["save_outcome"], "reactivated")
        self.assertEqual(response.json()["alert"]["alert_state"], "active")

    def test_at_the_cap(self) -> None:
        self.fill_cap()
        self.assert_error(self.create(max=5000), "alert_cap", "/api/v1/me/alerts/")

    def test_turning_the_same_one_back_on_at_the_cap_is_refused(self) -> None:
        pk = self.make_alert(max=9000)
        self.patch(f"/api/v1/me/alerts/{pk}/", {"alert_state": "off"})
        self.fill_cap()
        self.assert_error(self.create(max=9000), "alert_cap", "/api/v1/me/alerts/")

    def test_a_wanted_alert_is_outside_the_cap(self) -> None:
        wanted = self.listing(1, kind="wanted", price_max=1500)
        HomeAlert.objects.create(telegram_user_id=self.user.telegram_user_id, chat_id=1, country="CA", city="Toronto", wanted_listing=wanted)
        self.fill_cap()
        body = self.get("/api/v1/me/alerts/").json()
        self.assertEqual((len(body["results"]), body["active_count"], body["cap"]), (MAX_ALERTS_PER_USER + 1, MAX_ALERTS_PER_USER, MAX_ALERTS_PER_USER))

    def test_no_such_city(self) -> None:
        body = self.assert_error(self.post("/api/v1/me/alerts/", {"country": "CA", "city": "Nowhere"}), "validation_failed", "/api/v1/me/alerts/")
        self.assertEqual(body["fields"], {"city": ["invalid_choice"]})

    def test_a_bad_filter_is_a_field_error(self) -> None:
        response = self.create(term="forever")
        self.assert_error(response, "validation_failed", "/api/v1/me/alerts/")

    def test_a_body_that_cannot_be_read(self) -> None:
        response = self.client.post("/api/v1/me/alerts/", "{", content_type="application/json", **self.auth)
        self.assert_error(response, "bad_request", "/api/v1/me/alerts/")

    def test_area_ids_become_names_for_matching(self) -> None:
        area = HomeArea.objects.create(country="CA", city="Toronto", canonical="test quarter", fa="محله آزمایشی")
        pk = self.make_alert(area_ids=[area.pk])
        alert = HomeAlert.objects.get(pk=pk)
        self.assertEqual(alert.area, "test quarter")
        self.assertEqual(self.get(f"/api/v1/me/alerts/{pk}/").json()["filters"]["area_ids"], [area.pk])

    def test_bot_not_opened_is_reported_and_saved_anyway(self) -> None:
        self.assertFalse(self.create(max=2000).json()["bot_open"])
        HomeChatState.objects.create(telegram_user_id=self.user.telegram_user_id)
        self.assertTrue(self.create(max=2100).json()["bot_open"])


class ManageAlertTests(P3Case):
    def test_empty_list(self) -> None:
        body = self.get("/api/v1/me/alerts/").json()
        self.assertEqual(named(body, "AlertList"), [])
        self.assertEqual((body["results"], body["active_count"]), ([], 0))

    def test_newest_first_and_bot_alerts_show_their_filters(self) -> None:
        HomeAlert.objects.create(
            telegram_user_id=self.user.telegram_user_id, chat_id=1, country="CA", city="Toronto", max_price=1500, property_types=["room"], created_at=timezone.now() - timedelta(days=2)
        )
        pk = self.make_alert(max=2000)
        body = self.get("/api/v1/me/alerts/").json()
        self.assertEqual(named(body, "AlertList"), [])
        self.assertEqual(body["results"][0]["id"], pk)
        bot = body["results"][1]
        self.assertEqual((bot["origin"], bot["filters"]["max"], bot["filters"]["types"]), ("bot", 1500, ["room"]))

    def test_a_city_that_left_is_flagged(self) -> None:
        pk = self.make_alert(max=2000)
        HomeAlert.objects.filter(pk=pk).update(city="Calgary")
        self.assertFalse(self.get(f"/api/v1/me/alerts/{pk}/").json()["city_active"])

    def test_someone_elses_alert_is_gone(self) -> None:
        pk = self.make_alert(max=2000)
        self.auth = bearer_auth(make_user(telegram_user_id=880002))
        self.assert_error(self.get(f"/api/v1/me/alerts/{pk}/"), "alert_gone", "/api/v1/me/alerts/{id}/")
        self.assert_error(self.patch(f"/api/v1/me/alerts/{pk}/", {"delivery": "daily"}), "alert_gone", "/api/v1/me/alerts/{id}/")
        self.assert_error(self.post(f"/api/v1/me/alerts/{pk}/viewed/"), "alert_gone", "/api/v1/me/alerts/{id}/viewed/")
        self.assert_error(self.get(f"/api/v1/me/alerts/{pk}/listings/"), "alert_gone", "/api/v1/me/alerts/{id}/listings/")
        self.assertEqual(self.client.delete(f"/api/v1/me/alerts/{pk}/", **self.auth).status_code, 204)
        self.assertTrue(HomeAlert.objects.filter(pk=pk).exists())

    def test_pause_undo_off_on(self) -> None:
        url = f"/api/v1/me/alerts/{self.make_alert(max=2000)}/"
        paused = self.patch(url, {"alert_state": "paused"}).json()
        self.assertEqual(named(paused, "AlertRow"), [])
        self.assertEqual(paused["alert_state"], "paused")
        self.assertTrue(paused["paused_until"])
        self.assertEqual(self.patch(url, {"alert_state": "paused"}).json()["paused_until"], paused["paused_until"])
        self.assertEqual(self.patch(url, {"alert_state": "active"}).json()["paused_until"], None)
        self.assertEqual(self.patch(url, {"alert_state": "off"}).json()["alert_state"], "off")
        self.assertEqual(self.patch(url, {"alert_state": "active"}).json()["alert_state"], "active")

    def test_turning_on_at_the_cap(self) -> None:
        url = f"/api/v1/me/alerts/{self.make_alert(max=9000)}/"
        self.patch(url, {"alert_state": "off"})
        self.fill_cap()
        self.assert_error(self.patch(url, {"alert_state": "active"}), "alert_cap", "/api/v1/me/alerts/{id}/")

    def test_edit_filters_and_delivery(self) -> None:
        url = f"/api/v1/me/alerts/{self.make_alert(max=2000)}/"
        body = self.patch(url, {"filters": {"max": 2500, "types": ["studio"]}, "delivery": "daily"}).json()
        self.assertEqual((body["filters"]["max"], body["delivery"]), (2500, "daily"))
        alert = HomeAlert.objects.get()
        self.assertEqual((alert.max_price, alert.property_types), (2500, ["studio"]))

    def test_an_edit_that_matches_another_alert(self) -> None:
        first = self.make_alert(max=2000)
        second = self.make_alert(max=3000)
        body = self.assert_error(self.patch(f"/api/v1/me/alerts/{second}/", {"filters": {"max": 2000}}), "alert_duplicate", "/api/v1/me/alerts/{id}/")
        self.assertEqual(body["duplicate_id"], first)

    def test_a_wanted_alert_cannot_be_edited_but_can_be_switched(self) -> None:
        wanted = self.listing(1, kind="wanted", price_max=1500)
        alert = HomeAlert.objects.create(telegram_user_id=self.user.telegram_user_id, chat_id=1, country="CA", city="Toronto", wanted_listing=wanted)
        url = f"/api/v1/me/alerts/{alert.pk}/"
        self.assert_error(self.patch(url, {"delivery": "daily"}), "alert_not_editable", "/api/v1/me/alerts/{id}/")
        self.assertEqual(self.patch(url, {"alert_state": "off"}).json()["alert_state"], "off")

    def test_delete_is_safe_to_retry(self) -> None:
        url = f"/api/v1/me/alerts/{self.make_alert(max=2000)}/"
        self.assertEqual(self.client.delete(url, **self.auth).status_code, 204)
        self.assertEqual(self.client.delete(url, **self.auth).status_code, 204)
        self.assert_error(self.get(url), "alert_gone", "/api/v1/me/alerts/{id}/")

    def test_new_listings_then_viewed(self) -> None:
        pk = self.make_alert()
        HomeAlert.objects.filter(pk=pk).update(viewed_at=timezone.now() - timedelta(days=1), created_at=timezone.now() - timedelta(days=1))
        listing = self.listing(1)
        body = self.get(f"/api/v1/me/alerts/{pk}/listings/").json()
        self.assertEqual(named(body, "AlertListings"), [])
        self.assertEqual(([card["id"] for card in body["results"]], body["total"], body["next_cursor"]), ([listing.pk], 1, None))
        self.assertEqual(self.get(f"/api/v1/me/alerts/{pk}/").json()["new_count"], 1)
        self.assertEqual(self.post(f"/api/v1/me/alerts/{pk}/viewed/").status_code, 204)
        self.assertEqual(self.get(f"/api/v1/me/alerts/{pk}/").json()["new_count"], 0)

    def test_an_off_alert_has_nothing_new(self) -> None:
        pk = self.make_alert()
        self.patch(f"/api/v1/me/alerts/{pk}/", {"alert_state": "off"})
        self.assertEqual(self.get(f"/api/v1/me/alerts/{pk}/listings/").json()["results"], [])

    def test_a_bad_cursor_is_a_field_error(self) -> None:
        pk = self.make_alert()
        self.assert_error(self.get(f"/api/v1/me/alerts/{pk}/listings/", cursor="x"), "validation_failed", "/api/v1/me/alerts/{id}/listings/")

    def test_bot_blocked_shows_and_clears_when_turned_on(self) -> None:
        pk = self.make_alert()
        HomeAlert.objects.filter(pk=pk).update(active=False)
        HomeAlertSent.objects.create(telegram_user_id=self.user.telegram_user_id, listing=self.listing(1), status=AlertSendStatus.BLOCKED)
        self.assertTrue(self.get("/api/v1/me/alerts/").json()["bot_blocked"])
        self.patch(f"/api/v1/me/alerts/{pk}/", {"alert_state": "active"})
        self.assertFalse(self.get("/api/v1/me/alerts/").json()["bot_blocked"])


class InboxTests(P3Case):
    def note(self, **values) -> HomeNotification:
        return HomeNotification.objects.create(user=self.user, kind=values.pop("kind", NotificationKind.LISTING_LIVE), **values)

    def test_none(self) -> None:
        body = self.get("/api/v1/me/notifications/").json()
        self.assertEqual(body, {"results": [], "unread": 0, "next_cursor": None})

    def test_targets_active_closed_and_gone(self) -> None:
        # Different prices and areas, or review merges these three into one.
        live = self.note(listing=self.listing(1))
        closed_listing = self.listing(2, price_amount=1400, area="Downtown", contact_telegram="other_two")
        mark_taken(closed_listing, self.user.telegram_user_id, self.user)
        mark_taken(closed_listing, 880009)
        closed = self.note(kind=NotificationKind.SAVED_CLOSED, listing=closed_listing)
        gone = self.note(kind=NotificationKind.SAVED_CLOSED)
        held_listing = self.listing(3, price_amount=1800, area="Etobicoke", contact_telegram="other_three")
        held_listing.needs_review = True
        held_listing.save()
        held = self.note(kind=NotificationKind.LISTING_HELD, listing=held_listing)
        body = self.get("/api/v1/me/notifications/").json()
        self.assertEqual(named(body, "NotificationList"), [])
        states = {row["id"]: row["target_state"] for row in body["results"]}
        self.assertEqual(states, {live.pk: "active", closed.pk: "closed", gone.pk: "gone", held.pk: "active"})

    def test_only_ones_own_and_paged(self) -> None:
        other = make_user(telegram_user_id=880003)
        HomeNotification.objects.create(user=other, kind=NotificationKind.LISTING_LIVE)
        for _ in range(31):
            self.note()
        first = self.get("/api/v1/me/notifications/").json()
        self.assertEqual((len(first["results"]), first["next_cursor"], first["unread"]), (30, "30", 31))
        last = self.get("/api/v1/me/notifications/", cursor="30").json()
        self.assertEqual((len(last["results"]), last["next_cursor"]), (1, None))

    def test_older_than_retention_is_not_listed_and_gets_purged(self) -> None:
        old = self.note()
        HomeNotification.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=91))
        self.assertEqual(self.get("/api/v1/me/notifications/").json()["results"], [])
        self.assertEqual(inbox.purge_old(), 1)
        self.assertFalse(HomeNotification.objects.exists())

    def test_read_some_all_and_retry(self) -> None:
        first, second = self.note(), self.note()
        foreign = HomeNotification.objects.create(user=make_user(telegram_user_id=880004), kind=NotificationKind.LISTING_LIVE)
        self.assertEqual(self.post("/api/v1/me/notifications/read/", {"ids": [first.pk, foreign.pk]}).json(), {"unread": 1})
        foreign.refresh_from_db()
        self.assertIsNone(foreign.read_at)
        self.assertEqual(self.post("/api/v1/me/notifications/read/").json(), {"unread": 0})
        self.assertEqual(self.post("/api/v1/me/notifications/read/").json(), {"unread": 0})
        second.refresh_from_db()
        self.assertIsNotNone(second.read_at)

    def test_read_rejects_a_bad_id(self) -> None:
        self.assert_error(self.post("/api/v1/me/notifications/read/", {"ids": ["x"]}), "validation_failed", "/api/v1/me/notifications/read/")

    def test_nothing_is_written_while_the_flag_is_off(self) -> None:
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(account_api_on=False)
        inbox.write(self.user.pk, NotificationKind.LISTING_LIVE)
        self.assertFalse(HomeNotification.objects.exists())

    def test_a_failed_write_never_raises(self) -> None:
        with patch("home.inbox.HomeNotification.objects.create", side_effect=RuntimeError("db")):
            inbox.write(self.user.pk, NotificationKind.LISTING_LIVE)

    def test_saved_closing_tells_who_saved_it(self) -> None:
        listing = self.listing(1)
        HomeSaved.objects.create(user=self.user, listing=listing)
        with patch("home.status_posts.apply"):
            mark_taken(listing, 880010)
            mark_taken(listing, 880011)
        row = HomeNotification.objects.get()
        self.assertEqual((row.user, row.kind, row.listing), (self.user, NotificationKind.SAVED_CLOSED, listing))

    def test_expiry_tells_owner_and_savers(self) -> None:
        saver = make_user(telegram_user_id=880005)
        listing = self.listing(1)
        post = listing.post
        post.author_id = self.user.telegram_user_id
        post.save(update_fields=["author_id"])
        HomeSaved.objects.create(user=saver, listing=listing)
        type(listing).objects.filter(pk=listing.pk).update(last_seen=timezone.now() - timedelta(days=60))
        expire_stale()
        kinds = {row.user_id: row.kind for row in HomeNotification.objects.all()}
        self.assertEqual(kinds, {self.user.pk: NotificationKind.LISTING_EXPIRED, saver.pk: NotificationKind.SAVED_CLOSED})

    def test_the_job_step_purges(self) -> None:
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(freshness_enabled=True)
        old = self.note()
        HomeNotification.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=91))
        self.assertEqual(run_freshness()["inbox_purged"], 1)


class AccountTests(P3Case):
    def test_new_person(self) -> None:
        body = self.get("/api/v1/me/account/").json()
        self.assertEqual(named(body, "Account"), [])
        self.assertEqual(body["counts"], {"saved": 0, "listings": 0, "alerts_active": 0, "alerts_new": 0, "unread": 0})
        self.assertIsNone(body["channel"])
        self.assertFalse(body["bot_open"])

    def test_no_username(self) -> None:
        self.assertIsNone(self.get("/api/v1/me/account/").json()["user"]["telegram_username"])

    def test_counts_and_channel(self) -> None:
        HomeUserPrefs.objects.create(user=self.user, country="CA", city="Toronto")
        HomeSaved.objects.create(user=self.user, listing=self.listing(1))
        HomeNotification.objects.create(user=self.user, kind=NotificationKind.LISTING_LIVE)
        self.make_alert(max=2000)
        body = self.get("/api/v1/me/account/").json()
        self.assertEqual((body["counts"]["saved"], body["counts"]["alerts_active"], body["counts"]["unread"]), (1, 1, 1))
        self.assertEqual(body["channel"]["username"], "chamedoon_toronto")


class SettingsAtSendTests(P3Case):
    """F23 and the delivery choice, with no message leaving: send_alert is mocked and alerts stay off outside these tests."""

    def alert(self, **values) -> HomeAlert:
        return HomeAlert(telegram_user_id=self.user.telegram_user_id, chat_id=1, country="CA", city="Toronto", **values)

    def test_quiet_hours_are_the_persons_own_in_the_citys_clock(self) -> None:
        prefs = HomeUserPrefs(user=self.user, quiet_from=clock(11), quiet_to=clock(13))
        self.assertTrue(is_quiet(self.alert(), TORONTO_NOON, prefs))
        self.assertFalse(is_quiet(self.alert(), TORONTO_EARLY, prefs))

    def test_quiet_hours_over_midnight(self) -> None:
        prefs = HomeUserPrefs(user=self.user, quiet_from=clock(22), quiet_to=clock(7))
        self.assertFalse(is_quiet(self.alert(), TORONTO_NOON, prefs))
        self.assertTrue(is_quiet(self.alert(), datetime(2026, 11, 3, 8, 0, tzinfo=dt_timezone.utc), prefs))

    def test_daily_waits_for_nine_and_goes_once(self) -> None:
        self.assertTrue(delivery_due(self.alert()))
        daily = self.alert(delivery="daily")
        self.assertFalse(delivery_due(daily, TORONTO_EARLY))
        self.assertTrue(delivery_due(daily, TORONTO_NOON))
        daily.last_notified_at = TORONTO_NOON - timedelta(hours=1)
        self.assertFalse(delivery_due(daily, TORONTO_NOON))
        self.assertTrue(delivery_due(daily, TORONTO_NOON + timedelta(days=1)))

    def send(self, alert: HomeAlert) -> dict:
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(enabled=True, alerts_enabled=True, bot_token="123:FAKE")
        alert.save()
        HomeAlert.objects.filter(pk=alert.pk).update(created_at=timezone.now() - timedelta(days=1))
        self.listing(1)
        with patch("home.alerts.send_alert", return_value=7) as sent, patch("home.alerts.is_quiet", return_value=False):
            result = notify_pending()
        return {"sent": result["sent"], "calls": sent.call_count}

    def test_a_match_lands_in_the_inbox_once(self) -> None:
        result = self.send(self.alert(user=self.user, answered=["type", "budget", "area", "gender", "rooms", "term", "registration"], complete=True))
        self.assertEqual(result, {"sent": 1, "calls": 1})
        row = HomeNotification.objects.get()
        self.assertEqual((row.kind, row.match_count, row.user), (NotificationKind.ALERT_MATCH, 1, self.user))

    def test_the_master_switch_mutes_the_send(self) -> None:
        HomeUserPrefs.objects.create(user=self.user, notify_enabled=False)
        result = self.send(self.alert(user=self.user, answered=["type", "budget", "area", "gender", "rooms", "term", "registration"], complete=True))
        self.assertEqual(result, {"sent": 0, "calls": 0})
        self.assertFalse(HomeAlertSent.objects.exists())

    def test_alerts_stay_off_by_default(self) -> None:
        self.assertFalse(HomeSettings().alerts_enabled)
        self.assertFalse(HomeSettings().alerts_api_on)
        self.assertFalse(HomeSettings().account_api_on)
        self.assertFalse(HomeSettings().posting_api_on)
        self.assertFalse(HomeSettings().smart_posting_on)


class UnpricedTests(P3Case):
    def test_a_new_alert_with_a_budget_leaves_out_no_price_unless_asked(self) -> None:
        listing = self.listing(1, price_amount=None)
        self.assertFalse(fits({"max": 2000, "unpriced": False}, listing))
        self.assertTrue(fits({"max": 2000, "unpriced": True}, listing))
        self.assertTrue(fits({"max": 2000}, listing))
