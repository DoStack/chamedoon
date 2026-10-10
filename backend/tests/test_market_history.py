from __future__ import annotations

import importlib
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from market.ingest import ingest_all_market_channels, ingest_market_channel
from market.models import MarketPost, MarketRole
from market.telegram_history import market_invite_sources, message_to_raw


class MigrationHistoryTests(TestCase):
    def test_applied_market_migrations_keep_the_original_user_dependency(self) -> None:
        older = importlib.import_module("market.migrations.0007_reassign_imported_owners")
        newer = importlib.import_module("market.migrations.0008_normalize_user_first_names")
        self.assertIn(("users", "0001_initial"), older.Migration.dependencies)
        self.assertNotIn(("users", "0002_user_origin_and_botstart"), older.Migration.dependencies)
        self.assertIn(("users", "0001_initial"), newer.Migration.dependencies)
        self.assertNotIn(("users", "0002_user_origin_and_botstart"), newer.Migration.dependencies)


class TelegramHistoryTests(TestCase):
    def test_message_to_raw_keeps_the_sender_and_private_link(self) -> None:
        message = SimpleNamespace(
            id=42,
            date=datetime(2026, 10, 10, 8, 0, tzinfo=dt_timezone.utc),
            message="#مسافر تهران به تورنتو",
            action=None,
            views=4,
            photo=None,
            post_author="",
            sender=SimpleNamespace(username="traveler", first_name="Ali", last_name=""),
        )
        raw = message_to_raw("koolbarcanada-group", message, public_username="", chat_id=99)
        self.assertIsNotNone(raw)
        assert raw is not None
        self.assertEqual(raw["author_username"], "traveler")
        self.assertEqual(raw["author_name"], "Ali")
        self.assertEqual(raw["source_url"], "https://t.me/c/99/42")
        self.assertEqual(raw["telegram_message_id"], 42)

    def test_message_to_raw_skips_service_messages_and_the_group_itself(self) -> None:
        service = SimpleNamespace(id=1, date=timezone.now(), message="joined", action=object(), sender=None)
        self.assertIsNone(message_to_raw("group", service))
        own = SimpleNamespace(
            id=2,
            date=timezone.now(),
            message="hello",
            action=None,
            views=None,
            photo=None,
            post_author="",
            sender=SimpleNamespace(username="koolbarcanada-group", first_name="", last_name=""),
        )
        raw = message_to_raw("koolbarcanada-group", own, chat_id=5)
        assert raw is not None
        self.assertEqual(raw["author_username"], "")

    @override_settings(MARKET_SOURCE_INVITES=" canada : mRng6VpbVNwzMDNh , barehash ")
    def test_invite_sources_parse_keys_and_hashes(self) -> None:
        sources = market_invite_sources()
        self.assertEqual(sources["canada"], "mRng6VpbVNwzMDNh")
        self.assertEqual(sources["invite-barehash"], "barehash")

    def test_ingest_accepts_posts_without_preview_html(self) -> None:
        posted_at = timezone.now()

        def fetch(_username: str, _before: int | None):
            return [
                {
                    "channel_username": "koolbarcanada-group",
                    "telegram_message_id": 7,
                    "posted_at": posted_at,
                    "text": "#مسافر\nمبدا : تهران\nمقصد : تورنتو\nقبول بار تا 10 کیلو",
                    "views": 3,
                    "has_photo": False,
                    "author_username": "traveler",
                    "author_name": "Ali",
                    "source_url": "https://t.me/c/99/7",
                }
            ]

        result = ingest_market_channel(
            username="koolbarcanada-group",
            fetch_page=fetch,
            head_pages=1,
            backfill_pages=0,
            days=1,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["created"], 1)
        post = MarketPost.objects.get()
        self.assertEqual(post.role, MarketRole.SUPPLY)
        self.assertEqual(post.author_username, "traveler")
        self.assertEqual(post.source_url, "https://t.me/c/99/7")

    @override_settings(
        OUTREACH_TG_API_ID=1,
        OUTREACH_TG_API_HASH="hash",
        OUTREACH_TG_SESSION="session",
        MARKET_CHANNEL_USERNAMES="koolbarcanada",
        MARKET_SOURCE_INVITES="koolbarcanada-group:mRng6VpbVNwzMDNh",
    )
    @patch("market.ingest.fetch_preview_page")
    @patch("market.ingest.HistoryReader")
    def test_daily_ingest_reads_private_groups_with_the_telegram_account(self, reader_cls, preview) -> None:
        reader = reader_cls.return_value
        posted_at = timezone.now()

        def page(username: str, _before: int | None):
            if username != "koolbarcanada-group":
                return []
            return [
                {
                    "channel_username": username,
                    "telegram_message_id": 42,
                    "posted_at": posted_at,
                    "text": "#مسافر\nمبدا : تهران\nمقصد : تورنتو",
                    "views": 1,
                    "has_photo": False,
                    "author_username": "traveler",
                    "author_name": "Ali",
                    "source_url": "https://t.me/c/1/42",
                }
            ]

        reader.page.side_effect = page
        result = ingest_all_market_channels(days=1)
        preview.assert_not_called()
        reader.open.assert_called_once()
        reader.close.assert_called_once()
        self.assertEqual(result["created"], 1)
        self.assertTrue(MarketPost.objects.filter(channel_username="koolbarcanada-group", telegram_message_id=42).exists())
