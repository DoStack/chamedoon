from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from item_requests.models import ChannelStatus, ItemRequest, RequestStatus, RequestType
from item_requests.seed import seed_catalog
from market.classify import classify_role, extract_route
from market.ingest import ingest_market_channel, market_channel_usernames
from market.migrate import migrate_market_posts
from market.models import MarketPost, MarketRole
from market.parse import parse_preview_html, parse_views
from tests.helpers import TEST_SECRET, bearer_auth, make_user

FIXTURE = Path(__file__).parent / "fixtures" / "channel_preview.html"
CRON_SECRET = "cron-test-secret"


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


class MarketIngestTests(TestCase):
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

    def test_daily_vercel_cron_ingests_and_migrates(self) -> None:
        from pathlib import Path

        html = recent_preview_html()
        root = Path(__file__).resolve().parents[2]
        backend = Path(__file__).resolve().parents[1]
        for vercel_json in (root / "vercel.json", backend / "vercel.json"):
            text = vercel_json.read_text(encoding="utf-8")
            self.assertIn('"/api/cron/market-migrate/"', text)
            self.assertIn('"0 6 * * *"', text)
            self.assertNotIn("0 * * * *", text)
            self.assertNotIn("*/4", text)
        self.assertFalse((root / ".github/workflows/ingest-market-channel.yml").exists())

        def fetch(_username: str, _before: int | None) -> str:
            return html

        with patch("market.ingest.fetch_preview_page", fetch):
            response = self.client.get(
                "/api/cron/market-migrate/",
                HTTP_AUTHORIZATION=f"Bearer {CRON_SECRET}",
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["ingest"]["ok"])
        self.assertTrue(payload["migrate"]["ok"])
        self.assertEqual(MarketPost.objects.count(), 2)


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
        self.assertEqual(str(item.capacity_kg), "10.00")
        self.assertIn("DOCUMENTS", {category.code for category in item.item_categories.all()})
        self.assertTrue(item.imported)
        self.assertEqual(item.source_url, "https://t.me/koolbar_international/8001")
        self.assertNotIn("#مسافر", item.description)
        self.assertIn("tehran", item.description.lower())
        post.refresh_from_db()
        self.assertEqual(post.item_request_id, item.id)

        viewer = make_user(telegram_user_id=99001, first_name="Leila")
        response = self.client.get("/api/explore/", **bearer_auth(viewer))
        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.json()}
        self.assertIn(item.id, ids)
        row = next(row for row in response.json() if row["id"] == item.id)
        self.assertTrue(row["imported"])
        self.assertEqual(row["owner_first_name"], "koolbar_international")

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
        self.assertEqual(str(iran_milan.weight_kg), "10.00")
        karaj = ItemRequest.objects.get(origin_city="tehran", destination_city="dallas")
        self.assertEqual(karaj.destination_country, "US")

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
        self.assertEqual(item.date_from, travel)
        self.assertEqual(item.date_to, travel)
        self.assertEqual(str(item.capacity_kg), "8.00")
        self.assertEqual(item.excluded_other_text, "no liquids")
        self.assertEqual(item.description, payload["description"])
        self.assertEqual(item.channel_status, ChannelStatus.NOT_PUBLISHED)
        self.assertIn("CIGARETTES", {category.code for category in item.excluded_categories.all()})
        post.refresh_from_db()
        self.assertIsNotNone(post.reviewed_at)

        ad = self._post(
            telegram_message_id=8202,
            role=MarketRole.UNKNOWN,
            text="random promo without noise keywords but not a request",
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

    def test_extract_one_post_opens_review_draft(self) -> None:
        from datetime import timedelta

        from ai.openrouter import ChatResult
        from django.utils import timezone as dj_timezone
        from market.extract import convert_reviewed_post, extract_one_post

        travel = (dj_timezone.now() + timedelta(days=10)).date()
        html = recent_preview_html()

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
        messages = " ".join(line["message"] for line in result["logs"])
        self.assertIn("OpenRouter is on", messages)
        self.assertIn("Review the draft below", messages)
        self.assertEqual(ItemRequest.objects.filter(imported=True).count(), 0)

        converted = convert_reviewed_post(result["post_id"], result["draft"])
        self.assertTrue(converted["ok"])
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
        self.assertEqual(item.channel_status, ChannelStatus.PUBLISHED)
        self.assertEqual(item.channel_message_id, 9001)

    def test_admin_extract_page_and_post(self) -> None:
        html = vitamin_preview_html()
        page = self.client.get("/admin/market/extract/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Manual channel extract")
        self.assertContains(page, "Extract 1 post")
        self.assertContains(page, "Run extraction")
        self.assertContains(page, "Run log")
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

