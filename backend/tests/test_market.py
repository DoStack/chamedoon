from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from item_requests.models import ChannelStatus, ItemRequest, RequestStatus, RequestType
from item_requests.seed import seed_catalog
from market.classify import classify_role, extract_route, extract_stops
from market.ingest import clamp_lookback_days, ingest_market_channel, market_channel_usernames
from market.migrate import migrate_market_posts
from market.models import MarketPost, MarketRole
from market.parse import parse_preview_html, parse_views
from tests.helpers import TEST_SECRET, bearer_auth, make_user

FIXTURE = Path(__file__).parent / "fixtures" / "channel_preview.html"
CRON_SECRET = "cron-test-secret"


def supply_preview_html(username: str = "koolbar_international") -> str:
    stamp = (timezone.now() - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="{username}/7001">
    <div class="tgme_widget_message_text js-message_text" dir="auto">#مسافر<br>مبدا : تهران<br>مقصد : تورنتو<br>قبول بار تا 10 کیلو</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">1.2K</span>
      <a class="tgme_widget_message_date" href="https://t.me/{username}/7001"><time datetime="{stamp}">04:26</time></a>
    </div>
  </div>
</div>
"""


def hanover_preview_html(username: str = "koolbar_international") -> str:
    stamp = "2026-09-10T08:00:00+00:00"
    return f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="{username}/7010">
    <div class="tgme_widget_message_text js-message_text" dir="auto">پرواز تهران به هانوفر<br>بیستم سپتامبر<br>بار قابل رویت+ مدارک<br><br>@Kh_8758</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">40</span>
      <a class="tgme_widget_message_date" href="https://t.me/{username}/7010"><time datetime="{stamp}">08:00</time></a>
    </div>
  </div>
</div>
"""


def vitamin_preview_html(username: str = "koolbar_international") -> str:
    stamp = (timezone.now() - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="{username}/7011">
    <div class="tgme_widget_message_text js-message_text" dir="auto">لندن به تهران کسی هست دو سه بسته قرص ویتامین بچه ببره؟  هنوز خریداری نشده، برای اطمینان خودتون هم می‌تونید تهیه کنید.<br><br>@n_ii_ss</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">80</span>
      <a class="tgme_widget_message_date" href="https://t.me/{username}/7011"><time datetime="{stamp}">16:43</time></a>
    </div>
  </div>
</div>
"""


def mashhad_istanbul_preview_html(username: str = "koolbar_international") -> str:
    stamp = "2026-08-26T12:00:00+00:00"
    return f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="{username}/7016">
    <div class="tgme_widget_message_text js-message_text" dir="auto">تاریخ پرواز : ۲۷ آگوست<br>پرواز با ترکیش<br>@amirzz9122<br>توضیحات : کالاهای مجاز و مدارک و خرید داروهای مجاز<br>پرواز از مشهد و تهران به استانبول و سپس تورنتو</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">30</span>
      <a class="tgme_widget_message_date" href="https://t.me/{username}/7016"><time datetime="{stamp}">12:00</time></a>
    </div>
  </div>
</div>
"""


def augsburg_preview_html(username: str = "koolbar_international") -> str:
    stamp = (timezone.now() - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="{username}/7014">
    <div class="tgme_widget_message_text js-message_text" dir="auto">قبول بار از ایران (اصفهان) به آلمان (آگزبورگ)<br>تاریخ پرواز بین ۵ تا ۱۰ سپتامبر<br>هزینه توافقی<br><br>@Mj_rafal</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">20</span>
      <a class="tgme_widget_message_date" href="https://t.me/{username}/7014"><time datetime="{stamp}">14:00</time></a>
    </div>
  </div>
</div>
"""


def flight_group_preview_html(username: str = "koolbarcanada") -> str:
    stamp = (timezone.now() - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="{username}/25801">
    <div class="tgme_widget_message_text js-message_text" dir="auto">✈️ گروه خرید و رزرو بلیط هواپیما و هتل<br><br>🌐 لینک گروه:<br>@FlightAbroad</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">12</span>
      <a class="tgme_widget_message_date" href="https://t.me/{username}/25801"><time datetime="{stamp}">05:58</time></a>
    </div>
  </div>
</div>
"""


def recent_preview_html(username: str = "koolbar_international") -> str:
    stamp = (timezone.now() - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return (
        FIXTURE.read_text(encoding="utf-8")
        .replace("koolbar_international", username)
        .replace("2026-08-27T04:26:35+00:00", stamp)
        .replace("2026-08-27T05:00:00+00:00", stamp)
    )


class MarketParseTests(TestCase):
    def test_parse_preview_extracts_posts_and_views(self) -> None:
        posts = parse_preview_html(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual([item["telegram_message_id"] for item in posts], [7001, 7002])
        self.assertEqual(posts[0]["views"], 1200)
        self.assertIn("تورنتو", posts[0]["text"])
        self.assertEqual(posts[0]["channel_username"], "koolbar_international")

    def test_parse_views_suffixes(self) -> None:
        self.assertEqual(parse_views("132"), 132)
        self.assertEqual(parse_views("1.2K"), 1200)

    def test_parse_inline_contact_button(self) -> None:
        stamp = (timezone.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        html = f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="koolbar_international/7307">
    <div class="tgme_widget_message_author accent_color">
      <a class="tgme_widget_message_owner_name" href="https://t.me/koolbar_international"><span>ارسال بار به سراسر دنیا</span></a>
    </div>
    <div class="tgme_widget_message_text js-message_text" dir="auto">#خریدار_بار رم به تهران</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">7</span>
      <a class="tgme_widget_message_date" href="https://t.me/koolbar_international/7307"><time datetime="{stamp}">12:00</time></a>
    </div>
  </div>
  <div class="tgme_widget_message_inline_keyboard">
    <div class="tgme_widget_message_inline_row">
      <a class="tgme_widget_message_inline_button url_button" href="https://t.me/Saraaghyani">
        <span class="tgme_widget_message_inline_button_text">@Saraaghyani</span>
      </a>
    </div>
  </div>
</div>
"""
        posts = parse_preview_html(html)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["author_username"], "Saraaghyani")
        self.assertEqual(posts[0]["channel_username"], "koolbar_international")


class MarketClassifyTests(TestCase):
    def test_supply_route_and_demand_route(self) -> None:
        supply = "#مسافر\nمبدا : تهران\nمقصد : تورنتو\nقبول بار تا 10 کیلو"
        demand = "مسافر نیستم\nمبدا: ونکوور\nمقصد : تهران\nحدود 10 کیلو لباس"
        self.assertEqual(classify_role(supply), MarketRole.SUPPLY)
        self.assertEqual(classify_role(demand), MarketRole.DEMAND)
        origin, dest = extract_route(supply)
        self.assertEqual(origin, {"city": "Tehran", "country": "IR"})
        self.assertEqual(dest, {"city": "Toronto", "country": "CA"})

    def test_daram_is_not_rome(self) -> None:
        origin, dest = extract_route("بار دارم از ونکوور به تهران")
        self.assertEqual(origin, {"city": "Vancouver", "country": "CA"})
        self.assertEqual(dest, {"city": "Tehran", "country": "IR"})

    def test_informal_carry_request_is_demand(self) -> None:
        text = (
            "لندن به تهران کسی هست دو سه بسته قرص ویتامین بچه ببره؟  "
            "هنوز خریداری نشده، برای اطمینان خودتون هم می‌تونید تهیه کنید.\n\n@n_ii_ss"
        )
        self.assertEqual(classify_role(text), MarketRole.DEMAND)
        origin, dest = extract_route(text)
        self.assertEqual(origin, {"city": "London", "country": "GB"})
        self.assertEqual(dest, {"city": "Tehran", "country": "IR"})

    def test_channel_promo_is_noise(self) -> None:
        ad = (
            "«کانال کولبر کانادا» با هدف ارائه ی اطلاعات مانند قوانین و مقررات گمرکی "
            "#لینک کانال تلگرام: https://t.me/koolbarcanada"
        )
        self.assertEqual(classify_role(ad), MarketRole.NOISE)

    def test_flight_group_invite_is_noise(self) -> None:
        text = (
            "✈️ گروه خرید و رزرو بلیط هواپیما و هتل\n"
            "🌐 لینک گروه:\n@FlightAbroad"
        )
        self.assertEqual(classify_role(text), MarketRole.NOISE)


class MarketDateTests(TestCase):
    def test_persian_ordinal_flight_window(self) -> None:
        from market.dates import parse_travel_date, supply_travel_window

        posted = datetime(2026, 9, 10, 8, 0, tzinfo=dt_timezone.utc)
        text = "پرواز تهران به هانوفر\nبیستم سپتامبر\nبار قابل رویت+ مدارک"
        self.assertEqual(parse_travel_date(text, posted_at=posted), date(2026, 9, 20))
        window = supply_travel_window(text, posted_at=posted, today=date(2026, 9, 10))
        self.assertEqual(window["flight_date"], date(2026, 9, 20))
        self.assertEqual(window["date_from"], date(2026, 9, 10))
        self.assertEqual(window["date_to"], date(2026, 9, 17))

    def test_missing_time_is_two_weeks_and_three_days_before_flight(self) -> None:
        from market.dates import supply_travel_window

        posted = datetime(2026, 9, 10, 8, 0, tzinfo=dt_timezone.utc)
        window = supply_travel_window(
            "پرواز تهران به هانوفر\nبار قابل رویت+ مدارک",
            posted_at=posted,
            today=date(2026, 9, 10),
        )
        self.assertEqual(window["flight_date"], date(2026, 9, 24))
        self.assertEqual(window["date_from"], date(2026, 9, 10))
        self.assertEqual(window["date_to"], date(2026, 9, 21))

    def test_past_flight_date_is_not_a_valid_window(self) -> None:
        from market.dates import is_past_travel_date, parse_travel_date, supply_travel_window

        posted = datetime(2026, 8, 26, 12, 0, tzinfo=dt_timezone.utc)
        text = "تاریخ پرواز : ۲۷ آگوست\nپرواز از مشهد به تورنتو"
        self.assertEqual(parse_travel_date(text, posted_at=posted), date(2026, 8, 27))
        self.assertTrue(is_past_travel_date(text, posted_at=posted, today=date(2026, 9, 10)))
        self.assertIsNone(supply_travel_window(text, posted_at=posted, today=date(2026, 9, 10)))

    def test_tehran_hanover_flight_is_supply(self) -> None:
        text = (
            "پرواز تهران به هانوفر\n"
            "بیستم سپتامبر\n"
            "بار قابل رویت+ مدارک\n"
            "@Kh_8758"
        )
        self.assertEqual(classify_role(text), MarketRole.SUPPLY)
        origin, dest = extract_route(text)
        self.assertEqual(origin, {"city": "Tehran", "country": "IR"})
        self.assertEqual(dest, {"city": "Hanover", "country": "DE"})

    def test_milan_tehran_mashhad_is_supply_with_stops(self) -> None:
        text = (
            "فروش بار قابل بررسی\n"
            "توضیحات: از #میلان به #تهران و #مشهد\n"
            "سه شنبه 8 سپتامبر\n"
            "لباس و مدارک پذیرفته می شود\n"
            "و بار باید قابل بررسی باشد\n"
            "شهر: Milan#\n"
        )
        self.assertEqual(classify_role(text), MarketRole.SUPPLY)
        origin, dests = extract_stops(text)
        self.assertEqual(origin, {"city": "Milan", "country": "IT"})
        self.assertEqual(
            dests,
            [
                {"city": "Tehran", "country": "IR"},
                {"city": "Mashhad", "country": "IR"},
            ],
        )
        origin, dest = extract_route(text)
        self.assertEqual(dest, {"city": "Mashhad", "country": "IR"})

    def test_iran_to_italy_is_demand_country_route(self) -> None:
        text = "خریدار بار از ایران به ایتالیا\nفوری\n@Lnzhi"
        self.assertEqual(classify_role(text), MarketRole.DEMAND)
        origin, dests = extract_stops(text)
        self.assertEqual(origin, {"city": "Iran", "country": "IR"})
        self.assertEqual(dests, [{"city": "Italy", "country": "IT"}])

    def test_bologna_rimini_demand_is_italy(self) -> None:
        text = (
            "خریدار بار\n"
            "از ایران ترجیحا تهران\n"
            "به بلونیا فولی یا ریمینی (ایتالیا)\n"
            "یه مدرک شناسایی\n"
            "@fiordinarciso"
        )
        self.assertEqual(classify_role(text), MarketRole.DEMAND)
        origin, dests = extract_stops(text)
        self.assertEqual(origin, {"city": "Tehran", "country": "IR"})
        self.assertIn({"city": "Italy", "country": "IT"}, dests)
        self.assertIn({"city": "Bologna", "country": "IT"}, dests)
        self.assertIn({"city": "Forli", "country": "IT"}, dests)
        self.assertIn({"city": "Rimini", "country": "IT"}, dests)

    def test_germany_augsburg_paren_is_one_city(self) -> None:
        text = (
            "قبول بار از ایران (اصفهان) به آلمان (آگزبورگ)\n"
            "تاریخ پرواز بین ۵ تا ۱۰ سپتامبر\n"
            "هزینه توافقی\n"
            "@Mj_rafal"
        )
        self.assertEqual(classify_role(text), MarketRole.SUPPLY)
        origin, dests = extract_stops(text)
        self.assertEqual(origin, {"city": "Isfahan", "country": "IR"})
        self.assertEqual(dests, [{"city": "Augsburg", "country": "DE"}])

    def test_mashhad_istanbul_toronto_uses_first_origin_and_both_dests(self) -> None:
        text = (
            "تاریخ پرواز : ۲۷ آگوست\n"
            "پرواز با ترکیش\n"
            "@amirzz9122\n"
            "توضیحات : کالاهای مجاز و مدارک و خرید داروهای مجاز\n"
            "پرواز از مشهد و تهران به استانبول و سپس تورنتو"
        )
        self.assertEqual(classify_role(text), MarketRole.SUPPLY)
        origin, dests = extract_stops(text)
        self.assertEqual(origin, {"city": "Mashhad", "country": "IR"})
        self.assertEqual(
            dests,
            [
                {"city": "Istanbul", "country": "TR"},
                {"city": "Toronto", "country": "CA"},
            ],
        )


class MarketIngestTests(TestCase):
    def test_clamp_lookback_days(self) -> None:
        from market.ingest import DEFAULT_EXTRACT_DAYS

        self.assertEqual(clamp_lookback_days(None, default=DEFAULT_EXTRACT_DAYS), 1)
        self.assertEqual(clamp_lookback_days("", default=1), 1)
        self.assertEqual(clamp_lookback_days(0, default=1), 1)
        self.assertEqual(clamp_lookback_days(1), 1)
        self.assertEqual(clamp_lookback_days(30), 30)
        self.assertEqual(clamp_lookback_days(99), 30)

    def test_ingest_upserts_by_message_id(self) -> None:
        html = recent_preview_html()

        def fetch(_username: str, _before: int | None) -> str:
            return html

        first = ingest_market_channel(
            username="koolbar_international",
            fetch_page=fetch,
            head_pages=1,
            backfill_pages=0,
        )
        second = ingest_market_channel(
            username="koolbar_international",
            fetch_page=fetch,
            head_pages=1,
            backfill_pages=0,
        )
        self.assertTrue(first["ok"])
        self.assertEqual(first["created"], 2)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 2)
        self.assertEqual(MarketPost.objects.count(), 2)
        supply = MarketPost.objects.get(telegram_message_id=7001)
        self.assertEqual(supply.role, MarketRole.SUPPLY)
        self.assertEqual(supply.origin_city, "Tehran")
        self.assertEqual(supply.destination_city, "Toronto")
        self.assertEqual(supply.views, 1200)

    def test_skips_posts_older_than_one_month(self) -> None:
        html = """
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="koolbar_international/1">
    <div class="tgme_widget_message_text js-message_text">#مسافر مبدا تهران مقصد تورنتو</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">10</span>
      <time datetime="2020-01-01T00:00:00+00:00">01:00</time>
    </div>
  </div>
</div>
"""

        def fetch(_username: str, _before: int | None) -> str:
            return html

        result = ingest_market_channel(
            username="koolbar_international",
            fetch_page=fetch,
            head_pages=1,
            backfill_pages=0,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["created"], 0)
        self.assertEqual(MarketPost.objects.count(), 0)

    def test_ingest_stops_before_deadline(self) -> None:
        def fetch(_username: str, _before: int | None) -> str:
            raise AssertionError("should not fetch after the deadline")

        result = ingest_market_channel(
            username="koolbar_international",
            fetch_page=fetch,
            head_pages=1,
            backfill_pages=0,
            stop_at=0,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["deferred"])
        self.assertEqual(result["created"], 0)
        self.assertEqual(MarketPost.objects.count(), 0)

    def test_lookback_days_skips_older_posts(self) -> None:
        stamp = (timezone.now() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        html = f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="koolbar_international/2">
    <div class="tgme_widget_message_text js-message_text">#مسافر مبدا تهران مقصد تورنتو</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">10</span>
      <time datetime="{stamp}">01:00</time>
    </div>
  </div>
</div>
"""

        def fetch(_username: str, _before: int | None) -> str:
            return html

        one_day = ingest_market_channel(
            username="koolbar_international",
            fetch_page=fetch,
            head_pages=1,
            backfill_pages=0,
            days=1,
        )
        self.assertTrue(one_day["ok"])
        self.assertEqual(one_day["created"], 0)
        month = ingest_market_channel(
            username="koolbar_international",
            fetch_page=fetch,
            head_pages=1,
            backfill_pages=0,
            days=30,
        )
        self.assertEqual(month["created"], 1)
        self.assertEqual(MarketPost.objects.get(telegram_message_id=2).origin_city, "Tehran")

    def test_wider_lookback_resumes_past_known_head_pages(self) -> None:
        from market.models import MarketIngestState

        recent_stamp = (timezone.now() - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        older_stamp = (timezone.now() - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        recent_html = f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="koolbar_international/900">
    <div class="tgme_widget_message_text js-message_text">#مسافر مبدا تهران مقصد تورنتو</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">10</span>
      <time datetime="{recent_stamp}">01:00</time>
    </div>
  </div>
</div>
"""
        older_html = f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="koolbar_international/800">
    <div class="tgme_widget_message_text js-message_text">#مسافر مبدا ونکوور مقصد تهران</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">8</span>
      <time datetime="{older_stamp}">01:00</time>
    </div>
  </div>
</div>
"""

        def fetch(_username: str, before: int | None) -> str:
            return older_html if before else recent_html

        first = ingest_market_channel(
            username="koolbar_international",
            fetch_page=fetch,
            head_pages=1,
            backfill_pages=0,
            days=1,
        )
        self.assertEqual(first["created"], 1)
        state = MarketIngestState.objects.get(channel_username="koolbar_international")
        state.backfill_complete = True
        state.save(update_fields=["backfill_complete"])

        second = ingest_market_channel(
            username="koolbar_international",
            fetch_page=fetch,
            head_pages=1,
            backfill_pages=2,
            days=15,
        )
        self.assertEqual(second["created"], 1)
        self.assertTrue(MarketPost.objects.filter(telegram_message_id=800).exists())
        self.assertEqual(MarketPost.objects.count(), 2)

    def test_ancient_db_post_does_not_skip_15_day_preview_walk(self) -> None:
        from market.models import MarketIngestState

        ancient = timezone.now() - timedelta(days=25)
        recent_stamp = (timezone.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        mid_stamp = (timezone.now() - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        MarketPost.objects.create(
            channel_username="koolbar_international",
            telegram_message_id=100,
            posted_at=ancient,
            text="#مسافر مبدا تهران مقصد تورنتو",
            role=MarketRole.SUPPLY,
        )
        MarketIngestState.objects.create(
            channel_username="koolbar_international",
            backfill_complete=True,
            oldest_message_id=100,
        )
        recent_html = f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="koolbar_international/500">
    <div class="tgme_widget_message_text js-message_text">#مسافر مبدا تهران مقصد تورنتو</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">10</span>
      <time datetime="{recent_stamp}">01:00</time>
    </div>
  </div>
</div>
"""
        mid_html = f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="koolbar_international/400">
    <div class="tgme_widget_message_text js-message_text">#مسافر مبدا ونکوور مقصد تهران</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">8</span>
      <time datetime="{mid_stamp}">01:00</time>
    </div>
  </div>
</div>
"""

        def fetch(_username: str, before: int | None) -> str:
            return mid_html if before else recent_html

        result = ingest_market_channel(
            username="koolbar_international",
            fetch_page=fetch,
            head_pages=1,
            backfill_pages=2,
            days=15,
        )
        self.assertEqual(result["created"], 2)
        self.assertFalse(result["window_covered"])
        self.assertTrue(MarketPost.objects.filter(telegram_message_id=400).exists())
        self.assertTrue(MarketPost.objects.filter(telegram_message_id=500).exists())

    def test_configured_channel_list(self) -> None:
        with override_settings(
            MARKET_CHANNEL_USERNAMES="koolbar_international, koolbarcanada, CoolbarEUIRAN"
        ):
            self.assertEqual(
                market_channel_usernames(),
                ["koolbar_international", "koolbarcanada", "CoolbarEUIRAN"],
            )

    def test_ingest_all_channels(self) -> None:
        from market.ingest import ingest_all_market_channels

        def fetch(username: str, _before: int | None) -> str:
            return recent_preview_html(username)

        with override_settings(MARKET_CHANNEL_USERNAMES="koolbar_international,koolbarcanada"):
            result = ingest_all_market_channels(fetch_page=fetch, head_pages=1, backfill_pages=0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["created"], 4)
        self.assertEqual(
            set(MarketPost.objects.values_list("channel_username", flat=True)),
            {"koolbar_international", "koolbarcanada"},
        )


@override_settings(
    CRON_SECRET=CRON_SECRET,
    BOT_SERVICE_SECRET="",
    MARKET_CHANNEL_USERNAME="koolbar_international",
    MARKET_CHANNEL_USERNAMES="koolbar_international",
    MARKET_INGEST_ENABLED=True,
)
class MarketCronTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()
    def test_cron_requires_secret(self) -> None:
        response = self.client.get("/api/cron/market-channel/")
        self.assertEqual(response.status_code, 403)

    def test_cron_ingests_when_authorized(self) -> None:
        html = recent_preview_html()

        def fetch(_username: str, _before: int | None) -> str:
            return html

        with patch("market.ingest.fetch_preview_page", fetch):
            response = self.client.get(
                "/api/cron/market-channel/",
                HTTP_AUTHORIZATION=f"Bearer {CRON_SECRET}",
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(MarketPost.objects.count(), 2)

    def test_daily_vercel_cron_extracts_converts_and_publishes_separately(self) -> None:
        from pathlib import Path

        html = recent_preview_html()
        root = Path(__file__).resolve().parents[2]
        backend = Path(__file__).resolve().parents[1]
        for vercel_json in (root / "vercel.json", backend / "vercel.json"):
            text = vercel_json.read_text(encoding="utf-8")
            self.assertIn('"/api/cron/market-extract/"', text)
            self.assertIn('"/api/cron/market-convert/"', text)
            self.assertIn('"/api/cron/market-publish/"', text)
            self.assertIn('"0 2 * * *"', text)
            self.assertIn('"0 3 * * *"', text)
            self.assertIn('"0 4 * * *"', text)
            self.assertNotIn("0 * * * *", text)
            self.assertNotIn("*/4", text)
        self.assertFalse((root / ".github/workflows/market-extract.yml").exists())
        self.assertFalse((root / ".github/workflows/market-llm-retry.yml").exists())
        self.assertFalse((root / ".github/workflows/ingest-market-channel.yml").exists())

        def fetch(_username: str, _before: int | None) -> str:
            return html

        auth = {"HTTP_AUTHORIZATION": f"Bearer {CRON_SECRET}"}
        with patch("market.ingest.fetch_preview_page", fetch):
            extract = self.client.get("/api/cron/market-extract/", **auth)
        self.assertEqual(extract.status_code, 200)
        extract_payload = extract.json()
        self.assertTrue(extract_payload["ok"])
        self.assertEqual(extract_payload["step"], "extract")
        self.assertEqual(extract_payload["days"], 1)
        self.assertTrue(extract_payload["ingest"]["ok"])
        self.assertEqual(extract_payload["migrate"]["created"], 0)
        self.assertEqual(MarketPost.objects.count(), 2)

        convert = self.client.get("/api/cron/market-convert/", **auth)
        self.assertEqual(convert.status_code, 200)
        convert_payload = convert.json()
        self.assertTrue(convert_payload["ok"])
        self.assertEqual(convert_payload["step"], "convert")
        self.assertGreaterEqual(convert_payload["migrate"]["created"] + convert_payload["migrate"]["skipped"], 1)

        with override_settings(
            TELEGRAM_CHANNEL_ENABLED=True,
            TELEGRAM_CHANNEL_ID="-100111",
            TELEGRAM_BOT_TOKEN="tok",
        ):
            with patch("notifications.telegram.call_telegram_api") as mocked:
                mocked.return_value = {"ok": True, "result": {"message_id": 9301}}
                publish = self.client.get("/api/cron/market-publish/", **auth)
        self.assertEqual(publish.status_code, 200)
        publish_payload = publish.json()
        self.assertEqual(publish_payload["step"], "publish")
        self.assertGreaterEqual(publish_payload["publish"]["published"], 1)

        with patch("market.ingest.fetch_preview_page", fetch):
            limited = self.client.get("/api/cron/market-extract/?days=15", **auth)
        self.assertEqual(limited.status_code, 200)
        self.assertEqual(limited.json()["days"], 15)


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False)
class MarketMigrateTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def _post(self, **kwargs) -> MarketPost:
        from django.utils import timezone

        defaults = {
            "channel_username": "koolbar_international",
            "telegram_message_id": kwargs.pop("telegram_message_id", 8001),
            "posted_at": timezone.now(),
            "text": "#مسافر\nمبدا : تهران\nمقصد : تورنتو",
            "role": MarketRole.SUPPLY,
        }
        defaults.update(kwargs)
        return MarketPost.objects.create(**defaults)

    def test_defaults_kg_documents_and_lists_in_explore(self) -> None:
        post = self._post()
        result = migrate_market_posts()
        self.assertEqual(result["created"], 1)
        item = ItemRequest.objects.get(imported=True)
        self.assertEqual(item.status, RequestStatus.ACTIVE)
        self.assertEqual(item.type, RequestType.SUPPLY)
        self.assertEqual(item.origin_city, "tehran")
        self.assertEqual(item.destination_city, "toronto")
        self.assertEqual(str(item.capacity_kg), "0.10")
        self.assertIn("DOCUMENTS", {category.code for category in item.item_categories.all()})
        self.assertTrue(item.imported)
        self.assertEqual(item.source_url, "https://t.me/koolbar_international/8001")
        self.assertNotIn("#مسافر", item.description)
        self.assertIn("تهران", item.description)
        self.assertIn("تورنتو", item.description)
        self.assertIn("مدارک", item.description)
        post.refresh_from_db()
        self.assertEqual(post.item_request_id, item.id)

        viewer = make_user(telegram_user_id=99001, first_name="Leila")
        response = self.client.get("/api/explore/", **bearer_auth(viewer))
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.json()}
        self.assertIn(item.id, ids)
        row = next(row for row in response.json() if row["id"] == item.id)
        self.assertTrue(row["imported"])
        self.assertEqual(row["owner_first_name"], "koolbar")
        self.assertEqual(item.user.telegram_username, "koolbar")

    def test_country_only_and_italy_and_karaj_map_to_catalog(self) -> None:
        self._post(
            telegram_message_id=8002,
            role=MarketRole.DEMAND,
            text="مسافر نیستم\nمبدا: ایران\nمقصد : میلان",
        )
        self._post(
            telegram_message_id=8003,
            role=MarketRole.SUPPLY,
            text="#مسافر مبدا : کرج مقصد : دالاس",
        )
        migrate_market_posts()
        iran_milan = ItemRequest.objects.get(origin_city="tehran", destination_city="milan")
        self.assertEqual(iran_milan.origin_country, "IR")
        self.assertEqual(iran_milan.destination_country, "IT")
        self.assertEqual(str(iran_milan.weight_kg), "0.10")
        karaj = ItemRequest.objects.get(origin_city="tehran", destination_city="dallas")
        self.assertEqual(karaj.destination_country, "US")

    def test_tehran_hanover_flight_converts_as_supply(self) -> None:
        posted = timezone.make_aware(datetime(2026, 9, 10, 8, 0))
        text = (
            "پرواز تهران به هانوفر\n"
            "بیستم سپتامبر\n"
            "بار قابل رویت+ مدارک\n"
            "@Kh_8758"
        )
        self._post(
            telegram_message_id=9010,
            role=MarketRole.UNKNOWN,
            posted_at=posted,
            author_username="Kh_8758",
            text=text,
        )
        with patch("market.migrate.timezone.now", return_value=posted):
            result = migrate_market_posts()
        self.assertEqual(result["created"], 1)
        item = ItemRequest.objects.get(imported=True)
        self.assertEqual(item.type, RequestType.SUPPLY)
        self.assertEqual(item.origin_city, "tehran")
        self.assertEqual(item.origin_country, "IR")
        self.assertEqual(item.destination_city, "hannover")
        self.assertEqual(item.destination_country, "DE")
        self.assertEqual(item.flight_date, date(2026, 9, 20))
        self.assertEqual(item.date_from, date(2026, 9, 10))
        self.assertEqual(item.date_to, date(2026, 9, 17))
        self.assertIn("DOCUMENTS", {category.code for category in item.item_categories.all()})
        self.assertEqual(item.user.telegram_username, "Kh_8758")

    def test_milan_stops_convert_as_supply_with_clothes_and_documents(self) -> None:
        posted = timezone.make_aware(datetime(2026, 9, 6, 8, 0))
        text = (
            "فروش بار قابل بررسی\n"
            "توضیحات: از #میلان به #تهران و #مشهد\n"
            "سه شنبه 8 سپتامبر\n"
            "لباس و مدارک پذیرفته می شود\n"
            "و بار باید قابل بررسی باشد\n"
            "@carrier_it\n"
        )
        self._post(
            telegram_message_id=9011,
            role=MarketRole.UNKNOWN,
            posted_at=posted,
            author_username="carrier_it",
            text=text,
        )
        from market.extract import _rules_payload

        post = MarketPost.objects.get(telegram_message_id=9011)
        with patch("market.migrate.timezone.now", return_value=posted):
            payload, reason = _rules_payload(post)
        self.assertIsNone(reason)
        self.assertEqual(payload["type"], RequestType.SUPPLY)
        self.assertEqual(payload["origin_city"], "milan")
        self.assertEqual(payload["destination_city"], "mashhad")
        self.assertEqual(
            payload["destination_cities"],
            [
                {"country": "IR", "city": "tehran"},
                {"country": "IR", "city": "mashhad"},
            ],
        )
        self.assertCountEqual(payload["item_category_codes"], ["CLOTHES", "DOCUMENTS"])

    def test_iran_to_italy_keeps_tehran_and_all_italian_cities(self) -> None:
        from market.extract import _rules_payload

        posted = timezone.now()
        text = "خریدار بار از ایران به ایتالیا\nفوری\n@Lnzhi"
        post = self._post(
            telegram_message_id=9012,
            role=MarketRole.UNKNOWN,
            posted_at=posted,
            author_username="Lnzhi",
            text=text,
        )
        payload, reason = _rules_payload(post)
        self.assertIsNone(reason)
        self.assertEqual(payload["type"], RequestType.DEMAND)
        self.assertEqual(payload["origin_country"], "IR")
        self.assertEqual(payload["origin_city"], "tehran")
        self.assertEqual(payload["destination_country"], "IT")
        self.assertEqual(payload["destination_city"], "milan")
        self.assertCountEqual(
            [(item["country"], item["city"]) for item in payload["destination_cities"]],
            [
                ("IT", "milan"),
                ("IT", "rome"),
                ("IT", "turin"),
                ("IT", "naples"),
                ("IT", "bologna"),
                ("IT", "forli"),
                ("IT", "rimini"),
            ],
        )
        self.assertIn("ایتالیا", payload["description"])
        self.assertEqual(payload["item_category_codes"], [])

    def test_id_document_to_bologna_rimini_fills_italy_and_documents(self) -> None:
        from market.extract import _rules_payload

        text = (
            "خریدار بار\n"
            "از ایران ترجیحا تهران\n"
            "به بلونیا فولی یا ریمینی (ایتالیا)\n"
            "یه مدرک شناسایی\n"
            "@fiordinarciso"
        )
        post = self._post(
            telegram_message_id=9013,
            role=MarketRole.UNKNOWN,
            author_username="fiordinarciso",
            text=text,
        )
        payload, reason = _rules_payload(post)
        self.assertIsNone(reason)
        self.assertEqual(payload["type"], RequestType.DEMAND)
        self.assertEqual(payload["origin_city"], "tehran")
        self.assertEqual(payload["destination_country"], "IT")
        self.assertCountEqual(
            [item["city"] for item in payload["destination_cities"]],
            ["bologna", "forli", "rimini"],
        )
        self.assertIn("DOCUMENTS", payload["item_category_codes"])
        self.assertNotIn("PET", payload["item_category_codes"])

    def test_augsburg_supply_does_not_expand_germany_or_default_documents(self) -> None:
        from item_requests.models import City
        from market.extract import _rules_payload

        text = (
            "قبول بار از ایران (اصفهان) به آلمان (آگزبورگ)\n"
            "تاریخ پرواز بین ۵ تا ۱۰ سپتامبر\n"
            "هزینه توافقی\n"
            "@Mj_rafal"
        )
        post = self._post(
            telegram_message_id=9014,
            role=MarketRole.UNKNOWN,
            author_username="Mj_rafal",
            text=text,
        )
        payload, reason = _rules_payload(post)
        self.assertIsNone(reason)
        self.assertEqual(payload["type"], RequestType.SUPPLY)
        self.assertEqual(payload["origin_city"], "isfahan")
        self.assertEqual(payload["destination_cities"], [{"country": "DE", "city": "augsburg"}])
        self.assertEqual(payload["item_category_codes"], [])
        self.assertNotIn("capacity_kg", payload)
        self.assertTrue(City.objects.filter(country__code="DE", slug="augsburg").exists())

    def test_unknown_german_city_is_created_instead_of_all_germany(self) -> None:
        from item_requests.models import City
        from market.extract import _rules_payload

        text = "قبول بار از اصفهان به آلمان (فلدا)\n@guest"
        post = self._post(
            telegram_message_id=9015,
            role=MarketRole.UNKNOWN,
            text=text,
        )
        payload, reason = _rules_payload(post)
        self.assertIsNone(reason)
        self.assertEqual(payload["destination_cities"], [{"country": "DE", "city": "فلدا"}])
        self.assertTrue(City.objects.filter(country__code="DE", slug="فلدا").exists())
        self.assertEqual(payload["item_category_codes"], [])

    def test_simcard_and_other_small_items_select_documents_and_other(self) -> None:
        from market.extract import _rules_payload

        posted = timezone.make_aware(datetime(2026, 9, 10, 8, 0))
        post = self._post(
            telegram_message_id=9018,
            role=MarketRole.SUPPLY,
            posted_at=posted,
            text=(
                "تاریخ: ۱۵ سپتامبر\n"
                "قبول مدارک و سیمکارت و سایر کوچک و کم وزن\n"
                "پرواز تهران به تورنتو"
            ),
        )
        with patch("market.migrate.timezone.now", return_value=posted):
            payload, reason = _rules_payload(post)
        self.assertIsNone(reason)
        self.assertCountEqual(payload["item_category_codes"], ["DOCUMENTS", "OTHER"])

    def test_mashhad_istanbul_toronto_keeps_stated_date_and_categories(self) -> None:
        from market.extract import _rules_payload

        posted = timezone.make_aware(datetime(2026, 8, 20, 12, 0))
        text = (
            "تاریخ پرواز : ۲۷ آگوست\n"
            "پرواز با ترکیش\n"
            "@amirzz9122\n"
            "توضیحات : کالاهای مجاز و مدارک و خرید داروهای مجاز\n"
            "پرواز از مشهد و تهران به استانبول و سپس تورنتو"
        )
        post = self._post(
            telegram_message_id=9016,
            role=MarketRole.UNKNOWN,
            author_username="amirzz9122",
            posted_at=posted,
            text=text,
        )
        reviewed_at = timezone.make_aware(datetime(2026, 8, 20, 14, 0))
        with patch("market.migrate.timezone.now", return_value=reviewed_at):
            payload, reason = _rules_payload(post)
        self.assertIsNone(reason)
        self.assertEqual(payload["type"], RequestType.SUPPLY)
        self.assertEqual(payload["origin_city"], "mashhad")
        self.assertEqual(
            payload["destination_cities"],
            [
                {"country": "TR", "city": "istanbul"},
                {"country": "CA", "city": "toronto"},
            ],
        )
        self.assertCountEqual(payload["item_category_codes"], ["DOCUMENTS", "MEDICINE"])
        self.assertEqual(payload["flight_date"], "2026-08-27")
        self.assertNotIn("capacity_kg", payload)

    def test_past_august_flight_is_not_converted(self) -> None:
        from market.extract import _rules_payload
        from market.migrate import SKIP_EXPIRED

        posted = timezone.make_aware(datetime(2026, 8, 26, 12, 0))
        post = self._post(
            telegram_message_id=9017,
            role=MarketRole.SUPPLY,
            author_username="amirzz9122",
            posted_at=posted,
            text=(
                "تاریخ پرواز : ۲۷ آگوست\n"
                "پرواز از مشهد و تهران به استانبول و سپس تورنتو\n"
                "مدارک و دارو"
            ),
        )
        reviewed_at = timezone.make_aware(datetime(2026, 9, 10, 14, 0))
        with patch("market.migrate.timezone.now", return_value=reviewed_at):
            payload, reason = _rules_payload(post)
            result = migrate_market_posts()
        self.assertIsNone(payload)
        self.assertEqual(reason, SKIP_EXPIRED)
        self.assertEqual(result["created"], 0)
        self.assertEqual(ItemRequest.objects.filter(imported=True).count(), 0)

    def test_skips_noise_and_expired_dates(self) -> None:
        self._post(
            telegram_message_id=8004,
            role=MarketRole.NOISE,
            text="تاکسی در رم شهری و فرودگاهی",
        )
        self._post(
            telegram_message_id=8005,
            role=MarketRole.SUPPLY,
            text="#مسافر مبدا تهران مقصد تورنتو تاریخ 1 May 2020",
        )
        result = migrate_market_posts()
        self.assertEqual(result["created"], 0)
        self.assertGreaterEqual(result["skipped"], 2)
        self.assertEqual(ItemRequest.objects.filter(imported=True).count(), 0)

    def test_skips_channel_ad_and_expires_existing_request(self) -> None:
        ad = (
            "«کانال کولبر کانادا» با هدف ارائه ی اطلاعات مانند قوانین و مقررات گمرکی "
            "ایران و کانادا #لینک کانال تلگرام: https://t.me/koolbarcanada "
            "#لینک گروه کولبر کانادا تلگرام: https://t.me/joinchat/mRng6VpbVNwzMDNh "
            "#ادمین تبلیغات و تبادل: @Niknia2012"
        )
        self.assertEqual(classify_role(ad), MarketRole.NOISE)
        created = self._post(
            telegram_message_id=8115,
            role=MarketRole.DEMAND,
            text="#مسافر\nمبدا : تهران\nمقصد : تورنتو",
        )
        migrate_market_posts()
        created.refresh_from_db()
        item = created.item_request
        self.assertIsNotNone(item)
        created.text = ad
        created.role = MarketRole.DEMAND
        created.save(update_fields=["text", "role", "updated_at"])
        result = migrate_market_posts()
        self.assertEqual(result["expired"], 1)
        item.refresh_from_db()
        self.assertEqual(item.status, RequestStatus.EXPIRED)
        created.refresh_from_db()
        self.assertEqual(created.skip_reason, "noise")

    @override_settings(OPENROUTER_API_KEY="sk-or-test")
    def test_openrouter_review_fills_fields_and_skips_ads(self) -> None:
        from datetime import timedelta

        from django.utils import timezone as dj_timezone

        travel = (dj_timezone.now() + timedelta(days=12)).date()
        payload = {
            "accept": True,
            "role": "supply",
            "origin_city": "Tehran",
            "origin_country": "IR",
            "destination_city": "Toronto",
            "destination_country": "CA",
            "date_from": travel.isoformat(),
            "date_to": travel.isoformat(),
            "flight_date": travel.isoformat(),
            "weight_kg": 8,
            "item_category_codes": ["DOCUMENTS", "MEDICINE"],
            "excluded_category_codes": ["CIGARETTES"],
            "excluded_other_text": "no liquids",
            "description": "Traveler from Tehran to Toronto can carry documents and medicine.",
            "author_username": "reza_trip",
        }

        def fake_complete(_prompt, **_kwargs):
            from ai.openrouter import ChatResult

            return ChatResult(ok=True, text=__import__("json").dumps(payload), model="openrouter/free")

        post = self._post(
            telegram_message_id=8201,
            text="#مسافر مبدا تهران مقصد تورنتو قبول بار مدارک دارو سیگار قبول نمیکنم",
        )
        with patch("market.review.complete", side_effect=fake_complete):
            result = migrate_market_posts()
        self.assertEqual(result["created"], 1)
        item = ItemRequest.objects.get(imported=True)
        self.assertEqual(item.user.telegram_username, "reza_trip")
        self.assertEqual(item.user.first_name, "reza_trip")
        self.assertEqual(item.flight_date, travel)
        self.assertEqual(item.date_from, dj_timezone.now().date())
        self.assertEqual(item.date_to, travel - timedelta(days=3))
        self.assertEqual(str(item.capacity_kg), "8.00")
        self.assertEqual(item.excluded_other_text, "no liquids")
        self.assertIn("تهران", item.description)
        self.assertIn("تورنتو", item.description)
        self.assertIn("مدارک", item.description)
        self.assertEqual(item.channel_status, ChannelStatus.NOT_PUBLISHED)
        self.assertIn("CIGARETTES", {category.code for category in item.excluded_categories.all()})
        post.refresh_from_db()
        self.assertIsNotNone(post.reviewed_at)

        ad = self._post(
            telegram_message_id=8202,
            role=MarketRole.DEMAND,
            text="مسافر نیستم\nمبدا: ونکوور\nمقصد : تهران\nحدود 10 کیلو لباس",
        )

        def reject_complete(_prompt, **_kwargs):
            from ai.openrouter import ChatResult

            return ChatResult(
                ok=True,
                text='{"accept": false, "reject_reason": "ad"}',
                model="openrouter/free",
            )

        with patch("market.review.complete", side_effect=reject_complete):
            second = migrate_market_posts()
        ad.refresh_from_db()
        self.assertEqual(ad.skip_reason, "ad")
        self.assertGreaterEqual(second["skipped"], 1)

    @override_settings(OPENROUTER_API_KEY="sk-or-test", OPENAI_API_KEY="sk-openai")
    def test_noise_does_not_call_llm(self) -> None:
        self._post(
            telegram_message_id=8302,
            role=MarketRole.NOISE,
            text="گروه خرید و رزرو بلیط هواپیما\nلینک گروه:\n@FlightAbroad",
        )
        with patch("market.review.complete") as mocked:
            result = migrate_market_posts()
        mocked.assert_not_called()
        self.assertEqual(result["created"], 0)
        self.assertEqual(ItemRequest.objects.count(), 0)

    @override_settings(OPENROUTER_API_KEY="sk-or-test", OPENAI_API_KEY="sk-openai")
    def test_unknown_group_message_skips_llm(self) -> None:
        self._post(
            telegram_message_id=8303,
            role=MarketRole.UNKNOWN,
            text="hello everyone, who is online?",
        )
        with patch("market.review.complete") as mocked:
            migrate_market_posts()
        mocked.assert_not_called()
        post = MarketPost.objects.get(telegram_message_id=8303)
        self.assertEqual(post.skip_reason, "unknown_role")

    @override_settings(
        OPENROUTER_API_KEY="sk-or-test",
        OPENAI_API_KEY="sk-openai",
        TELEGRAM_CHANNEL_ENABLED=True,
        TELEGRAM_CHANNEL_ID="-100111",
        TELEGRAM_BOT_TOKEN="tok",
        TELEGRAM_BOT_USERNAME="CB_koolbarbot",
    )
    def test_llm_failure_retries_then_falls_back_after_six_hours(self) -> None:
        from datetime import timedelta

        from ai.openrouter import ChatResult
        from django.utils import timezone as dj_timezone

        post = self._post(telegram_message_id=8301)
        fail = ChatResult(ok=False, error="Empty model response.", model="openrouter/free")
        with patch("market.review.complete", return_value=fail):
            first = migrate_market_posts()
        self.assertEqual(first["created"], 0)
        self.assertEqual(first["deferred"], 1)
        post.refresh_from_db()
        self.assertEqual(post.skip_reason, "llm_retry")
        self.assertIsNotNone(post.llm_retry_started_at)
        self.assertEqual(ItemRequest.objects.count(), 0)

        with patch("market.review.complete", return_value=fail):
            still_waiting = migrate_market_posts()
        self.assertEqual(still_waiting["created"], 0)
        self.assertEqual(ItemRequest.objects.count(), 0)

        post.llm_retry_started_at = dj_timezone.now() - timedelta(hours=7)
        post.save(update_fields=["llm_retry_started_at"])
        with patch("market.review.complete", return_value=fail):
            with patch("notifications.telegram.call_telegram_api") as telegram:
                telegram.return_value = {"ok": True, "result": {"message_id": 9101}}
                with self.captureOnCommitCallbacks(execute=True):
                    result = migrate_market_posts()
        self.assertEqual(result["created"], 1)
        item = ItemRequest.objects.get(imported=True)
        self.assertEqual(item.origin_city, "tehran")
        self.assertEqual(item.destination_city, "toronto")
        self.assertEqual(item.channel_status, ChannelStatus.PUBLISHED)
        self.assertEqual(item.channel_message_id, 9101)

    @override_settings(
        TELEGRAM_CHANNEL_ENABLED=True,
        TELEGRAM_CHANNEL_ID="-100111",
        TELEGRAM_BOT_TOKEN="tok",
        TELEGRAM_BOT_USERNAME="CB_koolbarbot",
    )
    def test_imported_requests_are_published_to_koolbar_channel(self) -> None:
        self._post(telegram_message_id=8006)
        with patch("notifications.telegram.call_telegram_api") as mocked:
            mocked.return_value = {"ok": True, "result": {"message_id": 9001}}
            with self.captureOnCommitCallbacks(execute=True):
                migrate_market_posts()
        mocked.assert_called()
        self.assertEqual(mocked.call_args.args[0], "sendMessage")
        item = ItemRequest.objects.get(imported=True)
        self.assertEqual(item.channel_status, ChannelStatus.PUBLISHED)
        self.assertEqual(item.channel_message_id, 9001)

    @override_settings(OPENROUTER_API_KEY="sk-or-test", OPENAI_API_KEY="sk-openai")
    def test_wait_for_llm_false_converts_with_rules_only(self) -> None:
        self._post(telegram_message_id=8401)
        with patch("market.review.complete") as mocked:
            result = migrate_market_posts(
                wait_for_llm=False,
                pending_only=True,
                newest_first=True,
            )
        mocked.assert_not_called()
        self.assertEqual(result["created"], 1)
        self.assertEqual(ItemRequest.objects.filter(imported=True).count(), 1)

    def test_convert_assigns_author_user_for_messaging(self) -> None:
        self._post(
            telegram_message_id=8410,
            author_username="sara_trip",
            author_name="Sara Aghyani",
        )
        result = migrate_market_posts(wait_for_llm=False)
        self.assertEqual(result["created"], 1)
        item = ItemRequest.objects.get(imported=True)
        self.assertEqual(item.user.telegram_username, "sara_trip")
        self.assertEqual(item.user.first_name, "Sara Aghyani")

    def test_convert_reads_author_handle_from_post_text(self) -> None:
        self._post(
            telegram_message_id=8411,
            author_username="",
            author_name="ارسال بار به سراسر دنیا",
            text="#مسافر\nمبدا : تهران\nمقصد : تورنتو\n@n_ii_ss",
        )
        migrate_market_posts(wait_for_llm=False)
        item = ItemRequest.objects.get(imported=True)
        self.assertEqual(item.user.telegram_username, "n_ii_ss")
        self.assertEqual(item.user.first_name, "n_ii_ss")
        post = MarketPost.objects.get(telegram_message_id=8411)
        self.assertEqual(post.author_username, "n_ii_ss")

    def test_reassign_imported_owners_fixes_existing_requests(self) -> None:
        from market.migrate import ingest_owner, reassign_imported_request_owners

        post = self._post(
            telegram_message_id=8412,
            author_username="old_owner",
            author_name="Old Owner",
        )
        migrate_market_posts(wait_for_llm=False)
        item = ItemRequest.objects.get(imported=True)
        item.user = ingest_owner()
        item.save(update_fields=["user", "updated_at"])
        post.author_username = "fixed_author"
        post.author_name = "Fixed Author"
        post.save(update_fields=["author_username", "author_name", "updated_at"])

        result = reassign_imported_request_owners()
        self.assertEqual(result["updated"], 1)
        item.refresh_from_db()
        self.assertEqual(item.user.telegram_username, "fixed_author")
        self.assertEqual(item.user.first_name, "Fixed Author")

    def test_convert_assigns_author_user_for_messaging(self) -> None:
        self._post(
            telegram_message_id=8410,
            author_username="sara_trip",
            author_name="Sara Aghyani",
        )
        result = migrate_market_posts(wait_for_llm=False)
        self.assertEqual(result["created"], 1)
        item = ItemRequest.objects.get(imported=True)
        self.assertEqual(item.user.telegram_username, "sara_trip")
        self.assertEqual(item.user.first_name, "Sara Aghyani")

    def test_convert_reads_author_handle_from_post_text(self) -> None:
        self._post(
            telegram_message_id=8411,
            author_username="",
            author_name="ارسال بار به سراسر دنیا",
            text="#مسافر\nمبدا : تهران\nمقصد : تورنتو\n@n_ii_ss",
        )
        migrate_market_posts(wait_for_llm=False)
        item = ItemRequest.objects.get(imported=True)
        self.assertEqual(item.user.telegram_username, "n_ii_ss")
        self.assertEqual(item.user.first_name, "n_ii_ss")
        post = MarketPost.objects.get(telegram_message_id=8411)
        self.assertEqual(post.author_username, "n_ii_ss")

    def test_reassign_imported_owners_fixes_existing_requests(self) -> None:
        from market.migrate import ingest_owner, reassign_imported_request_owners

        post = self._post(
            telegram_message_id=8412,
            author_username="old_owner",
            author_name="Old Owner",
        )
        migrate_market_posts(wait_for_llm=False)
        item = ItemRequest.objects.get(imported=True)
        item.user = ingest_owner()
        item.save(update_fields=["user", "updated_at"])
        post.author_username = "fixed_author"
        post.author_name = "Fixed Author"
        post.save(update_fields=["author_username", "author_name", "updated_at"])

        result = reassign_imported_request_owners()
        self.assertEqual(result["updated"], 1)
        item.refresh_from_db()
        self.assertEqual(item.user.telegram_username, "fixed_author")
        self.assertEqual(item.user.first_name, "Fixed Author")

    def test_normalize_user_first_names_replaces_channel_and_empty(self) -> None:
        from market.migrate import SOURCE_USER_BASE, normalize_user_first_names
        from users.models import User

        blank = User.objects.create(telegram_user_id=91001, first_name=" ", telegram_username="has_handle")
        channel = User.objects.create(
            telegram_user_id=91002,
            first_name="koolbar_international",
            telegram_username="channel_owner",
        )
        listing = User.objects.create(telegram_user_id=91000, first_name="Channel listing")
        dear = User.objects.create(
            telegram_user_id=91004,
            first_name="کاربر عزیز",
            telegram_username="Saleh_hhh",
        )
        handle_as_name = User.objects.create(
            telegram_user_id=SOURCE_USER_BASE + 11,
            first_name="reza_trip",
            telegram_username="reza_trip",
        )
        keep = User.objects.create(telegram_user_id=91003, first_name="Leila", telegram_username="leila")

        result = normalize_user_first_names()
        self.assertGreaterEqual(result["updated"], 3)
        blank.refresh_from_db()
        channel.refresh_from_db()
        listing.refresh_from_db()
        dear.refresh_from_db()
        handle_as_name.refresh_from_db()
        keep.refresh_from_db()
        self.assertEqual(blank.first_name, "has_handle")
        self.assertEqual(channel.first_name, "channel_owner")
        self.assertEqual(listing.first_name, "Channel listing")
        self.assertEqual(dear.first_name, "Saleh_hhh")
        self.assertEqual(handle_as_name.first_name, "reza_trip")
        self.assertEqual(keep.first_name, "Leila")

    def test_pending_only_skips_already_converted_posts(self) -> None:
        first = self._post(telegram_message_id=8402)
        migrate_market_posts(wait_for_llm=False)
        first.refresh_from_db()
        self.assertIsNotNone(first.item_request_id)
        self._post(
            telegram_message_id=8403,
            text="#مسافر\nمبدا : ونکوور\nمقصد : تهران",
            role=MarketRole.DEMAND,
        )
        result = migrate_market_posts(
            wait_for_llm=False,
            pending_only=True,
            newest_first=True,
            max_posts=5,
        )
        self.assertEqual(result["created"], 1)
        self.assertEqual(ItemRequest.objects.filter(imported=True).count(), 2)


@override_settings(
    SECRET_KEY=TEST_SECRET,
    DEBUG=True,
    MARKET_CHANNEL_USERNAMES="koolbar_international",
    OPENROUTER_API_KEY="sk-or-test",
)
class MarketExtractTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()
        StaffUser = __import__("django.contrib.auth.models", fromlist=["User"]).User
        StaffUser.objects.create_superuser("ops", "ops@example.com", "ops-pass")

    def setUp(self) -> None:
        self.client.login(username="ops", password="ops-pass")

    def test_parse_telegram_post_ref(self) -> None:
        from market.extract import parse_telegram_post_ref

        self.assertEqual(
            parse_telegram_post_ref("https://t.me/koolbar_international/7265"),
            ("koolbar_international", 7265),
        )
        self.assertEqual(
            parse_telegram_post_ref("https://t.me/s/koolbar_international/7265"),
            ("koolbar_international", 7265),
        )
        self.assertEqual(parse_telegram_post_ref("koolbar_international/7265"), ("koolbar_international", 7265))
        self.assertIsNone(parse_telegram_post_ref("https://t.me/koolbar_international"))

    def test_extract_named_post_from_url(self) -> None:
        from ai.openrouter import ChatResult
        from market.extract import extract_named_post
        from market.models import MarketIngestState

        html = supply_preview_html().replace("/7001", "/7265")

        def fetch(_username: str, _before: int | None) -> str:
            return html

        with patch("market.review.complete") as mocked:
            mocked.return_value = ChatResult(ok=False, error="Empty model response.", model="openrouter/free")
            result = extract_named_post(
                url="https://t.me/koolbar_international/7265",
                fetch_page=fetch,
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"], "draft")
        self.assertEqual(result["draft"]["message_id"], 7265)
        self.assertEqual(result["draft"]["origin_city"], "tehran")
        self.assertFalse(MarketIngestState.objects.filter(extract_cursor_id=7265).exists())

    def test_extract_named_post_without_text(self) -> None:
        from market.extract import extract_named_post

        stamp = (timezone.now() - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        html = f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="koolbar_international/7265">
    <div class="tgme_widget_message_forwarded_from">Forwarded from advertioCL</div>
    <div class="message_media_not_supported">Please open Telegram to view this post</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">17</span>
      <a class="tgme_widget_message_date" href="https://t.me/koolbar_international/7265"><time datetime="{stamp}">20:43</time></a>
    </div>
  </div>
</div>
"""

        def fetch(_username: str, _before: int | None) -> str:
            return html

        result = extract_named_post(url="https://t.me/koolbar_international/7265", fetch_page=fetch)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"], "no_text")
        self.assertIsNone(result["draft"])
        self.assertEqual(result["preview"]["message_id"], 7265)
        self.assertEqual(ItemRequest.objects.count(), 0)

    def test_extract_uses_inline_contact_button(self) -> None:
        from ai.openrouter import ChatResult
        from market.extract import extract_one_post

        stamp = (timezone.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        html = f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="koolbar_international/7306">
    <div class="tgme_widget_message_text js-message_text" dir="auto">#فروش_بار تبریز به ناپل پرواز ۱۸ سپتامبر</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">5</span>
      <a class="tgme_widget_message_date" href="https://t.me/koolbar_international/7306"><time datetime="{stamp}">12:00</time></a>
    </div>
  </div>
  <div class="tgme_widget_message_inline_keyboard">
    <a class="tgme_widget_message_inline_button url_button" href="https://t.me/shabi_80">@shabi_80</a>
  </div>
</div>
"""

        def fetch(_username: str, _before: int | None) -> str:
            return html

        with patch("market.review.complete") as mocked:
            mocked.return_value = ChatResult(ok=False, error="Empty model response.", model="openrouter/free")
            result = extract_one_post("koolbar_international", fetch_page=fetch)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"], "draft")
        self.assertEqual(result["draft"]["author_username"], "shabi_80")
        self.assertEqual(result["draft"]["contact_url"], "https://t.me/shabi_80")

    def test_extract_one_post_opens_review_draft(self) -> None:
        from datetime import timedelta

        from ai.openrouter import ChatResult
        from django.utils import timezone as dj_timezone
        from market.extract import convert_reviewed_post, extract_one_post

        travel = (dj_timezone.now() + timedelta(days=10)).date()
        html = supply_preview_html()

        def fake_complete(_prompt, **_kwargs):
            return ChatResult(
                ok=True,
                text=json.dumps(
                    {
                        "accept": True,
                        "role": "supply",
                        "origin_city": "Tehran",
                        "origin_country": "IR",
                        "destination_city": "Toronto",
                        "destination_country": "CA",
                        "flight_date": travel.isoformat(),
                        "date_from": travel.isoformat(),
                        "date_to": travel.isoformat(),
                        "weight_kg": 10,
                        "item_category_codes": ["DOCUMENTS"],
                        "description": "Traveler Tehran to Toronto.",
                    }
                ),
                model="openrouter/free",
            )

        def fetch(_username: str, _before: int | None) -> str:
            return html

        with patch("market.review.complete", side_effect=fake_complete):
            result = extract_one_post("koolbar_international", fetch_page=fetch)
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], "draft")
        self.assertEqual(result["draft"]["type"], "SUPPLY")
        self.assertEqual(result["draft"]["origin_city"], "tehran")
        self.assertEqual(result["draft"]["destination_city"], "toronto")
        self.assertEqual(result["draft"]["author_username"], "koolbar")
        self.assertNotEqual(result["draft"]["author_username"], "koolbar_international")
        messages = " ".join(line["message"] for line in result["logs"])
        self.assertIn("LLMs:", messages)
        self.assertIn("Review the draft below", messages)
        self.assertEqual(ItemRequest.objects.filter(imported=True).count(), 0)

        draft = result["draft"]
        self.assertEqual(draft["item_category_codes"], ["DOCUMENTS"])
        self.assertEqual(draft["weight_kg"], "10.00")
        converted = convert_reviewed_post(result["post_id"], draft)
        self.assertTrue(converted["ok"], converted)
        self.assertEqual(converted["result"], "created")
        self.assertEqual(ItemRequest.objects.filter(imported=True).count(), 1)

    def test_empty_llm_still_fills_vitamin_draft(self) -> None:
        from ai.openrouter import ChatResult
        from market.extract import convert_reviewed_post, extract_one_post

        html = vitamin_preview_html()

        def fetch(_username: str, _before: int | None) -> str:
            return html

        with patch("market.review.complete") as mocked:
            mocked.return_value = ChatResult(ok=False, error="Empty model response.", model="openrouter/free")
            result = extract_one_post("koolbar_international", fetch_page=fetch)
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], "draft")
        draft = result["draft"]
        self.assertEqual(draft["type"], "DEMAND")
        self.assertEqual(draft["origin_country"], "GB")
        self.assertEqual(draft["origin_city"], "london")
        self.assertEqual(draft["destination_country"], "IR")
        self.assertEqual(draft["destination_city"], "tehran")
        self.assertIn("MEDICINE", draft["item_category_codes"])
        self.assertEqual(draft["author_username"], "n_ii_ss")
        self.assertEqual(draft["llm_error"], "Empty model response.")
        self.assertNotIn("کسی هست", draft["description"])
        self.assertNotIn("وقت به خیر", draft["description"])
        self.assertNotEqual(draft["description"].strip(), draft["text"].strip())
        self.assertIn("لندن", draft["description"])
        self.assertIn("تهران", draft["description"])
        self.assertIn("دارو", draft["description"])
        self.assertEqual(ItemRequest.objects.count(), 0)

        with override_settings(
            TELEGRAM_CHANNEL_ENABLED=True,
            TELEGRAM_CHANNEL_ID="-100111",
            TELEGRAM_BOT_TOKEN="tok",
        ):
            with patch("notifications.telegram.call_telegram_api") as mocked_tg:
                mocked_tg.return_value = {"ok": True, "result": {"message_id": 9001}}
                converted = convert_reviewed_post(result["post_id"], draft)
        self.assertTrue(converted["ok"], converted)
        item = ItemRequest.objects.get(imported=True)
        self.assertEqual(item.type, RequestType.DEMAND)
        self.assertEqual(item.origin_city, "london")
        self.assertEqual(item.destination_city, "tehran")
        self.assertEqual(item.user.telegram_username, "n_ii_ss")
        self.assertEqual(item.user.first_name, "n_ii_ss")
        self.assertEqual(item.channel_status, ChannelStatus.PUBLISHED)
        self.assertEqual(item.channel_message_id, 9001)

    def test_empty_llm_fills_hanover_supply_draft(self) -> None:
        from ai.openrouter import ChatResult
        from market.extract import extract_one_post

        posted = timezone.make_aware(datetime(2026, 9, 10, 8, 0))

        def fetch(_username: str, _before: int | None) -> str:
            return hanover_preview_html()

        with (
            patch("django.utils.timezone.now", return_value=posted),
            patch("market.review.complete") as mocked,
        ):
            mocked.return_value = ChatResult(ok=False, error="Empty model response.", model="openrouter/free")
            result = extract_one_post("koolbar_international", fetch_page=fetch)
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], "draft")
        draft = result["draft"]
        self.assertEqual(draft["type"], "SUPPLY")
        self.assertEqual(draft["origin_city"], "tehran")
        self.assertEqual(draft["destination_city"], "hannover")
        self.assertEqual(draft["flight_date"], "2026-09-20")
        self.assertEqual(draft["date_from"], "2026-09-10")
        self.assertEqual(draft["date_to"], "2026-09-17")
        self.assertIn("DOCUMENTS", draft["item_category_codes"])
        self.assertEqual(draft["author_username"], "Kh_8758")

    def test_extract_augsburg_does_not_expand_germany_or_default_documents(self) -> None:
        from ai.openrouter import ChatResult
        from market.extract import extract_one_post

        def fake_complete(_prompt, **_kwargs):
            return ChatResult(
                ok=True,
                text=json.dumps(
                    {
                        "accept": True,
                        "role": "supply",
                        "origin_city": "Isfahan",
                        "origin_country": "IR",
                        "destination_city": "Augsburg",
                        "destination_country": "DE",
                        "weight_kg": "",
                        "item_category_codes": [],
                        "description": "Traveler from Isfahan to Augsburg.",
                    }
                ),
                model="openrouter/free",
            )

        def fetch(_username: str, _before: int | None) -> str:
            return augsburg_preview_html()

        with patch("market.review.complete", side_effect=fake_complete):
            result = extract_one_post("koolbar_international", fetch_page=fetch)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"], "draft")
        draft = result["draft"]
        self.assertEqual(draft["type"], "SUPPLY")
        self.assertEqual(draft["origin_city"], "isfahan")
        self.assertEqual(draft["destination_keys"], ["DE:augsburg"])
        self.assertEqual(draft["item_category_codes"], [])
        self.assertEqual(draft["weight_kg"], "")
        self.assertEqual(draft["author_username"], "Mj_rafal")
        messages = " ".join(line["message"] for line in result["logs"])
        self.assertIn("filled the draft", messages)

    def test_description_keeps_persian_phone_and_koolbar_author(self) -> None:
        from ai.openrouter import ChatResult
        from market.extract import extract_one_post
        from market.review import extract_contact_phone, fallback_listing_description

        text = (
            "پرواز: ۳۰ سپتامبر\n"
            "فول بار و قابل رویت\n"
            "ارسال بار به کلیه شهرهای ایران\n"
            "مسیر: تورنتو → اصفهان\n"
            "جهت هماهنگی در واتساپ: ۰۹۱۳۵۸۸۱۶۸۸"
        )
        self.assertEqual(extract_contact_phone(text), "09135881688")
        description = fallback_listing_description(
            is_supply=True,
            origin="toronto",
            dest="isfahan",
            contact_phone="09135881688",
        )
        self.assertIn("تورنتو", description)
        self.assertIn("اصفهان", description)
        self.assertIn("09135881688", description)

        stamp = (timezone.now() - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        html = f"""
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message js-widget_message" data-post="koolbar_international/7020">
    <div class="tgme_widget_message_text js-message_text" dir="auto">پرواز: ۳۰ سپتامبر<br>مسیر: تورنتو → اصفهان<br>جهت هماهنگی در واتساپ: 09135881688</div>
    <div class="tgme_widget_message_footer compact js-message_footer">
      <span class="tgme_widget_message_views">10</span>
      <a class="tgme_widget_message_date" href="https://t.me/koolbar_international/7020"><time datetime="{stamp}">12:00</time></a>
    </div>
  </div>
</div>
"""

        def fake_complete(_prompt, **_kwargs):
            return ChatResult(
                ok=True,
                text=json.dumps(
                    {
                        "accept": True,
                        "role": "supply",
                        "origin_city": "Toronto",
                        "origin_country": "CA",
                        "destination_city": "Isfahan",
                        "destination_country": "IR",
                        "flight_date": "2026-09-30",
                        "item_category_codes": [],
                        "description": "Traveler can carry from Toronto to Isfahan.",
                    }
                ),
                model="openrouter/free",
            )

        def fetch(_username: str, _before: int | None) -> str:
            return html

        with (
            override_settings(TELEGRAM_CHANNEL_USERNAME="koolbar"),
            patch("market.review.complete", side_effect=fake_complete),
        ):
            result = extract_one_post("koolbar_international", fetch_page=fetch)
        draft = result["draft"]
        self.assertEqual(draft["author_username"], "koolbar")
        self.assertNotEqual(draft["author_username"], "koolbar_international")
        self.assertIn("تورنتو", draft["description"])
        self.assertIn("اصفهان", draft["description"])
        self.assertIn("09135881688", draft["description"])

    def test_extract_uses_ai_route_and_categories_for_mashhad_post(self) -> None:
        from ai.openrouter import ChatResult
        from market.extract import extract_one_post

        reviewed_at = timezone.make_aware(datetime(2026, 8, 20, 14, 0))

        def fake_complete(_prompt, **_kwargs):
            return ChatResult(
                ok=True,
                text=json.dumps(
                    {
                        "accept": True,
                        "role": "supply",
                        "origin_city": "Mashhad",
                        "origin_country": "IR",
                        "destination_city": "Toronto",
                        "destination_country": "CA",
                        "destination_cities": [
                            {"city": "Istanbul", "country": "TR"},
                            {"city": "Toronto", "country": "CA"},
                        ],
                        "flight_date": "2026-08-27",
                        "weight_kg": "",
                        "item_category_codes": ["DOCUMENTS", "MEDICINE"],
                        "description": "Traveler from Mashhad to Istanbul then Toronto can carry documents and medicine.",
                        "author_username": "amirzz9122",
                    }
                ),
                model="openrouter/free",
            )

        def fetch(_username: str, _before: int | None) -> str:
            return mashhad_istanbul_preview_html()

        with (
            patch("django.utils.timezone.now", return_value=reviewed_at),
            patch("market.migrate.timezone.now", return_value=reviewed_at),
            patch("market.review.complete", side_effect=fake_complete),
        ):
            result = extract_one_post("koolbar_international", fetch_page=fetch)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"], "draft")
        draft = result["draft"]
        self.assertEqual(draft["origin_city"], "mashhad")
        self.assertEqual(draft["destination_keys"], ["TR:istanbul", "CA:toronto"])
        self.assertCountEqual(draft["item_category_codes"], ["DOCUMENTS", "MEDICINE"])
        self.assertEqual(draft["flight_date"], "2026-08-27")
        self.assertEqual(draft["weight_kg"], "")
        self.assertEqual(draft["author_username"], "amirzz9122")

    def test_extract_skips_past_august_flight(self) -> None:
        from ai.openrouter import ChatResult
        from market.extract import extract_one_post

        reviewed_at = timezone.make_aware(datetime(2026, 9, 10, 14, 0))

        def fetch(_username: str, _before: int | None) -> str:
            return mashhad_istanbul_preview_html()

        with (
            patch("django.utils.timezone.now", return_value=reviewed_at),
            patch("market.migrate.timezone.now", return_value=reviewed_at),
            patch("market.review.complete") as mocked,
        ):
            mocked.return_value = ChatResult(ok=True, text="{}", model="openrouter/free")
            result = extract_one_post("koolbar_international", fetch_page=fetch)
        mocked.assert_not_called()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"], "expired")
        self.assertIsNone(result["draft"])
        self.assertIn("۲۷ آگوست", result["preview"]["text"])
        self.assertEqual(ItemRequest.objects.count(), 0)
        messages = " ".join(line["message"] for line in result["logs"])
        self.assertIn("already past", messages)

    def test_extract_walks_to_next_older_post(self) -> None:
        from ai.openrouter import ChatResult
        from market.extract import extract_one_post, reset_extract_cursor
        from market.models import MarketIngestState

        html = recent_preview_html()

        def fetch(_username: str, _before: int | None) -> str:
            return html

        with patch("market.review.complete") as mocked:
            mocked.return_value = ChatResult(ok=False, error="Empty model response.", model="openrouter/free")
            first = extract_one_post("koolbar_international", fetch_page=fetch)
            second = extract_one_post("koolbar_international", fetch_page=fetch)
            third = extract_one_post("koolbar_international", fetch_page=fetch)
        self.assertEqual(first["result"], "draft")
        self.assertEqual(first["draft"]["message_id"], 7002)
        self.assertEqual(second["result"], "draft")
        self.assertEqual(second["draft"]["message_id"], 7001)
        self.assertEqual(third["result"], "caught_up")
        self.assertIsNone(third["draft"])
        cursor = MarketIngestState.objects.get(channel_username="koolbar_international").extract_cursor_id
        self.assertEqual(cursor, 7001)

        reset = reset_extract_cursor("koolbar_international")
        self.assertTrue(reset["ok"])
        with patch("market.review.complete") as mocked:
            mocked.return_value = ChatResult(ok=False, error="Empty model response.", model="openrouter/free")
            again = extract_one_post("koolbar_international", fetch_page=fetch)
        self.assertEqual(again["draft"]["message_id"], 7002)

    def test_extract_skips_group_promo(self) -> None:
        from market.extract import extract_one_post

        html = flight_group_preview_html()

        def fetch(_username: str, _before: int | None) -> str:
            return html

        with patch("market.review.complete") as mocked:
            result = extract_one_post("koolbarcanada", fetch_page=fetch)
        mocked.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], "noise")
        self.assertIsNone(result["draft"])
        self.assertIn("FlightAbroad", result["preview"]["text"])
        self.assertEqual(ItemRequest.objects.count(), 0)
        messages = " ".join(line["message"] for line in result["logs"])
        self.assertIn("not a send/carry request", messages)

    def test_admin_extract_page_and_post(self) -> None:
        html = vitamin_preview_html()
        page = self.client.get("/admin/market/extract/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Manual channel extract")
        self.assertContains(page, "Extract 1 post")
        self.assertContains(page, "Extract this post")
        self.assertContains(page, "https://t.me/koolbar_international/7265")
        self.assertContains(page, "Run extraction")
        self.assertContains(page, "Convert stored posts")
        self.assertContains(page, "Publish to channel")
        self.assertContains(page, "Unconverted in window")
        self.assertContains(page, 'name="days"')
        self.assertContains(page, 'value="1"')
        self.assertContains(page, "1–30 for all channels")
        self.assertContains(page, "every configured channel")
        self.assertContains(page, "Run log")
        self.assertContains(page, "Reset to latest")
        self.assertContains(page, "dark:bg-base-900")
        self.assertContains(page, "dark:border-base-700")

        def fetch(_username: str, _before: int | None) -> str:
            return html

        with patch("market.extract.fetch_preview_page", side_effect=fetch):
            with patch("market.ingest.fetch_preview_page", side_effect=fetch):
                with patch("market.review.complete") as mocked:
                    from ai.openrouter import ChatResult

                    mocked.return_value = ChatResult(
                        ok=False, error="Empty model response.", model="openrouter/free"
                    )
                    response = self.client.post(
                        "/admin/market/extract/",
                        {
                            "action": "one_post",
                            "channel": "koolbar_international",
                            "force_review": "on",
                        },
                    )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Empty model response")
        self.assertContains(response, "Convert to request")
        self.assertContains(response, "london")
        self.assertContains(response, "n_ii_ss")
        self.assertContains(response, "extract-post-box")
        self.assertContains(response, "extract-segment")
        self.assertContains(response, "Copy log")
        self.assertNotContains(response, "Did not convert")

        post = MarketPost.objects.get(telegram_message_id=7011)
        convert = self.client.post(
            "/admin/market/extract/",
            {
                "action": "convert",
                "channel": "koolbar_international",
                "post_id": str(post.pk),
                "type": "DEMAND",
                "origin_country": "GB",
                "origin_city": "london",
                "destination_country": "IR",
                "destination_city": "tehran",
                "desired_date": (timezone.now() + timedelta(days=14)).date().isoformat(),
                "weight_kg": "2",
                "item_category_codes": ["MEDICINE"],
                "description": "Kids vitamins London to Tehran",
                "author_username": "n_ii_ss",
            },
        )
        self.assertEqual(convert.status_code, 200)
        self.assertContains(convert, "Converted to request")
        item = ItemRequest.objects.get(imported=True)
        self.assertEqual(item.origin_city, "london")
        self.assertEqual(item.destination_city, "tehran")
        self.assertIn("MEDICINE", {category.code for category in item.item_categories.all()})

    def test_admin_extract_named_post_url(self) -> None:
        html = supply_preview_html().replace("/7001", "/7265")

        with patch("market.extract.fetch_preview_around", side_effect=lambda *_args, **_kwargs: html):
            with patch("market.review.complete") as mocked:
                from ai.openrouter import ChatResult

                mocked.return_value = ChatResult(
                    ok=False, error="Empty model response.", model="openrouter/free"
                )
                response = self.client.post(
                    "/admin/market/extract/",
                    {
                        "action": "named_post",
                        "channel": "koolbar_international",
                        "post_url": "https://t.me/koolbar_international/7265",
                        "force_review": "on",
                    },
                )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Convert to request")
        self.assertContains(response, "koolbar_international/7265")
        self.assertTrue(MarketPost.objects.filter(telegram_message_id=7265).exists())

    def test_run_extraction_crawls_all_channels_like_the_job(self) -> None:
        from market.extract import extract_all_channels

        def fetch(username: str, _before: int | None) -> str:
            return recent_preview_html(username)

        with override_settings(MARKET_CHANNEL_USERNAMES="koolbar_international,koolbarcanada"):
            with patch("market.review.complete") as mocked:
                from ai.openrouter import ChatResult

                mocked.return_value = ChatResult(
                    ok=False, error="Empty model response.", model="openrouter/free"
                )
                result = extract_all_channels(fetch_page=fetch, days=1)
                with patch("market.ingest.fetch_preview_page", side_effect=fetch):
                    response = self.client.post(
                        "/admin/market/extract/",
                        {"action": "extract", "days": "1"},
                    )
        self.assertEqual(result["result"], "job")
        self.assertTrue(result["ok"])
        self.assertEqual(result["days"], 1)
        self.assertGreaterEqual(result["ingest"]["created"], 4)
        self.assertEqual(
            set(MarketPost.objects.values_list("channel_username", flat=True)),
            {"koolbar_international", "koolbarcanada"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crawled")
        self.assertContains(response, "Expired")
        self.assertContains(response, "Ingest created")
        self.assertContains(response, "for the last 1 day")
        self.assertContains(response, "Convert stored posts")
        self.assertGreaterEqual(result["migrate"]["created"] + result["migrate"]["skipped"], 1)
        self.assertGreaterEqual(ItemRequest.objects.filter(imported=True).count(), 1)

    def test_convert_stored_posts_uses_rules_without_llm(self) -> None:
        from market.extract import convert_stored_posts

        MarketPost.objects.create(
            channel_username="koolbar_international",
            telegram_message_id=8501,
            posted_at=timezone.now(),
            text="#مسافر\nمبدا : تهران\nمقصد : تورنتو",
            role=MarketRole.SUPPLY,
        )
        with patch("market.review.complete") as mocked:
            result = convert_stored_posts()
            response = self.client.post(
                "/admin/market/extract/",
                {"action": "convert_stored", "days": "15"},
            )
        mocked.assert_not_called()
        self.assertEqual(result["result"], "convert")
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["migrate"]["created"], 1)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Converted created")
        self.assertTrue(ItemRequest.objects.filter(imported=True).exists())
        self.assertEqual(ItemRequest.objects.get(imported=True).channel_status, ChannelStatus.NOT_PUBLISHED)

    @override_settings(
        TELEGRAM_CHANNEL_ENABLED=True,
        TELEGRAM_CHANNEL_ID="-100111",
        TELEGRAM_BOT_TOKEN="tok",
        TELEGRAM_BOT_USERNAME="CB_koolbarbot",
    )
    def test_publish_stored_requests_posts_unpublished_imports(self) -> None:
        from market.extract import convert_stored_posts, publish_stored_requests

        MarketPost.objects.create(
            channel_username="koolbar_international",
            telegram_message_id=8502,
            posted_at=timezone.now(),
            text="#مسافر\nمبدا : تهران\nمقصد : تورنتو",
            role=MarketRole.SUPPLY,
        )
        convert_stored_posts()
        item = ItemRequest.objects.get(imported=True)
        self.assertEqual(item.channel_status, ChannelStatus.NOT_PUBLISHED)
        with patch("notifications.telegram.call_telegram_api") as mocked:
            mocked.return_value = {"ok": True, "result": {"message_id": 9201}}
            result = publish_stored_requests()
            response = self.client.post(
                "/admin/market/extract/",
                {"action": "publish_stored", "days": "15"},
            )
        self.assertEqual(result["result"], "publish")
        self.assertGreaterEqual(result["publish"]["published"], 1)
        item.refresh_from_db()
        self.assertEqual(item.channel_status, ChannelStatus.PUBLISHED)
        self.assertEqual(item.channel_message_id, 9201)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Published")

