"""F08: rules, model handling, caps, cache, the states the server decides, and the mock's truth. Never calls a real model."""

from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.utils import timezone

from ai.openrouter import ChatResult
from aisearch import evaluation, llm, service
from aisearch.models import SearchCache, SearchDailyCounter
from aisearch.rules import Metro, extract, normalize
from aisearch.serializers import SearchParseSerializer
from aisearch.spec import SearchSpec, from_model
from home.area_seed import SEED
from home.models import HomeArea, HomeChannel, HomeListing, HomeSearchQuery, HomeSettings
from home.search import fits
from tests.helpers import TEST_SECRET, bearer_auth, make_user

TODAY = date(2026, 11, 3)
PARSE = "/api/v1/me/search/parse/"
RECENT = "/api/v1/me/search/recent/"
RESTORE = "/api/v1/me/search/recent/restore/"
FIXTURES = evaluation.DATA / "parse_fixtures.json"


def seed_areas() -> None:
    """As migration 0030 does, for a run without migrations."""
    for city, country, rows in SEED:
        for rank, (canonical, fa, group, lat, lng, aliases) in enumerate(rows):
            HomeArea.objects.get_or_create(country=country, city=city, canonical=canonical, defaults={"fa": fa, "group": group, "lat": lat, "lng": lng, "aliases": aliases, "rank": rank})


@override_settings(SECRET_KEY=TEST_SECRET)
class SearchCase(TestCase):
    def setUp(self) -> None:
        cache.clear()
        seed_areas()
        HomeChannel.objects.all().delete()
        for country, city in (("CA", "Toronto"), ("CA", "Montreal"), ("CA", "Vancouver"), ("DE", "Berlin"), ("DE", "Hamburg")):
            HomeChannel.objects.create(country=country, city=city, username=f"chamedoon_{city.lower()}", title=city)
        self.user = make_user(telegram_user_id=880077, first_name="Sara")
        self.auth = bearer_auth(self.user)
        patcher = patch("aisearch.service.timezone.localdate", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)
        HomeSettings.objects.update_or_create(pk=1, defaults={"miniapp_v2": True, "ai_search_enabled": True})

    def post(self, text: str, city: str = "Toronto", country: str = "CA", **extra):
        return self.client.post(PARSE, {"text": text, "country": country, "city": city, **extra}, content_type="application/json", **self.auth)

    def parse(self, text: str, city: str = "Toronto", country: str = "CA", **extra) -> dict:
        response = self.post(text, city, country, **extra)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def area_id(self, canonical: str, city: str = "Toronto") -> int:
        return HomeArea.objects.get(canonical=canonical, city=city).pk


def model_says(reply: object, tokens: int = 123):
    return patch("aisearch.llm.ask", return_value=llm.ModelResult(ok=True, data=reply, tokens=tokens))


def model_on():
    return patch("aisearch.llm.available", return_value=True)


class NormalizeTests(TestCase):
    def test_one_spelling_per_query(self) -> None:
        self.assertEqual(normalize("  ۲ خوابه‌ی  ۱٬۰۰۰ كيف  "), "2 خوابه ی 1000 کیف")
        self.assertEqual(normalize("٢ Bedroom, $2,000"), "2 bedroom 2000")

    def test_same_query_same_key(self) -> None:
        self.assertEqual(service.cache_key(normalize("۲ خوابه"), "CA:toronto"), service.cache_key(normalize(" 2  خوابه "), "CA:toronto"))
        self.assertNotEqual(service.cache_key("x", "CA:toronto"), service.cache_key("x", "DE:berlin"))


class EvaluationTests(SearchCase):
    def test_at_least_50_queries_and_the_report_is_the_committed_one(self) -> None:
        report, misses = evaluation.run(self.user.telegram_user_id)
        print(json.dumps(report, ensure_ascii=False))
        self.assertEqual(misses, [])
        self.assertGreaterEqual(report["queries"], 50)
        self.assertGreaterEqual(report["rule_share"], 0.8)
        if os.environ.get("AISEARCH_WRITE_REPORT"):
            evaluation.REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(report, json.loads(evaluation.REPORT.read_text(encoding="utf-8")))


