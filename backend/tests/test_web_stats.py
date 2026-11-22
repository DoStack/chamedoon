"""Public stats endpoint (PLAN §4): shape, digest arithmetic, privacy, build-token lock."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from home.models import HomeChannel, HomeListing, HomePost, PostStatus
from item_requests.models import RequestType
from tests.test_home import make_source
from web import stats

NOW = datetime(2026, 11, 20, 17, 0, tzinfo=dt_timezone.utc)  # Friday, noon in Toronto
_ids = iter(range(50_000, 60_000))
SECRET_TEXT = "تماس @landlord_handle 4165550199"


def listing(source, text: str, *, kind="offer", type="room", price=900, hours_ago=2.0, city="Toronto", area="", **extra) -> HomeListing:
    n = next(_ids)
    post = HomePost.objects.create(
        source=source, telegram_message_id=n, posted_at=NOW - timedelta(hours=hours_ago), text=text, status=PostStatus.ACCEPTED,
        author_username="landlord_handle", source_url="https://t.me/src/1",
    )
    return HomeListing.objects.create(
        post=post, kind=kind, property_type=type, country=source.country, city=city, area=area,
        price_amount=Decimal(price) if price else None, price_currency="CAD" if source.country == "CA" else "EUR",
        price_period="month", status="active", first_seen=post.posted_at, last_seen=timezone_now(), **extra,
    )


def timezone_now():
    from django.utils import timezone

    return timezone.now()


class Base(TestCase):
    def setUp(self) -> None:
        cache.clear()
        HomeChannel.objects.all().delete()
        self.source = make_source(username="toronto_src", country="CA", default_city="Toronto", default_currency="CAD")
        self.toronto = HomeChannel.objects.create(country="CA", city="Toronto", aliases="North York", username="t_khane", enabled=True)
        HomeChannel.objects.create(country="CA", city="Vancouver", username="v_khane", enabled=True)
        HomeChannel.objects.create(country="CA", city="", username="ca_khane", enabled=True)
        HomeChannel.objects.create(country="DE", city="Berlin", username="b_khane", enabled=True)

    def rooms(self, n: int, *, price=1000, hours_ago=30.0, area="", step=10) -> None:
        for i in range(n):
            listing(self.source, f"اتاق مبله شماره {i} {SECRET_TEXT}", price=price + i * step, hours_ago=hours_ago + i, area=area)


class BuildTests(Base):
    def test_shape_and_order(self) -> None:
        body = stats.build(NOW)
        self.assertEqual(body["generated_at"], "2026-11-20T17:00:00Z")
        self.assertEqual(body["method_version"], "1")
        self.assertEqual([c["slug"] for c in body["cities"]], ["toronto", "vancouver", "berlin"])
        city = body["cities"][0]
        self.assertEqual(
            set(city),
            {"slug", "name_fa", "name_en", "country", "currency", "live", "new_24h", "new_7d", "new_30d", "medians_30d", "weekly",
             "areas_30d", "by_weekday", "with_photo_share", "furnished_share"},
        )
        self.assertEqual(city["currency"], "CAD")
        self.assertEqual(len(city["by_weekday"]), 7)
        self.assertEqual(set(body["courier"]), {"live_trips", "live_requests", "routes_30d"})
        self.assertEqual(set(body["totals"]), {"sources_read", "posts_reviewed_30d", "listings_live"})

    def test_median_needs_eight_and_reposts_count_once(self) -> None:
        self.rooms(7)
        listing(self.source, "اتاق مبله شماره 0 " + SECRET_TEXT, price=1000, hours_ago=40)  # a repost of the first
        room = next(m for m in stats.build(NOW)["cities"][0]["medians_30d"] if m["type"] == "room")
        self.assertEqual(room["n"], 7)
        self.assertIsNone(room["median"])
        self.assertIsNone(room["p25"])
        self.rooms(1, price=2000, hours_ago=50)
        room = next(m for m in stats.build(NOW)["cities"][0]["medians_30d"] if m["type"] == "room")
        self.assertEqual(room["n"], 8)
        self.assertIsNotNone(room["median"])
        self.assertLessEqual(room["p25"], room["median"])
        self.assertLessEqual(room["median"], room["p75"])

    def test_counts_by_window(self) -> None:
        listing(self.source, "اتاق تازه یک", hours_ago=2)
        listing(self.source, "اتاق سه روزه", hours_ago=72)
        listing(self.source, "اتاق بیست روزه", hours_ago=24 * 20)
        listing(self.source, "اتاق دو ماهه", hours_ago=24 * 60)
        listing(self.source, "دنبال اتاق", kind="wanted", price=None, hours_ago=5)
        city = stats.build(NOW)["cities"][0]
        self.assertEqual((city["new_24h"], city["new_7d"], city["new_30d"]), (2, 3, 4))

    def test_weeks_start_2026_11_02_and_are_complete(self) -> None:
        self.rooms(8, hours_ago=24 * 8)  # in the week of 11-09 (Toronto)
        weekly = stats.build(NOW)["cities"][0]["weekly"]
        self.assertEqual([w["week_start"] for w in weekly], ["2026-11-02", "2026-11-09"])
        self.assertEqual(weekly[1]["new"], 8)
        room = next(m for m in weekly[1]["medians"] if m["type"] == "room")
        self.assertEqual(room["n"], 8)
        self.assertIsNotNone(room["median"])

    def test_weeks_never_go_back_more_than_twelve(self) -> None:
        weeks = stats._week_starts(datetime(2027, 6, 2, 12, 0, tzinfo=dt_timezone.utc))
        self.assertEqual(len(weeks), stats.MAX_WEEKS)

    def test_areas_need_four_rooms(self) -> None:
        self.rooms(4, area="Yonge", hours_ago=20)
        self.rooms(3, area="Downsview", hours_ago=10)
        areas = stats.build(NOW)["cities"][0]["areas_30d"]
        self.assertEqual([a["name"] for a in areas], ["Yonge"])
        self.assertIsNone(areas[0]["median_room"])

    def test_no_private_data_anywhere(self) -> None:
        self.rooms(10, area="Yonge")
        text = json.dumps(stats.build(NOW), ensure_ascii=False)
        for secret in ("landlord_handle", "4165550199", "t.me", "toronto_src", "اتاق مبله"):
            self.assertNotIn(secret, text)

    def test_courier_routes(self) -> None:
        from item_requests.models import City, Country, ItemRequest
        from users.models import User

        user = User.objects.create(telegram_user_id=5001, first_name="A", telegram_username="secret_user")
        ir, _ = Country.objects.get_or_create(code="IR", defaults={"name_en": "Iran", "name_fa": "ایران"})
        ca, _ = Country.objects.get_or_create(code="CA", defaults={"name_en": "Canada", "name_fa": "کانادا"})
        City.objects.get_or_create(country=ir, slug="tehran", defaults={"name_en": "Tehran", "name_fa": "تهران"})
        City.objects.get_or_create(country=ca, slug="toronto", defaults={"name_en": "Toronto", "name_fa": "تورنتو"})
        for kind in (RequestType.SUPPLY, RequestType.SUPPLY, RequestType.DEMAND):
            ItemRequest.objects.create(
                user=user, type=kind, origin_country="IR", origin_city="tehran", destination_country="CA", destination_city="toronto",
                date_from=NOW.date(), date_to=NOW.date() + timedelta(days=10), expires_at=timezone_now() + timedelta(days=5),
                description="secret description",
            )
        courier = stats.build(timezone_now())["courier"]
        self.assertEqual((courier["live_trips"], courier["live_requests"]), (2, 1))
        self.assertEqual(courier["routes_30d"][0], {"from": "Tehran", "to": "Toronto", "trips": 2, "requests": 1})
        text = json.dumps(courier)
        self.assertNotIn("secret", text)

    def test_courier_weekly_counts(self) -> None:
        from item_requests.models import ItemRequest
        from users.models import User

        user = User.objects.create(telegram_user_id=5002, first_name="B")
        for kind, days_ago in ((RequestType.SUPPLY, 5), (RequestType.DEMAND, 5), (RequestType.DEMAND, 12), (RequestType.SUPPLY, 1)):
            item = ItemRequest.objects.create(
                user=user, type=kind, origin_country="IR", origin_city="tehran", destination_country="CA", destination_city="toronto",
                date_from=NOW.date(), date_to=NOW.date() + timedelta(days=10), expires_at=NOW + timedelta(days=5),
            )
            ItemRequest.objects.filter(pk=item.pk).update(created_at=NOW - timedelta(days=days_ago))
        weekly = stats.build(NOW)["courier"]["weekly"]
        # Complete Toronto weeks only: 2 Nov and 9 Nov; the item from 19 Nov is in the running week.
        self.assertEqual(weekly, [
            {"week_start": "2026-11-02", "trips": 0, "requests": 1},
            {"week_start": "2026-11-09", "trips": 1, "requests": 1},
        ])


TOKEN = "t" * 40


@override_settings(PUBLIC_STATS_TOKEN=TOKEN)
class EndpointTests(Base):
    def get(self, path: str = "/api/v1/public/stats/", token: str | None = TOKEN):
        headers = {"HTTP_X_STATS_TOKEN": token} if token is not None else {}
        return self.client.get(path, **headers)

    def test_build_token_required(self) -> None:
        self.assertEqual(self.get(token=None).status_code, 403)
        self.assertEqual(self.get(token="wrong").status_code, 403)
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["method_version"], "1")

    def test_private_not_cacheable_no_cors(self) -> None:
        response = self.get()
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertIn("noindex", response["X-Robots-Tag"])
        self.assertFalse(response.has_header("Access-Control-Allow-Origin"))
        self.assertFalse(response.has_header("X-License"))
        self.assertEqual(self.get(token=None)["Cache-Control"], "private, no-store")

    @override_settings(PUBLIC_STATS_TOKEN="")
    def test_closed_when_no_token_configured(self) -> None:
        self.assertEqual(self.get(token="").status_code, 403)

    def test_server_cache_used(self) -> None:
        self.get()
        with patch("web.stats.build") as build:
            self.get()
        build.assert_not_called()

    def test_csv_route_is_gone(self) -> None:
        self.assertEqual(self.get("/api/v1/public/stats/toronto.csv").status_code, 404)

    def test_failure_is_503_not_500(self) -> None:
        with patch("web.stats.build", side_effect=RuntimeError("x")):
            response = self.get()
        self.assertEqual(response.status_code, 503)

    def test_housing_contract_untouched(self) -> None:
        self.assertEqual(self.client.get("/api/v1/public/nothing/").status_code, 404)
