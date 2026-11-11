"""courier_v2 sends people from the courier template pages to the new app. Off, or for anyone not on it, nothing changes."""

from __future__ import annotations

from django.test import TestCase

from courier.models import CourierSettings
from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from miniapp.auth import SESSION_USER_KEY
from tests.helpers import make_user
from tests.test_courier_api import trip_payload


class RedirectTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()
        cls.user = make_user(telegram_user_id=7701, first_name="Sara")
        cls.owner = make_user(telegram_user_id=7702, first_name="Ali")
        cls.trip = create_item_request(cls.owner, trip_payload(), sync_channel=False, sync_matches=False)

    def setUp(self) -> None:
        session = self.client.session
        session[SESSION_USER_KEY] = self.user.pk
        session.save()

    def _switch(self, **values) -> None:
        row = CourierSettings.load()
        for name, value in values.items():
            setattr(row, name, value)
        row.save()

    def test_flag_off_keeps_the_templates(self) -> None:
        self.assertEqual(self.client.get("/app/courier/").status_code, 200)
        self.assertEqual(self.client.get("/app/courier/alerts/").status_code, 200)
        self.assertEqual(self.client.get(f"/app/explore/{self.trip.pk}/").status_code, 200)

    def test_someone_else_on_the_allowlist_changes_nothing_for_this_person(self) -> None:
        self._switch(courier_v2_allowlist="1234")
        self.assertEqual(self.client.get("/app/courier/").status_code, 200)

    def test_on_the_allowlist_goes_to_the_app_and_keeps_launch_data(self) -> None:
        self._switch(courier_v2_allowlist="7701")
        response = self.client.get("/app/courier/", {"tgWebAppStartParam": "explore_5", "other": "drop"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/app/?tgWebAppStartParam=explore_5")
        self.assertEqual(self.client.get("/app/courier/alerts/")["Location"], "/app/alerts")
        self.assertEqual(self.client.get(f"/app/explore/{self.trip.pk}/")["Location"], f"/app/request/{self.trip.pk}")

    def test_writes_stay_on_the_templates(self) -> None:
        self._switch(courier_v2_allowlist="7701")
        response = self.client.post(f"/app/explore/{self.trip.pk}/")
        self.assertNotIn("/app/home/", response.get("Location", ""))


class MainAppOpenTests(RedirectTests):
    """The courier bot's main Mini App URL is /app/: a board's «همه» button, the menu button and plain opens."""

    BOARD = "board_ca_20261111"

    def test_flag_off_keeps_the_old_routes(self) -> None:
        self.assertEqual(self.client.get("/app/", {"tgWebAppStartParam": self.BOARD})["Location"], "/app/courier/")
        self.assertEqual(self.client.get("/app/").status_code, 200)

    def test_board_button_opens_the_new_app_with_its_start_param(self) -> None:
        self._switch(courier_v2=True)
        response = self.client.get("/app/", {"tgWebAppStartParam": self.BOARD})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/app/?startapp={self.BOARD}&tgWebAppStartParam={self.BOARD}")
        # A second tap on the same board is the same app, not the old home page.
        again = self.client.get("/app/", {"tgWebAppStartParam": self.BOARD})
        self.assertTrue(again["Location"].startswith("/app/"))

    def test_menu_button_and_plain_open_go_to_the_new_app(self) -> None:
        self._switch(courier_v2=True)
        self.assertEqual(self.client.get("/app/")["Location"], "/app/")
        self.assertEqual(self.client.get("/app/login/", {"startapp": "courier_alerts"})["Location"], "/app/?startapp=courier_alerts")

    def test_housing_and_outreach_keep_their_pages(self) -> None:
        self._switch(courier_v2=True)
        self.assertEqual(self.client.get("/app/", {"startapp": "home"})["Location"], "/app/home/")
        self.assertFalse(self.client.get("/app/", {"startapp": "o_abcdefgh12"})["Location"].startswith("/app/"))
        session = self.client.session
        session["miniapp_home_bot"] = True
        session.save()
        self.assertNotIn("/app/", self.client.get("/app/").get("Location", ""))

    def test_allowlist_only_for_listed_people(self) -> None:
        self._switch(courier_v2_allowlist="1234")
        self.assertEqual(self.client.get("/app/").status_code, 200)
