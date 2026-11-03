"""Back office v3 behind `HomeSettings.admin_v3`: hidden and refused with the flag off, read-only data with it on."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.contrib.auth.models import User as StaffUser
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from ai.openrouter import ChatResult
from home import admin_v3
from home.models import HomeArea, HomeBotMessage, HomeChannel, HomeSearchQuery, HomeSettings

USER = 5550001


def flag(on: bool = True, **extra) -> HomeSettings:
    row, _ = HomeSettings.objects.update_or_create(pk=1, defaults={"admin_v3": on, **extra})
    return row


def toronto() -> HomeChannel:
    row, _ = HomeChannel.objects.update_or_create(country="CA", city="Toronto", defaults={"username": "chamedoon_home_toronto", "enabled": True})
    HomeArea.objects.update_or_create(country="CA", city="Toronto", canonical="north york", defaults={"fa": "نورث یورک"})
    return row


def query(text: str = "room near Pearson under 1000", **values) -> HomeSearchQuery:
    defaults = {
        "telegram_user_id": USER, "raw_text": text, "country": "CA", "city": "Toronto", "parser": "gpt-5-mini", "confidence": Decimal("0.62"),
        "field_confidence": {"area": 0.38, "city": 0.97}, "parsed": {"types": ["room"], "max": 1000, "unmapped_areas": ["Pearson"]},
        "results_count": 0, "latency_ms": 2110, "status": "zero",
    }
    return HomeSearchQuery.objects.create(**{**defaults, **values})


class PagesCase(TestCase):
    def setUp(self) -> None:
        StaffUser.objects.create_superuser("ops", "ops@example.com", "ops-pass")
        self.client.login(username="ops", password="ops-pass")


class FlagOff(PagesCase):
    def setUp(self) -> None:
        super().setUp()
        flag(False)
        self.row = query()
        self.message = HomeBotMessage.objects.create(telegram_user_id=USER, direction="in", kind="text", text="سلام")

    def test_the_flag_defaults_to_off(self) -> None:
        HomeSettings.objects.all().delete()
        self.assertFalse(HomeSettings.load().admin_v3)

    def test_the_custom_pages_are_not_there(self) -> None:
        for url in (
            reverse("admin:home_ai_search"),
            reverse("admin:home_query_replay", args=[self.row.pk]),
            reverse("admin:home_query_export"),
            reverse("admin:home_conversation", args=[USER]),
        ):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_the_model_pages_refuse(self) -> None:
        for url in (reverse("admin:home_homesearchquery_changelist"), reverse("admin:home_homebotmessage_changelist")):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_the_sidebar_and_the_index_do_not_show_them(self) -> None:
        page = self.client.get(reverse("admin:index")).content.decode()
        self.assertNotIn(reverse("admin:home_ai_search"), page)
        self.assertNotIn(reverse("admin:home_homesearchquery_changelist"), page)
        self.assertNotIn(reverse("admin:home_homebotmessage_changelist"), page)


class Access(TestCase):
    def setUp(self) -> None:
        flag()
        self.url = reverse("admin:home_ai_search")

    def test_anonymous_goes_to_login(self) -> None:
        self.assertEqual(Client().get(self.url).status_code, 302)

    def test_staff_without_the_permission_is_refused(self) -> None:
        StaffUser.objects.create_user("staff", password="p", is_staff=True)
        client = Client()
        client.login(username="staff", password="p")
        self.assertEqual(client.get(self.url).status_code, 403)
        self.assertEqual(client.get(reverse("admin:home_query_export")).status_code, 403)
        self.assertEqual(client.get(reverse("admin:home_conversation", args=[USER])).status_code, 403)

    def test_viewing_does_not_allow_changing_settings(self) -> None:
        user = StaffUser.objects.create_user("viewer", password="p", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_homesearchquery"))
        client = Client()
        client.login(username="viewer", password="p")
        self.assertEqual(client.get(self.url).status_code, 200)
        self.assertEqual(client.post(self.url, {"action": "stop"}).status_code, 403)
        self.assertFalse(HomeSettings.load().ai_search_emergency_stop)


class SearchQueries(PagesCase):
    def setUp(self) -> None:
        super().setUp()
        flag()
        toronto()
        self.url = reverse("admin:home_homesearchquery_changelist")

    def test_the_list_shows_the_board_columns(self) -> None:
        query()
        query("اتاق نورث یورک", parser="regex", confidence=Decimal("0.91"), results_count=7, status="ok", parsed={})
        page = self.client.get(self.url).content.decode()
        for word in ("room near Pearson under 1000", "اتاق نورث یورک", "gpt-5-mini", "regex", "Zero results", "0.62", "Replay"):
            self.assertIn(word, page)

    def test_filters_and_search(self) -> None:
        query("zero one")
        query("fine one", results_count=4, confidence=Decimal("0.9"), status="ok")
        for params, shown, hidden in (
            ("?zero=yes", "zero one", "fine one"),
            ("?low=no", "fine one", "zero one"),
            ("?low=yes", "zero one", "fine one"),
            ("?q=fine", "fine one", "zero one"),
        ):
            page = self.client.get(self.url + params).content.decode()
            self.assertIn(shown, page, params)
            self.assertNotIn(hidden, page, params)

    def test_a_query_cannot_be_added_or_changed(self) -> None:
        row = query()
        self.assertEqual(self.client.get(reverse("admin:home_homesearchquery_add")).status_code, 403)
        page = self.client.get(reverse("admin:home_homesearchquery_change", args=[row.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertIn("Pearson", page.content.decode())
        self.assertIn("0.38", page.content.decode())

    def test_text_people_typed_is_escaped(self) -> None:
        row = query("<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)", self.client.get(self.url).content.decode())
        self.assertNotIn("<script>alert(1)", self.client.get(reverse("admin:home_homesearchquery_change", args=[row.pk])).content.decode())

    def test_mark_reviewed(self) -> None:
        row = query()
        self.client.post(self.url, {"action": "mark_reviewed", "_selected_action": [row.pk]})
        row.refresh_from_db()
        self.assertIsNotNone(row.reviewed_at)

    def test_csv_cannot_carry_a_formula(self) -> None:
        query('=HYPERLINK("http://x")')
        body = self.client.get(reverse("admin:home_query_export")).content.decode()
        self.assertIn("'=HYPERLINK", body)
        self.assertEqual(admin_v3.csv_cell("-1"), "'-1")
        self.assertEqual(admin_v3.csv_cell(5), "5")


class Replay(PagesCase):
    def setUp(self) -> None:
        super().setUp()
        flag()
        toronto()
        self.row = query("اتاق نورث یورک زیر ۱۰۰۰")
        self.url = reverse("admin:home_query_replay", args=[self.row.pk])

    def test_the_simple_parser_shows_what_changed_and_saves_nothing(self) -> None:
        with patch("home.bot.call") as telegram, patch("home.botparse.review_completion") as model:
            page = self.client.post(self.url, {"model": "regex"}).content.decode()
        telegram.assert_not_called()
        model.assert_not_called()
        self.assertIn("rp-changed", page)
        self.assertIn("north york", page)
        fresh = HomeSearchQuery.objects.get(pk=self.row.pk)
        self.assertEqual(fresh.parsed, self.row.parsed)
        self.assertEqual(HomeSearchQuery.objects.count(), 1)

    def test_a_model_replay_calls_that_model(self) -> None:
        answer = '{"types":["room"],"max":1000,"areas":["North York"],"overall":0.9}'
        with patch("home.botparse.review_completion", return_value=ChatResult(ok=True, text=answer, model="gpt-5-nano")) as model:
            page = self.client.post(self.url, {"model": "gpt-5-nano"}).content.decode()
        self.assertEqual(model.call_args.kwargs["model"], "gpt-5-nano")
        self.assertIn("Replay: gpt-5-nano", page)

    def test_a_model_that_fails_says_so(self) -> None:
        with patch("home.botparse.review_completion", return_value=ChatResult(ok=False, error="boom")):
            page = self.client.post(self.url, {"model": "gpt-5-mini"}).content.decode()
        self.assertIn("The model did not answer", page)

    def test_a_city_without_a_channel_says_so(self) -> None:
        self.row.city = "Atlantis"
        self.row.save()
        self.assertIn("This city has no channel now.", self.client.post(self.url, {"model": "regex"}).content.decode())

    def test_an_unknown_model_is_refused(self) -> None:
        self.assertEqual(self.client.post(self.url, {"model": "made-up"}).status_code, 404)

    def test_replay_selected_uses_the_free_parser(self) -> None:
        with patch("home.botparse.review_completion") as model:
            self.client.post(reverse("admin:home_homesearchquery_changelist"), {"action": "replay_selected", "_selected_action": [self.row.pk]})
        model.assert_not_called()


class Dashboard(PagesCase):
    def setUp(self) -> None:
        super().setUp()
        flag()
        toronto()
        self.url = reverse("admin:home_ai_search")

    def test_an_empty_page_reads(self) -> None:
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        text = page.content.decode()
        self.assertIn("No queries in this period.", text)
        self.assertIn("None in this period.", text)
        self.assertIn("not recorded yet", text)

    def test_the_numbers(self) -> None:
        query()
        query(results_count=5, status="ok", parsed={}, confidence=Decimal("0.90"), latency_ms=100)
        query(results_count=5, status="fallback", parser="regex", parsed={}, confidence=Decimal("0.70"), latency_ms=50, field_confidence={})
        old = query(results_count=5, status="ok", parsed={})
        HomeSearchQuery.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=1, hours=1))
        context = self.client.get(self.url).context
        self.assertEqual(context["kpis"]["total"], 3)
        self.assertEqual(context["kpis"]["zero_rate"], 33.3)
        self.assertEqual(context["kpis"]["fallback_rate"], 33.3)
        self.assertEqual(context["kpis"]["p95"], 2110)
        self.assertEqual(sum(bar["count"] for bar in context["bars"]), 4)
        self.assertEqual(len(context["bars"]), 14)
        self.assertEqual({row["model"] for row in context["models"]}, {"gpt-5-mini", "regex"})
        self.assertEqual({row["name"] for row in context["fields"]}, {"area", "city"})
        self.assertEqual(context["unmapped"][0]["raw"], "Pearson")

    def test_a_longer_range_reaches_further_back(self) -> None:
        old = query()
        HomeSearchQuery.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=3))
        self.assertEqual(self.client.get(self.url).context["kpis"]["total"], 0)
        self.assertEqual(self.client.get(self.url + "?range=7").context["kpis"]["total"], 1)
        self.assertEqual(self.client.get(self.url + "?range=nonsense").context["range"], "today")

    def test_emergency_stop(self) -> None:
        flag(ai_search_enabled=True)
        self.client.post(self.url, {"action": "stop"})
        row = HomeSettings.load()
        self.assertTrue(row.ai_search_emergency_stop)
        self.assertTrue(row.ai_search_enabled)
        self.assertIn("Emergency stop is on", self.client.get(self.url).content.decode())

    def test_settings_save_and_bad_numbers_are_refused(self) -> None:
        values = {"ai_search_enabled": "on", "search_model": "gpt-5-nano", "ai_search_user_cap": 20, "ai_search_global_cap": 900, "ai_search_timeout_seconds": 4, "search_log_retention_days": 60}
        self.client.post(self.url, values)
        row = HomeSettings.load()
        self.assertEqual((row.search_model, row.ai_search_user_cap, row.ai_search_global_cap), ("gpt-5-nano", 20, 900))
        self.assertEqual(self.client.post(self.url, {**values, "ai_search_user_cap": "lots"}).status_code, 200)
        self.assertEqual(HomeSettings.load().ai_search_user_cap, 20)

    def test_map_to_area_adds_an_alias_once(self) -> None:
        area = HomeArea.objects.get(canonical="north york")
        for _ in range(2):
            self.client.post(self.url, {"action": "map", "raw": "نورت یورک", "area": area.pk})
        area.refresh_from_db()
        self.assertEqual(area.aliases.count("نورت یورک"), 1)

    def test_map_to_area_refuses_a_comma_and_a_missing_area(self) -> None:
        area = HomeArea.objects.get(canonical="north york")
        before = area.aliases
        self.client.post(self.url, {"action": "map", "raw": "a,b", "area": area.pk})
        self.client.post(self.url, {"action": "map", "raw": "x", "area": 0})
        area.refresh_from_db()
        self.assertEqual(area.aliases, before)


class BotMessageLog(PagesCase):
    def setUp(self) -> None:
        super().setUp()
        flag()
        self.url = reverse("admin:home_homebotmessage_changelist")
        self.linked = query()
        HomeBotMessage.objects.create(telegram_user_id=USER, direction="in", kind="text", text="room near Pearson", query=self.linked)
        HomeBotMessage.objects.create(telegram_user_id=USER, direction="out", kind="clarify", text="کدام محله؟", query=self.linked)
        HomeBotMessage.objects.create(telegram_user_id=77, direction="in", kind="command", text="/start")

    def test_the_list_and_its_filters(self) -> None:
        page = self.client.get(self.url).content.decode()
        for word in ("room near Pearson", "کدام محله؟", "/start", f"Query {self.linked.pk}"):
            self.assertIn(word, page)
        narrowed = self.client.get(self.url + "?direction__exact=out").content.decode()
        self.assertIn("کدام محله؟", narrowed)
        self.assertNotIn("/start", narrowed)
        kind = self.client.get(self.url + "?kind__exact=command").content.decode()
        self.assertIn("/start", kind)
        self.assertNotIn("room near Pearson", kind)
        search = self.client.get(self.url + "?q=77").content.decode()
        self.assertIn("/start", search)
        self.assertNotIn("room near Pearson", search)

    def test_the_conversation_of_one_person(self) -> None:
        page = self.client.get(reverse("admin:home_conversation", args=[USER])).content.decode()
        self.assertIn("room near Pearson", page)
        self.assertIn("کدام محله؟", page)
        self.assertNotIn("/start", page)

    def test_a_person_with_no_messages(self) -> None:
        self.assertIn("No messages from this person.", self.client.get(reverse("admin:home_conversation", args=[12345])).content.decode())

    def test_a_message_cannot_be_changed(self) -> None:
        row = HomeBotMessage.objects.first()
        response = self.client.post(reverse("admin:home_homebotmessage_change", args=[row.pk]), {"text": "changed"})
        self.assertEqual(response.status_code, 403)
        row.refresh_from_db()
        self.assertNotEqual(row.text, "changed")


class PercentileTests(TestCase):
    def test_percentile(self) -> None:
        self.assertIsNone(admin_v3.percentile([], 0.95))
        self.assertEqual(admin_v3.percentile([5], 0.95), 5)
        self.assertEqual(admin_v3.percentile(list(range(1, 101)), 0.95), 95)
