"""The daily board's hour, window, minimum and rows come from Admin → Courier settings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone

from django.contrib.auth.models import User as StaffUser
from django.test import TestCase, override_settings

from courier import digest
from courier.admin import CourierSettingsForm
from courier.models import CourierSettings
from item_requests.models import CourierChannel, RequestType
from item_requests.seed import seed_catalog
from tests.helpers import make_user
from tests.test_courier_digest import CA_CHAT, NOW, SETTINGS, TODAY_TORONTO, make


def set_board(**values) -> CourierSettings:
    row = CourierSettings.load()
    row.channel_digest_on = True
    for name, value in values.items():
        setattr(row, name, value)
    row.save()
    return row


@override_settings(**SETTINGS)
class BoardKnobTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.user = make_user(telegram_user_id=98101, first_name="Nima")
        CourierChannel.objects.filter(country="CA").update(chat_id=CA_CHAT, username="chamedoon_courier_ca", enabled=True)

    def test_defaults_are_todays_values(self) -> None:
        self.assertEqual(digest.knobs(), digest.Knobs(hour=20, window_days=14, min_items=3, rows=5))

    def test_send_hour_follows_the_admin(self) -> None:
        at_eight_toronto = datetime(2026, 11, 11, 1, 0, tzinfo=dt_timezone.utc)
        set_board(digest_hour=21)
        self.assertFalse(digest.due("ca", now=at_eight_toronto))
        self.assertTrue(digest.due("ca", now=at_eight_toronto + timedelta(hours=1)))

    def test_window_follows_the_admin(self) -> None:
        for _ in range(3):
            make(self.user)
        make(self.user, when=TODAY_TORONTO + timedelta(days=20))
        self.assertEqual(digest.build("ca", now=NOW)["total"], 3)
        set_board(digest_window_days=30)
        self.assertEqual(digest.build("ca", now=NOW)["total"], 4)

    def test_minimum_and_rows_follow_the_admin(self) -> None:
        for _ in range(2):
            make(self.user, kind=RequestType.DEMAND, origin=("IR", "tehran"), dest=("CA", "toronto"))
        self.assertIsNone(digest.build("ca", now=NOW))
        set_board(digest_min_items=2, digest_rows=1)
        data = digest.build("ca", now=NOW)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["more"], "۱")

    def test_out_of_range_values_are_clamped(self) -> None:
        set_board(digest_hour=99, digest_rows=0)
        knob = digest.knobs()
        self.assertEqual((knob.hour, knob.rows), (23, 1))


class BoardKnobAdminTests(TestCase):
    def test_form_rejects_values_out_of_range(self) -> None:
        row = CourierSettings.load()
        data = {
            "intro_daily_limit": 5,
            "alert_daily_cap": 10,
            "digest_hour": 24,
            "digest_window_days": 14,
            "digest_min_items": 0,
            "digest_rows": 5,
        }
        form = CourierSettingsForm(data=data, instance=row)
        self.assertFalse(form.is_valid())
        self.assertIn("digest_hour", form.errors)
        self.assertIn("digest_min_items", form.errors)

    def test_admin_page_shows_the_board_knobs(self) -> None:
        StaffUser.objects.create_superuser("ops", "ops@example.com", "ops-pass")
        self.client.login(username="ops", password="ops-pass")
        CourierSettings.load()
        text = self.client.get("/admin/courier/couriersettings/1/change/").content.decode()
        for name in ("digest_hour", "digest_window_days", "digest_min_items", "digest_rows"):
            self.assertIn(f'name="{name}"', text)