class RuleTests(SearchCase):
    def rules(self, text: str, city: str = "Toronto", country: str = "CA"):
        here = HomeChannel.objects.get(city=city, country=country)
        return extract(normalize(text), Metro.load(here), TODAY)

    def test_a_date_past_this_year_is_next_year(self) -> None:
        self.assertEqual(self.rules("از ۱ مارس").spec.move, "2027-03-01")
        self.assertEqual(self.rules("from dec 25").spec.move, "2026-12-25")

    def test_an_impossible_date_is_not_a_date(self) -> None:
        self.assertEqual(self.rules("۳۱ نوامبر").spec.move, "")

    def test_a_word_that_could_be_a_month_needs_a_day(self) -> None:
        self.assertEqual(self.rules("may").spec.move, "")
        self.assertEqual(self.rules("may 5").spec.move, "2027-05-05")

    def test_a_small_number_is_not_a_price(self) -> None:
        spec = self.rules("۲ خوابه ۵۰").spec
        self.assertEqual((spec.min, spec.max), (None, None))

    def test_bedrooms_are_not_a_room(self) -> None:
        self.assertEqual(self.rules("۲ اتاق خواب").spec.types, ["bed2"])

    def test_three_and_more_share_the_tile(self) -> None:
        self.assertEqual(self.rules("۴ خوابه").spec.types, ["bed3"])

    def test_unfurnished_is_not_furnished(self) -> None:
        result = self.rules("بدون مبله")
        self.assertFalse(result.spec.furnished)
        self.assertEqual(result.ignored, ["بدون مبله"])

    def test_a_filter_of_the_other_country_is_said_not_applied(self) -> None:
        result = self.rules("با ملد")
        self.assertFalse(result.spec.reg)
        self.assertTrue(result.ignored)
        self.assertTrue(self.rules("با ملد", "Berlin", "DE").spec.reg)

    def test_a_chip_shows_the_intersection_as_typed(self) -> None:
        self.assertEqual(self.rules("Yonge و Finch").labels[0]["label"], "Yonge و Finch")
        self.assertEqual(self.rules("north york").labels[0]["label"], "نورث یورک")

    def test_a_street_alone_does_not_place_it_downtown(self) -> None:
        self.assertEqual(self.rules("queen st").spec.area_ids, [])

    def test_its_own_city_adds_nothing(self) -> None:
        result = self.rules("اتاق تورنتو")
        self.assertIsNone(result.other_city)
        self.assertEqual(result.leftover, [])


class SpecTests(SearchCase):
    def reply(self, data: object, country: str = "CA"):
        return from_model(data, country=country, today=TODAY, text="room near x")

    def test_anything_but_an_object_is_none(self) -> None:
        for bad in ([], "room", 3, None):
            self.assertIsNone(self.reply(bad))

    def test_wrong_values_are_dropped_and_the_rest_kept(self) -> None:
        parsed = self.reply({"types": ["room", "castle", "room"], "min": "free", "max": 1200.0, "gender": "robot", "term": 1, "furnished": "yes", "bills": True, "reg": True, "q": "x", "move": "not a date", "by": "2020-01-01", "extra": 1})
        spec = parsed.spec
        self.assertEqual((spec.types, spec.min, spec.max, spec.gender, spec.term, spec.furnished, spec.move, spec.by, spec.q), (["room"], None, 1200, "", "", False, "", "", ""))
        self.assertTrue(spec.bills)
        self.assertFalse(spec.reg, "Germany only")

    def test_out_of_range_numbers_and_swapped_bounds(self) -> None:
        spec = self.reply({"min": 2000, "max": 1000, "size_min": 99999}).spec
        self.assertEqual((spec.min, spec.max, spec.size_min), (1000, 2000, None))
        self.assertIsNone(self.reply({"min": True}).spec.min)
        self.assertIsNone(self.reply({"max": 10**9}).spec.max)

    def test_ignored_holds_only_what_was_typed(self) -> None:
        self.assertEqual(self.reply({"ignored": ["room", "<b>injected</b>", 5]}).ignored, ["room"])

    def test_alert_matcher_reads_the_same_filters(self) -> None:
        spec = SearchSpec(types=["room"], max=1000, furnished=True, gender="female", move="2026-12-01")
        search = spec.to_alert(["north york"])
        self.assertEqual(search["areas"], ["north york"])
        self.assertEqual(search["max"], 1000)
        listing = HomeListing(furnished="yes", roommate_gender="female", available_from=date(2026, 12, 3))
        self.assertTrue(fits(search, listing))
        listing.roommate_gender = "male"
        self.assertFalse(fits(search, listing))


