"""courier_v2 sends people from the courier template pages to the new app. Off, or for anyone not on it, nothing changes."""

from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from courier.models import CourierSettings
from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from miniapp.auth import SESSION_USER_KEY
from spa import views
from tests.helpers import make_user
from tests.test_courier_api import trip_payload


@override_settings(ALLOWED_HOSTS=["testserver"])
class ShellGateTests(SimpleTestCase):
    def _enabled(self, *, home=None, courier=None) -> bool:
        home_row = mock.Mock(**{"miniapp_v2": False, "miniapp_v2_allowlist": "", **(home or {})})
        courier_row = mock.Mock(**{"courier_v2": False, "courier_v2_allowlist": "", **(courier or {})})
        with (
            mock.patch.dict("os.environ", {"VERCEL": "1", "VERCEL_ENV": "production"}),
            mock.patch.object(views.conf, "row", return_value=home_row),
            mock.patch.object(views.courier_conf, "row", return_value=courier_row),
        ):
            return views._shell_enabled()

    def test_off_with_both_flags_off(self) -> None:
        self.assertFalse(self._enabled())

    def test_on_for_the_courier_allowlist_or_flag(self) -> None:
        self.assertTrue(self._enabled(courier={"courier_v2_allowlist": "7701"}))
        self.assertTrue(self._enabled(courier={"courier_v2": True}))

    def test_a_failed_courier_read_fails_closed(self) -> None:
        home_row = mock.Mock(miniapp_v2=False, miniapp_v2_allowlist="")
        with (
            mock.patch.dict("os.environ", {"VERCEL": "1", "VERCEL_ENV": "production"}),
            mock.patch.object(views.conf, "row", return_value=home_row),
            mock.patch.object(views.courier_conf, "row", side_effect=RuntimeError("db")),
        ):
            self.assertFalse(views._shell_enabled())


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
        row = CourierSettings.objects.first() or CourierSettings()
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
        self.assertEqual(response["Location"], "/app/v2/courier/?tgWebAppStartParam=explore_5")
        self.assertEqual(self.client.get("/app/courier/alerts/")["Location"], "/app/v2/courier/alerts")
        self.assertEqual(self.client.get(f"/app/explore/{self.trip.pk}/")["Location"], f"/app/v2/courier/request/{self.trip.pk}")

    def test_writes_stay_on_the_templates(self) -> None:
        self._switch(courier_v2_allowlist="7701")
        response = self.client.post(f"/app/explore/{self.trip.pk}/")
        self.assertNotIn("/app/v2/", response.get("Location", ""))
