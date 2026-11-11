"""System health watches the daily courier board and stops flagging per-request channel posts it replaced."""

from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from api.config_check import freshness_report
from courier import digest
from courier.models import CourierDigestPost, CourierSettings
from item_requests.models import CourierChannel
from tests.helpers import TEST_SECRET

SETTINGS = {
    "SECRET_KEY": TEST_SECRET,
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_BOT_USERNAME": "chamedoon_courier_bot",
    "TELEGRAM_CHANNEL_ENABLED": True,
    "TELEGRAM_CHANNEL_ID": "-1004370824307",
    "TELEGRAM_CHANNEL_USERNAME": "chamedoon_courier",
}


def board_on(on: bool = True) -> None:
    row = CourierSettings.load()
    row.channel_digest_on = on
    row.save()


def board(scope: str, *, hours_ago: float, status: str = "sent", day: date | None = None) -> CourierDigestPost:
    row = CourierDigestPost.objects.create(scope=scope, day=day or date.today() - timedelta(days=int(hours_ago // 24)), status=status)
    CourierDigestPost.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(hours=hours_ago))
    return row


def rows() -> dict:
    return {row.key: row for row in freshness_report()}


@override_settings(**SETTINGS)
class BoardHealthTests(TestCase):
    def setUp(self) -> None:
        CourierChannel.objects.all().delete()
        CourierChannel.objects.create(country="CA", username="chamedoon_courier_ca", chat_id=-1004470852135)

    def test_live_scopes_are_the_ones_with_a_channel(self) -> None:
        self.assertEqual(sorted(digest.live_scopes()), ["all", "ca"])
        CourierChannel.objects.filter(country="CA").update(enabled=False)
        self.assertEqual(digest.live_scopes(), ["all"])

    def test_board_row_is_off_while_the_board_is_off(self) -> None:
        board_on(False)
        row = rows()["courier_board"]
        self.assertFalse(row.active)
        self.assertFalse(row.stale)

    def test_fresh_when_every_live_channel_had_a_board_today(self) -> None:
        board_on()
        board("ca", hours_ago=3)
        board("all", hours_ago=2, status="skipped")
        row = rows()["courier_board"]
        self.assertTrue(row.active)
        self.assertFalse(row.stale)

    def test_stale_when_one_channel_never_had_a_board(self) -> None:
        board_on()
        board("all", hours_ago=2)
        row = rows()["courier_board"]
        self.assertIsNone(row.at)
        self.assertTrue(row.stale)

    def test_stale_when_the_oldest_newest_board_is_too_old(self) -> None:
        board_on()
        board("ca", hours_ago=30)
        board("all", hours_ago=2)
        self.assertTrue(rows()["courier_board"].stale)

    def test_a_failed_board_does_not_count(self) -> None:
        board_on()
        board("ca", hours_ago=1, status="failed")
        board("all", hours_ago=1)
        self.assertTrue(rows()["courier_board"].stale)

    def test_per_request_post_rows_rest_while_the_board_replaces_them(self) -> None:
        board_on(False)
        self.assertTrue(rows()["courier_channel"].active)
        self.assertTrue(rows()["market_publish"].active)
        board_on()
        self.assertFalse(rows()["courier_channel"].active)
        self.assertFalse(rows()["market_publish"].active)