class ModelCallTests(SearchCase):
    """The real `llm.ask` against a stubbed gateway."""

    def test_limit_is_six_seconds(self) -> None:
        self.assertEqual(llm.LIMIT_SECONDS, 6.0)

    def test_a_hung_gateway_comes_back_at_the_limit(self) -> None:
        def hang(*args, **kwargs):
            time.sleep(0.4)
            return ChatResult(ok=True, text="{}")

        with patch("aisearch.llm.LIMIT_SECONDS", 0.05), patch("aisearch.llm.openrouter_chat", hang), patch("aisearch.llm.free_models", return_value=("a",)):
            started = time.monotonic()
            result = llm.ask("x", today=TODAY, country="CA")
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertEqual((result.ok, result.error), (False, "timeout"))

    def test_next_model_after_a_failure_and_usage_counts(self) -> None:
        replies = [ChatResult(ok=False, error="429"), ChatResult(ok=True, text='sure: {"types":["room"]} ok', raw={"usage": {"total_tokens": 77}})]
        with patch("aisearch.llm.openrouter_chat", side_effect=replies), patch("aisearch.llm.free_models", return_value=("a", "b")):
            result = llm.ask("x", today=TODAY, country="CA")
        self.assertEqual((result.ok, result.data, result.tokens), (True, {"types": ["room"]}, 77))

    def test_bad_json_and_all_failing(self) -> None:
        with patch("aisearch.llm.openrouter_chat", return_value=ChatResult(ok=True, text="no json {here")), patch("aisearch.llm.free_models", return_value=("a",)):
            self.assertEqual(llm.ask("x", today=TODAY, country="CA").error, "bad_json")
        with patch("aisearch.llm.openrouter_chat", return_value=ChatResult(ok=False, error="down")), patch("aisearch.llm.free_models", return_value=("a", "b")):
            result = llm.ask("x", today=TODAY, country="CA")
        self.assertEqual(result.error, "failed")
        self.assertGreater(result.tokens, 0)

    def test_the_text_goes_as_data_and_the_reply_is_short(self) -> None:
        seen = {}

        def capture(messages, **kwargs):
            seen.update(kwargs, messages=messages)
            return ChatResult(ok=True, text="{}")

        with patch("aisearch.llm.openrouter_chat", capture), patch("aisearch.llm.free_models", return_value=("a",)):
            llm.ask('x"}', today=TODAY, country="CA")
        self.assertEqual(json.loads(seen["messages"][1]["content"])["text"], 'x"}')
        self.assertEqual(seen["max_tokens"], llm.MAX_TOKENS)
        self.assertEqual(seen["temperature"], 0)


