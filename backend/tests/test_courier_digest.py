"""Daily courier board: scopes, window, skip rule, due hour, one send per day, per-request posts off."""

from __future__ import annotations

import io
import json
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from courier import digest
from courier.models import CourierDigestPost, CourierSettings
from item_requests.models import CourierChannel, ItemRequest, RequestStatus, RequestType
from item_requests.seed import seed_catalog
from notifications import country_channels as cc
from notifications.channel import per_request_posts_on, publish_request_to_channel
from tests.helpers import TEST_SECRET, make_user

CA_CHAT = -1004470852135
MAIN_CHAT = -1004370824307
# 2026-11-10 20:30 in Toronto and 21:30 in Berlin, so 01:30 UTC next day.
NOW = datetime(2026, 11, 11, 1, 30, tzinfo=dt_timezone.utc)
TODAY_TORONTO = date(2026, 11, 10)

SETTINGS = {
    "SECRET_KEY": TEST_SECRET,
    "TELEGRAM_BOT_TOKEN": "test-token",
    "TELEGRAM_BOT_USERNAME": "chamedoon_courier_bot",
    "TELEGRAM_CHANNEL_ENABLED": True,
    "TELEGRAM_CHANNEL_ID": str(MAIN_CHAT),
    "TELEGRAM_CHANNEL_USERNAME": "chamedoon_courier",
}


