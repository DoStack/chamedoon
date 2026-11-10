"""Courier crawl sources live in Admin → Market crawl → Sources; the server settings rule while that table is empty."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User as StaffUser
from django.test import TestCase, override_settings
from django.utils import timezone

from market.admin import import_settings_sources
from market.ingest import RETIRED_MARKET_SOURCES, market_channel_usernames, market_ingest_usernames, market_source_names
from market.models import MarketIngestState, MarketSource, MarketSourceKind
from market.telegram_history import market_invite_sources

ENV = {
    "MARKET_CHANNEL_USERNAMES": "koolbarcanada,hilfen",
    "MARKET_SOURCE_INVITES": "koolbarcanada-group:mRngTEST,koolbar-qa:https://t.me/+DRnTEST",
}


@override_settings(**ENV)
class MarketSourceRulesTests(TestCase):
    def test_empty_table_reads_the_settings(self) -> None:
        self.assertEqual(market_channel_usernames(), ["koolbarcanada", "hilfen"])
        self.assertEqual(market_invite_sources(), {"koolbarcanada-group": "mRngTEST", "koolbar-qa": "DRnTEST"})

    def test_rows_win_over_the_settings(self) -> None:
        MarketSource.objects.create(username="@HamrahbarUSA")
        MarketSource.objects.create(username="hilfen", enabled=False)
        MarketSource.objects.create(username="gone", retired=True, enabled=False)
        MarketSource.objects.create(username="grp", kind=MarketSourceKind.GROUP, invite_hash="https://t.me/+ABC")
        MarketSource.objects.create(username="grp-no-invite", kind=MarketSourceKind.GROUP)
        self.assertEqual(market_channel_usernames(), ["HamrahbarUSA"])
        self.assertEqual(market_invite_sources(), {"grp": "ABC"})
        self.assertEqual(market_ingest_usernames(), ["HamrahbarUSA", "grp"])
        names = {name.lower() for name in market_source_names()}
        # Off and retired channels still mark an author as a source, as do the built-in retired names.
        self.assertTrue({"hamrahbarusa", "hilfen", "gone", *(n.lower() for n in RETIRED_MARKET_SOURCES)} <= names)

    def test_import_copies_settings_once_and_keeps_edits(self) -> None:
        created = import_settings_sources()
        self.assertEqual(created, 4 + len(RETIRED_MARKET_SOURCES))
        MarketSource.objects.filter(username="hilfen").update(enabled=False)
        self.assertEqual(import_settings_sources(), 0)
        self.assertFalse(MarketSource.objects.get(username="hilfen").enabled)
        self.assertEqual(market_channel_usernames(), ["koolbarcanada"])
        self.assertEqual(market_invite_sources(), {"koolbar-qa": "DRnTEST", "koolbarcanada-group": "mRngTEST"})
        self.assertTrue(MarketSource.objects.get(username=RETIRED_MARKET_SOURCES[0]).retired)


@override_settings(**ENV)
class MarketSourceAdminTests(TestCase):
    def setUp(self) -> None:
        StaffUser.objects.create_superuser("ops", "ops@example.com", "ops-pass")
        self.client.login(username="ops", password="ops-pass")

    def test_empty_list_says_the_settings_are_in_use_and_import_fills_it(self) -> None:
        text = self.client.get("/admin/market/marketsource/").content.decode()
        self.assertIn("No rows yet", text)
        self.assertIn("koolbarcanada", text)
        self.assertNotIn("mRngTEST", text)  # invite hashes are not printed in the notice
        response = self.client.get("/admin/market/marketsource/import-from-settings/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(MarketSource.objects.filter(username="koolbar-qa", kind=MarketSourceKind.GROUP).exists())
        text = self.client.get("/admin/market/marketsource/").content.decode()
        self.assertNotIn("No rows yet", text)

    def test_list_shows_health_from_ingest_state(self) -> None:
        MarketSource.objects.create(username="koolbarcanada")
        MarketSource.objects.create(username="hilfen")
        MarketSource.objects.create(username="old")
        now = timezone.now()
        MarketIngestState.objects.create(channel_username="koolbarcanada", last_run_at=now, last_created=4)
        MarketIngestState.objects.create(channel_username="hilfen", last_run_at=now, last_error="ChannelPrivateError")
        MarketIngestState.objects.create(channel_username="old", last_run_at=now - timedelta(days=3))
        text = self.client.get("/admin/market/marketsource/").content.decode()
        for flag in ("ok", "error", "stale", "ChannelPrivateError"):
            self.assertIn(flag, text)
        self.assertEqual(self.client.get("/admin/market/marketingeststate/").status_code, 200)

    def test_bulk_off_and_sidebar_link(self) -> None:
        source = MarketSource.objects.create(username="koolbarcanada")
        self.client.post("/admin/market/marketsource/", {"action": "disable_selected", "_selected_action": [source.pk]})
        source.refresh_from_db()
        self.assertFalse(source.enabled)
        self.assertIn("/admin/market/marketsource/", self.client.get("/admin/").content.decode())