class FlowTests(SearchCase):
    """Every state the server decides, by the design's own example where it has one, and how each is reached."""

    def assert_no_model(self):
        return patch("aisearch.llm.ask", side_effect=AssertionError("the rules should have answered"))

    def test_state_4_parsed_with_rules_alone(self) -> None:
        with model_on(), self.assert_no_model():
            answer = self.parse("۲ خوابه ۸۰ متر فینچ ۲۰۰۰")
        self.assertEqual((answer["status"], answer["source"]), ("parsed", "rules"))
        self.assertEqual(answer["spec"]["types"], ["bed2"])
        self.assertEqual((answer["spec"]["min"], answer["spec"]["max"]), (1800, 2200))
        self.assertEqual((answer["spec"]["size_min"], answer["spec"]["size_max"]), (70, 90))
        self.assertEqual(answer["spec"]["area_ids"], [self.area_id("north york")])
        self.assertEqual(answer["area_labels"][0]["label"], "فینچ")

    def test_state_6_unknown_area_offers_near_names(self) -> None:
        with model_on(), self.assert_no_model():
            answer = self.parse("اتاق نزدیک پیرسون تا ۱۰۰۰ مناسب خانم")
        self.assertEqual(answer["status"], "unknown_area")
        self.assertEqual(answer["unknown_area"]["text"], "پیرسون")
        self.assertEqual(len(answer["unknown_area"]["suggestions"]), 3)
        self.assertEqual((answer["spec"]["types"], answer["spec"]["max"], answer["spec"]["gender"]), (["room"], 1000, "female"))

    def test_state_7_no_city_asks_and_spends_nothing(self) -> None:
        with model_on(), self.assert_no_model():
            answer = self.parse("۱ خوابه تا ۲۰۰۰ از ۱ دسامبر چیز عجیب", city="", country="")
        self.assertEqual(answer["status"], "no_city")
        self.assertEqual((answer["spec"]["max"], answer["spec"]["move"]), (2000, "2026-12-01"))
        self.assertFalse(SearchCache.objects.exists())

    def test_state_7_an_unknown_or_disabled_city_is_no_city(self) -> None:
        self.assertEqual(self.parse("اتاق", city="Atlantis")["status"], "no_city")
        HomeChannel.objects.filter(city="Hamburg").update(enabled=False)
        self.assertEqual(self.parse("اتاق", city="Hamburg", country="DE")["status"], "no_city")

    def test_state_8_simple_when_the_model_is_off(self) -> None:
        HomeSettings.objects.update(ai_search_emergency_stop=True)
        with self.assert_no_model():
            answer = self.parse("اتاق نورث یورک چیز عجیب")
        self.assertEqual((answer["status"], answer["source"]), ("simple", "regex"))
        self.assertEqual(answer["ignored"], ["چیز عجیب"])
        self.assertEqual(answer["spec"]["types"], ["room"])

    def test_state_8_simple_on_every_kind_of_model_failure(self) -> None:
        failures = [
            llm.ModelResult(ok=False, error="timeout", tokens=9),
            llm.ModelResult(ok=False, error="bad_json", tokens=9),
            llm.ModelResult(ok=True, data=["not", "an", "object"], tokens=9),
        ]
        for index, failure in enumerate(failures):
            with self.subTest(failure=failure), model_on(), patch("aisearch.llm.ask", return_value=failure):
                answer = self.parse(f"اتاق چیز عجیب {'x' * (index + 2)}")
            self.assertEqual((answer["status"], answer["source"]), ("simple", "regex"))
            self.assertEqual(answer["spec"]["types"], ["room"])

    def test_state_8_a_wish_with_no_filter_needs_no_model(self) -> None:
        with model_on(), self.assert_no_model():
            answer = self.parse("اتاق با پارکینگ")
        self.assertEqual((answer["status"], answer["source"], answer["ignored"]), ("simple", "rules", ["پارکینگ"]))

    def test_state_9_capped_per_person_then_tomorrow_again(self) -> None:
        HomeSettings.objects.update(ai_search_user_cap=1)
        with model_on(), model_says({"types": ["room"]}) as ask:
            self.assertEqual(self.parse("چیز عجیب یک")["source"], "llm")
            answer = self.parse("چیز عجیب دو")
            self.assertEqual(ask.call_count, 1)
        self.assertEqual(answer["status"], "capped")
        self.assertEqual((answer["spec"], answer["area_labels"]), (SearchSpec().to_dict(), []))
        self.assertFalse(SearchCache.objects.filter(key=service.cache_key(normalize("چیز عجیب دو"), "CA:toronto")).exists())
        with patch("aisearch.service.timezone.localdate", return_value=TODAY + timedelta(days=1)), model_on(), model_says({"types": ["room"]}):
            self.assertEqual(self.parse("چیز عجیب دو")["status"], "parsed")

    def test_state_9_capped_for_everyone_and_gives_the_slot_back(self) -> None:
        HomeSettings.objects.update(ai_search_global_cap=0)
        with model_on(), self.assert_no_model():
            self.assertEqual(self.parse("چیز عجیب")["status"], "capped")
        self.assertEqual(SearchDailyCounter.objects.get(scope=f"user:{self.user.telegram_user_id}", day=TODAY).count, 0)

    def test_a_capped_person_still_gets_what_the_rules_answer_and_the_cache(self) -> None:
        with model_on(), model_says({"types": ["room"]}):
            self.parse("چیز عجیب")
        HomeSettings.objects.update(ai_search_user_cap=0)
        with model_on(), self.assert_no_model():
            self.assertEqual(self.parse("۲ خوابه")["status"], "parsed")
            self.assertEqual(self.parse("چیز عجیب")["status"], "parsed")

    def test_state_12_not_understood_with_no_model_call_for_a_greeting(self) -> None:
        with model_on(), self.assert_no_model():
            self.assertEqual(self.parse("سلام خوبی")["status"], "not_understood")

    def test_state_12_when_the_model_also_finds_nothing(self) -> None:
        with model_on(), model_says({}):
            answer = self.parse("نوبت دکتر")
        self.assertEqual((answer["status"], answer["source"]), ("not_understood", "llm"))

    def test_state_12_when_the_model_is_down_and_rules_found_nothing(self) -> None:
        with model_on(), patch("aisearch.llm.ask", return_value=llm.ModelResult(ok=False, error="failed")):
            self.assertEqual(self.parse("نوبت دکتر")["status"], "not_understood")

    def test_state_13_another_city_keeps_the_filters(self) -> None:
        answer = self.parse("۱ خوابه مونترال")
        self.assertEqual(answer["status"], "other_city")
        self.assertEqual(answer["other_city"], {"slug": "montreal", "name_fa": "مونترال", "country": "CA"})
        self.assertEqual(answer["spec"]["types"], ["bed1"])

    def test_state_13_from_an_area_of_another_city(self) -> None:
        self.assertEqual(self.parse("اتاق برنابی")["other_city"]["slug"], "vancouver")

    def test_state_13_without_a_city_names_it(self) -> None:
        self.assertEqual(self.parse("۱ خوابه مونترال", city="", country="")["status"], "other_city")

    def test_the_model_fills_gaps_and_the_rules_win(self) -> None:
        with model_on(), model_says({"types": ["studio"], "max": 5000, "furnished": True, "areas": ["north york", "جایی ناشناس"], "ignored": ["عجیب"]}):
            answer = self.parse("اتاق تا ۱۰۰۰ چیز عجیب")
        self.assertEqual((answer["spec"]["types"], answer["spec"]["max"], answer["spec"]["furnished"]), (["room"], 1000, True))
        self.assertEqual(answer["spec"]["area_ids"], [self.area_id("north york")])
        self.assertEqual(answer["unknown_area"]["text"], "جایی ناشناس")
        self.assertEqual(answer["status"], "unknown_area")

    def test_english_and_mixed(self) -> None:
        self.assertEqual(self.parse("2 bedroom north york 2000-2500")["status"], "parsed")
        self.assertEqual(self.parse("اتاق WG تا ۶۰۰ یورو", city="Berlin", country="DE")["spec"]["max"], 600)