def make(user, *, kind=RequestType.SUPPLY, origin=("CA", "toronto"), dest=("IR", "tehran"), when=None, stops=None, **extra):
    when = when or TODAY_TORONTO + timedelta(days=3)
    return ItemRequest.objects.create(
        user=user,
        type=kind,
        origin_country=origin[0],
        origin_city=origin[1],
        destination_country=dest[0],
        destination_city=dest[1],
        destination_cities=stops or [],
        date_from=when - timedelta(days=5),
        date_to=when,
        flight_date=when if kind == RequestType.SUPPLY else None,
        desired_date=when if kind == RequestType.DEMAND else None,
        expires_at=NOW + timedelta(days=30),
        **extra,
    )


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@override_settings(**SETTINGS)
class CourierDigestTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.user = make_user(telegram_user_id=98001, first_name="Sara")
        CourierChannel.objects.filter(country="CA").update(chat_id=CA_CHAT, username="chamedoon_courier_ca", enabled=True)

    def test_country_board_keeps_only_its_routes_in_the_window(self) -> None:
        make(self.user)
        make(self.user, kind=RequestType.DEMAND, origin=("IR", "tehran"), dest=("CA", "toronto"))
        make(self.user, origin=("IR", "tehran"), dest=("DE", "berlin"), stops=[{"country": "CA", "city": "toronto"}])
        make(self.user, origin=("IR", "tehran"), dest=("DE", "berlin"))  # no Canada
        make(self.user, when=TODAY_TORONTO + timedelta(days=20))  # past the 14 days
        make(self.user, status=RequestStatus.EXPIRED)
        data = digest.build("ca", now=NOW)
        self.assertEqual(data["total"], 3)
        self.assertEqual((data["supply"], data["demand"]), ("۲", "۱"))
        self.assertEqual(data["handle"], "chamedoon_courier_ca")
        self.assertEqual(data["rows"][0]["from"], "تورنتو")
        self.assertEqual(data["rows"][0]["month"], "Nov")
        self.assertIn("آبان", data["rows"][0]["sub"])
        self.assertEqual(data["day"], {"main": "10 Nov", "sub": "۱۹ آبان"})
        self.assertIn(data["photo"], digest.PHOTOS)
        self.assertEqual(data["buttons"][0][0]["url"], "https://t.me/chamedoon_courier_bot?startapp=courier")
        self.assertNotIn("←", json.dumps(data, ensure_ascii=False))

    def test_too_little_is_skipped_and_recorded(self) -> None:
        make(self.user)
        make(self.user)
        self.assertIsNone(digest.build("ca", now=NOW))
        with patch("courier.digest.timezone.now", return_value=NOW):
            call_command("courier_digest", "build", "--scope", "ca", stderr=io.StringIO())
        self.assertEqual(CourierDigestPost.objects.get(scope="ca").status, "skipped")

    def test_umbrella_groups_every_route_by_country_pair(self) -> None:
        for _ in range(3):
            make(self.user, origin=("IR", "tehran"), dest=("DE", "berlin"))
        make(self.user, kind=RequestType.DEMAND)
        make(self.user, when=TODAY_TORONTO + timedelta(days=40))  # far dates count here
        data = digest.build("all", now=NOW)
        self.assertEqual(data["kind"], "tiles")
        self.assertEqual(data["total"], 5)
        self.assertEqual(data["tiles"][0]["from_flag"], "ir")
        self.assertEqual(data["tiles"][0]["supply"], "۳")
        self.assertEqual(data["tiles"][1], {**data["tiles"][1], "from_flag": "ca", "supply": "۱", "demand": "۱"})
        self.assertEqual(data["handle"], "chamedoon_courier")

    def test_due_only_at_eight_pm_local_with_the_switch_on(self) -> None:
        at_eight_toronto = datetime(2026, 11, 11, 1, 0, tzinfo=dt_timezone.utc)
        self.assertFalse(digest.due("ca", now=at_eight_toronto))
        CourierSettings.objects.update_or_create(pk=1, defaults={"channel_digest_on": True})
        self.assertTrue(digest.due("ca", now=at_eight_toronto))
        self.assertFalse(digest.due("de", now=at_eight_toronto))
        self.assertFalse(digest.due("ca", now=at_eight_toronto + timedelta(hours=1)))
        CourierDigestPost.objects.create(scope="ca", day=TODAY_TORONTO, status="failed")
        self.assertTrue(digest.due("ca", now=at_eight_toronto))
        CourierDigestPost.objects.filter(scope="ca").update(status="sent")
        self.assertFalse(digest.due("ca", now=at_eight_toronto))

    def test_send_posts_once_per_day_with_buttons(self) -> None:
        for _ in range(3):
            make(self.user)
        data = digest.build("ca", now=NOW)
        bodies = []

        def fake_urlopen(request, timeout=0):
            bodies.append(request.data)
            return FakeResponse(json.dumps({"ok": True, "result": {"message_id": 77}}).encode())

        with patch("courier.digest.urllib.request.urlopen", side_effect=fake_urlopen):
            row = digest.send("ca", b"PNG", data, now=NOW)
            again = digest.send("ca", b"PNG", data, now=NOW)
        self.assertEqual((row.status, row.message_id, row.chat_id), ("sent", 77, CA_CHAT))
        self.assertEqual(again.pk, row.pk)
        self.assertEqual(len(bodies), 1)
        self.assertIn(b"inline_keyboard", bodies[0])
        self.assertIn(str(CA_CHAT).encode(), bodies[0])

    def test_failed_send_keeps_the_reason_without_the_token(self) -> None:
        for _ in range(3):
            make(self.user)
        data = digest.build("ca", now=NOW)
        with patch("courier.digest.urllib.request.urlopen", side_effect=OSError("down test-token")):
            row = digest.send("ca", b"PNG", data, now=NOW)
        self.assertEqual(row.status, "failed")
        self.assertNotIn("test-token", row.error)

    def test_switch_turns_off_per_request_posts(self) -> None:
        item = make(self.user)
        self.assertTrue(per_request_posts_on())
        self.assertEqual(cc.ready(), "")
        CourierSettings.objects.update_or_create(pk=1, defaults={"channel_digest_on": True})
        self.assertFalse(per_request_posts_on())
        self.assertIn("daily board", cc.ready())
        with patch("notifications.telegram.call_telegram_api") as api:
            self.assertFalse(publish_request_to_channel(item))
        api.assert_not_called()
