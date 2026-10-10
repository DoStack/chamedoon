from __future__ import annotations

import json
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from config.dashboard import build_dashboard_context
from market.migrate import _source_user, owner_for_post
from market.models import MarketPost
from matching.contact import SYNTHETIC_TELEGRAM_USER_ID
from tests.helpers import TEST_SECRET, make_user
from users.models import BotStart, User
from users.services import record_bot_start, upsert_telegram_user
from users.telegram import TelegramIdentity

HOOK_SECRET = "hook-secret"


def _identity(telegram_user_id: int, *, username: str | None = None, first_name: str = "Sara") -> TelegramIdentity:
    return TelegramIdentity(
        telegram_user_id=telegram_user_id,
        telegram_username=username,
        first_name=first_name,
        last_name=None,
    )


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False, TELEGRAM_WEBHOOK_SECRET=HOOK_SECRET)
class BotStartTests(APITestCase):
    def _start(self, telegram_user_id: int, *, text: str = "/start", chat_type: str = "private", **from_extra):
        return self.client.post(
            "/api/telegram/webhook/",
            {
                "message": {
                    "message_id": 1,
                    "text": text,
                    "chat": {"id": telegram_user_id, "type": chat_type},
                    "from": {
                        "id": telegram_user_id,
                        "first_name": "Sara",
                        "username": "sara_start",
                        **from_extra,
                    },
                }
            },
            format="json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=HOOK_SECRET,
        )

    def test_start_creates_organic_user_and_event(self) -> None:
        response = self._start(88101, text="/start invite")
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(telegram_user_id=88101)
        self.assertEqual(user.origin, "Organic")
        self.assertFalse(user.from_market)
        self.assertEqual(user.start_count, 1)
        self.assertIsNotNone(user.first_started_at)
        event = BotStart.objects.get(user=user)
        self.assertEqual(event.payload, "invite")
        self.assertEqual(event.telegram_user_id, 88101)

    def test_repeat_start_counts_same_person_twice(self) -> None:
        self._start(88102)
        self._start(88102, text="/start@CB_koolbarbot again")
        user = User.objects.get(telegram_user_id=88102)
        self.assertEqual(user.start_count, 2)
        self.assertEqual(BotStart.objects.filter(user=user).count(), 2)
        self.assertEqual(set(BotStart.objects.filter(user=user).values_list("payload", flat=True)), {"", "again"})

    def test_non_start_message_is_ignored(self) -> None:
        response = self._start(88103, text="hello")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(telegram_user_id=88103).count(), 0)
        self.assertEqual(BotStart.objects.count(), 0)

    def test_group_start_is_ignored(self) -> None:
        response = self._start(88104, chat_type="group")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BotStart.objects.count(), 0)

    def test_bot_account_start_is_ignored(self) -> None:
        response = self._start(88110, is_bot=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(BotStart.objects.count(), 0)

    def test_market_user_who_starts_is_converted(self) -> None:
        shadow = make_user(
            telegram_user_id=SYNTHETIC_TELEGRAM_USER_ID + 88,
            first_name="مریم",
            telegram_username="maryam_start",
            from_market=True,
        )
        real = record_bot_start(_identity(88105, username="maryam_start", first_name="Maryam"))
        real.user.refresh_from_db()
        shadow.refresh_from_db()
        self.assertTrue(real.user.from_market)
        self.assertEqual(real.user.origin, "Market + bot")
        self.assertFalse(shadow.is_active)

    def test_extracted_source_user_is_market(self) -> None:
        user = _source_user("user:extracted_sara", first_name="Sara", username="extracted_sara")
        self.assertTrue(user.from_market)
        self.assertEqual(user.origin, "Market extracted")
        self.assertGreaterEqual(user.telegram_user_id, SYNTHETIC_TELEGRAM_USER_ID)

    def test_owner_for_post_marks_existing_handle(self) -> None:
        existing = make_user(telegram_user_id=88106, first_name="Ali", telegram_username="ali_market")
        post = MarketPost.objects.create(
            channel_username="koolbarcanada",
            telegram_message_id=88106,
            posted_at=timezone.now(),
            text="@ali_market #مسافر تهران تورنتو",
            author_username="ali_market",
        )
        owner = owner_for_post(post)
        existing.refresh_from_db()
        self.assertEqual(owner.pk, existing.pk)
        self.assertTrue(existing.from_market)
        self.assertEqual(existing.origin, "Market extracted")

    def test_miniapp_user_stays_unknown_until_start(self) -> None:
        user = upsert_telegram_user(_identity(88107, username="mini_only"))
        self.assertEqual(user.origin, "Unknown")
        self.assertFalse(user.from_market)
        self.assertIsNone(user.first_started_at)

    def test_dashboard_counts_daily_starts(self) -> None:
        now = timezone.now()
        first = record_bot_start(_identity(88108, username="one"), started_at=now - timedelta(days=1))
        record_bot_start(_identity(88108, username="one"), started_at=now - timedelta(days=1))
        record_bot_start(_identity(88109, username="two"), started_at=now)
        context = build_dashboard_context(None, {})
        self.assertIn("bot_starts_chart", context)
        chart = json.loads(context["bot_starts_chart"])
        self.assertEqual(chart["datasets"][0]["label"], "People who started")
        self.assertEqual(chart["datasets"][1]["label"], "Total /start")
        self.assertEqual(sum(chart["datasets"][0]["data"]), 2)
        self.assertEqual(sum(chart["datasets"][1]["data"]), 3)
        self.assertEqual(context["kpis"][8]["title"], "Bot starts (14d)")
        self.assertEqual(context["kpis"][8]["metric"], 2)
        self.assertTrue(first.user.started_bot)