class CacheTests(SearchCase):
    def test_one_model_call_per_normalized_query_and_no_token_on_a_hit(self) -> None:
        with model_on(), model_says({"types": ["room"]}, tokens=50) as ask:
            first = self.parse("چیز عجیب ۲")
            second = self.parse("  چیز   عجیب 2 ")
        self.assertEqual(ask.call_count, 1)
        self.assertEqual((first["cached"], second["cached"]), (False, True))
        rows = HomeSearchQuery.objects.order_by("pk")
        self.assertEqual([row.parsed["tokens"] for row in rows], [50, 0])

    def test_per_city(self) -> None:
        with model_on(), model_says({"types": ["room"]}) as ask:
            self.parse("چیز عجیب")
            self.parse("چیز عجیب", city="Montreal")
        self.assertEqual(ask.call_count, 2)

    def test_expired_is_asked_again(self) -> None:
        with model_on(), model_says({"types": ["room"]}) as ask:
            self.parse("چیز عجیب")
            SearchCache.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
            self.parse("چیز عجیب")
        self.assertEqual(ask.call_count, 2)

    def test_a_failure_waits_an_hour_a_date_a_day_and_an_answer_a_month(self) -> None:
        def ttl(text: str) -> timedelta:
            key = service.cache_key(normalize(text), "CA:toronto")
            return SearchCache.objects.get(key=key).expires_at - timezone.now()

        with model_on(), patch("aisearch.llm.ask", return_value=llm.ModelResult(ok=False, error="failed")):
            self.parse("چیز الف")
        self.assertLess(ttl("چیز الف"), timedelta(hours=1, seconds=5))
        with model_on(), model_says({"move": "2026-12-22"}):
            self.parse("چیز ب")
        self.assertLess(ttl("چیز ب"), timedelta(days=1, seconds=5))
        with model_on(), model_says({"types": ["room"]}):
            self.parse("چیز ج")
        self.assertGreater(ttl("چیز ج"), timedelta(days=29))

    def test_a_broken_cache_table_does_not_fail_the_search(self) -> None:
        with patch("aisearch.service._cached", side_effect=DatabaseError("gone")), patch("aisearch.service._store", side_effect=DatabaseError("gone")):
            self.assertEqual(self.parse("۲ خوابه")["status"], "parsed")

    def test_broken_caps_mean_no_model_call(self) -> None:
        with model_on(), patch("aisearch.llm.ask", side_effect=AssertionError), patch("aisearch.service._within_caps", side_effect=DatabaseError("gone")):
            self.assertEqual(self.parse("چیز عجیب")["status"], "capped")

    def test_old_rows_go_once_a_day(self) -> None:
        SearchCache.objects.create(key="old", payload={}, expires_at=timezone.now() - timedelta(days=1))
        SearchDailyCounter.objects.create(scope="user:1", day=TODAY - timedelta(days=9), count=3)
        self.parse("۲ خوابه")
        self.assertFalse(SearchCache.objects.filter(key="old").exists())
        self.assertFalse(SearchDailyCounter.objects.filter(scope="user:1").exists())


class ApiTests(SearchCase):
    def test_off_by_default(self) -> None:
        HomeSettings.objects.update(ai_search_enabled=False)
        self.assertEqual(self.post("اتاق").json()["code"], "feature_off")
        self.assertEqual(self.client.get(RECENT, **self.auth).status_code, 403)
        self.assertIs(self.client.get("/api/v1/public/meta/").json()["text_search_on"], False)

    def test_meta_says_on(self) -> None:
        self.assertIs(self.client.get("/api/v1/public/meta/").json()["text_search_on"], True)

    def test_the_flag_is_off_on_a_new_settings_row(self) -> None:
        self.assertFalse(HomeSettings().ai_search_enabled)

    def test_emergency_stop_keeps_the_rules_and_stops_the_model(self) -> None:
        HomeSettings.objects.update(ai_search_emergency_stop=True)
        with model_on(), patch("aisearch.llm.ask", side_effect=AssertionError):
            self.assertEqual(self.parse("۲ خوابه")["status"], "parsed")
            self.assertEqual(self.parse("اتاق چیز عجیب")["source"], "regex")

    def test_the_admin_timeout_can_only_shorten_the_limit(self) -> None:
        HomeSettings.objects.update(ai_search_timeout_seconds=3)
        with model_on(), model_says({"types": ["room"]}) as ask:
            self.parse("چیز عجیب")
        self.assertEqual(ask.call_args.kwargs["limit"], 3)
        with patch("aisearch.llm.openrouter_chat", return_value=ChatResult(ok=True, text="{}")), patch("aisearch.llm.free_models", return_value=("a",)), patch("aisearch.llm.threading.Thread.join") as join:
            llm.ask("x", today=TODAY, country="CA", limit=60)
        self.assertEqual(join.call_args.args[0], 6.0)

    def test_the_admin_model_goes_first(self) -> None:
        with patch("aisearch.llm.openrouter_chat", return_value=ChatResult(ok=True, text="{}")) as chat, patch("aisearch.llm.free_models", return_value=("free",)):
            llm.ask("x", today=TODAY, country="CA", model="chosen")
        self.assertEqual(chat.call_args.kwargs["model"], "chosen")

    def test_the_log_is_the_shared_table_and_the_bots_rows_stay_out_of_recents(self) -> None:
        HomeSearchQuery.objects.create(telegram_user_id=self.user.telegram_user_id, raw_text="از بات", parsed={"bedrooms": 1})
        self.parse("اتاق")
        row = HomeSearchQuery.objects.filter(raw_text="اتاق").get()
        self.assertEqual((row.telegram_user_id, row.parser, row.status), (self.user.telegram_user_id, "rules", "ok"))
        self.assertEqual(row.parsed["status"], "parsed")
        self.assertIsNotNone(row.latency_ms)
        self.assertEqual([item["text"] for item in self.client.get(RECENT, **self.auth).json()["results"]], ["اتاق"])

    def test_a_broken_mark_table_still_shows_recents(self) -> None:
        self.parse("اتاق")
        with patch("aisearch.service.SearchRecentsMark.objects.filter", side_effect=DatabaseError("gone")):
            self.assertEqual(len(self.client.get(RECENT, **self.auth).json()["results"]), 1)

    def test_auth_and_validation(self) -> None:
        self.assertEqual(self.client.post(PARSE, {"text": "x"}, content_type="application/json").json()["code"], "auth_required")
        self.assertEqual(self.post("").json()["code"], "validation_failed")
        self.assertEqual(self.post("x" * 201).json()["fields"]["text"], ["max_length"])

    def test_rate_limited(self) -> None:
        from aisearch.views import SearchThrottle

        with patch.object(SearchThrottle, "rate", "1/min"), patch.object(SearchThrottle, "THROTTLE_RATES", {"home_search": "1/min"}):
            self.assertEqual(self.post("اتاق").status_code, 200)
            self.assertEqual(self.post("اتاق").json()["code"], "rate_limited")

    def test_rules_only_spends_and_stores_nothing(self) -> None:
        with model_on(), patch("aisearch.llm.ask", side_effect=AssertionError):
            answer = self.parse("اتاق چیز عجیب", rules_only=True)
        self.assertEqual(answer["spec"]["types"], ["room"])
        self.assertFalse(HomeSearchQuery.objects.exists())
        self.assertFalse(SearchCache.objects.exists())
        self.assertFalse(SearchDailyCounter.objects.exists())

    def test_recents_latest_first_one_per_text_only_what_found_something(self) -> None:
        self.parse("۱ خوابه")
        self.parse("سلام")
        self.parse("اتاق")
        self.parse("1  خوابه")
        rows = self.client.get(RECENT, **self.auth).json()["results"]
        self.assertEqual([row["text"] for row in rows], ["1  خوابه", "اتاق"])
        self.assertEqual(rows[1]["spec"]["types"], ["room"])

    def test_recents_keep_five_and_are_private(self) -> None:
        for text in ("۱ خوابه", "۲ خوابه", "۳ خوابه", "اتاق", "استودیو", "بیسمنت"):
            self.parse(text)
        self.assertEqual(len(self.client.get(RECENT, **self.auth).json()["results"]), 5)
        other = make_user(telegram_user_id=880078, first_name="Ali")
        self.assertEqual(self.client.get(RECENT, **bearer_auth(other)).json()["results"], [])

    def test_state_11_clear_and_undo_bring_back_that_set(self) -> None:
        self.parse("اتاق")
        stamp = self.client.delete(RECENT, **self.auth).json()["cleared_at"]
        self.assertEqual(self.client.get(RECENT, **self.auth).json()["results"], [])
        self.parse("۲ خوابه")
        for _ in range(2):
            restored = self.client.post(RESTORE, {"cleared_at": stamp}, content_type="application/json", **self.auth)
            self.assertEqual([row["text"] for row in restored.json()["results"]], ["۲ خوابه", "اتاق"])
        self.assertEqual(self.client.post(RESTORE, {"cleared_at": "soon"}, content_type="application/json", **self.auth).json()["code"], "validation_failed")


# What the frontend's mock answers (frontend/src/mocks/api.ts), one per state the server decides. Ids are the mock's.
MOCK_IDS = {"north york": 9001, "richmond hill": 9002, "downtown": 9003}
MOCK_CASES = {
    "parsed": {"text": "۱ خوابه ۵۵ متر فینچ ۱۹۰۰", "city": "Toronto", "country": "CA"},
    "nothing": {"text": "استودیو داون تاون تا ۹۰۰", "city": "Toronto", "country": "CA"},
    "unknown_area": {"text": "اتاق نزدیک پیرسون تا ۱۰۰۰ مناسب خانم", "city": "Toronto", "country": "CA"},
    "no_city": {"text": "۱ خوابه تا ۲۰۰۰ از ۱ دسامبر", "city": "", "country": ""},
    "simple": {"text": "اتاق نورث یورک چیز عجیب", "city": "Toronto", "country": "CA"},
    "capped": {"text": "۱ خوابه مبله چیز عجیب", "city": "Toronto", "country": "CA", "user_daily_llm": 0},
    "not_understood": {"text": "سلام خوبی", "city": "Toronto", "country": "CA"},
    "other_city": {"text": "۱ خوابه مونترال", "city": "Toronto", "country": "CA"},
}


class MockFixtureTests(SearchCase):
    """The mock answers from parse_fixtures.json. Each answer must be what this server says to its request, ids aside."""

    def answer(self, case: dict) -> dict:
        SearchCache.objects.all().delete()
        HomeSettings.objects.update(ai_search_user_cap=case.get("user_daily_llm", 20))
        with model_on(), patch("aisearch.llm.ask", return_value=llm.ModelResult(ok=False, error="failed")):
            answer = self.parse(case["text"], city=case["city"], country=case["country"])
        # A person's own run ids differ by database, so areas go by their place in the mock's table.
        ids = {area.pk: MOCK_IDS.get(area.canonical, 9100 + index) for index, area in enumerate(HomeArea.objects.order_by("pk"))}
        answer["spec"]["area_ids"] = [ids[pk] for pk in answer["spec"]["area_ids"]]
        for row in answer["area_labels"]:
            row["id"] = ids[row["id"]]
        if answer["unknown_area"]:
            for row in answer["unknown_area"]["suggestions"]:
                row["id"] = ids[row["id"]]
        return answer

    def test_each_answer_is_what_the_server_says(self) -> None:
        got = {name: {"request": {key: case[key] for key in ("text", "city", "country")}, "response": self.answer(case)} for name, case in MOCK_CASES.items()}
        if os.environ.get("AISEARCH_WRITE_REPORT"):
            FIXTURES.write_text(json.dumps(got, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(got, json.loads(FIXTURES.read_text(encoding="utf-8")))
        for name, row in got.items():
            with self.subTest(name=name):
                SearchParseSerializer(data=row["response"]).is_valid(raise_exception=True)
                self.assertEqual(row["response"]["status"], "parsed" if name == "nothing" else name)
