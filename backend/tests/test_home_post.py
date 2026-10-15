from __future__ import annotations

import io
import tempfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from home.models import HomeListing, HomeMedia, HomeSettings, HomeSource, ListingStatus, MediaStatus
from home.publish import publishable
from tests.helpers import make_user
from tests.test_home_miniapp import COURIER_TOKEN, HOME_TOKEN, MiniAppCase
from tests.test_home_photos import pattern_image
from tests.test_telegram_auth import build_init_data

OFFER = {
    "kind": "offer",
    "country": "CA",
    "city": "Toronto",
    "area": "Yonge & Finch",
    "property_type": "room",
    "price": "950",
    "price_period": "month",
    "bills_included": "yes",
    "furnished": "yes",
    "roommate_gender": "female",
    "available_from": "2026-11-01",
    "description": "اتاق نورگیر در نورث یورک، نزدیک مترو، با همه‌ی قبوض.",
    "phone": "",
}


class LandingTests(MiniAppCase):
    def test_the_housing_home_has_its_own_first_page(self) -> None:
        self.ca_listing(1)
        page = self.client.get("/app/home/").content.decode()
        self.assertIn("دنبال خونه‌ای یا هم‌خونه؟", page)
        self.assertIn('class="house-badge"', page)
        self.assertIn('href="/app/home/list/?country=CA&amp;kind=offer"', page)
        self.assertIn('href="/app/home/list/?country=CA&amp;kind=wanted"', page)
        self.assertIn('href="/app/home/new/"', page)
        self.assertIn('href="/app/home/mine/"', page)
        self.assertIn("1 آگهی", page)
        self.assertIn("https://t.me/Chamedoon_CA", page)
        self.assertIn("https://t.me/chamedoon_home_bot", page)
        self.assertNotIn("/app/demand/new/", page)
        self.assertNotIn("/app/supply/new/", page)

    def test_choosing_a_country_on_the_home_page_is_remembered(self) -> None:
        page = self.client.get("/app/home/?country=DE").content.decode()
        self.assertIn('href="/app/home/list/?country=DE&amp;kind=offer"', page)
        self.assertIn("https://t.me/Chamedoon_Germany", page)
        self.assertIn('href="/app/home/list/?country=DE&amp;kind=offer"', self.client.get("/app/home/").content.decode())

    def test_english_home_page(self) -> None:
        self.login(self.user, "en")
        page = self.client.get("/app/home/").content.decode()
        self.assertIn("Looking for a home or a flatmate?", page)
        self.assertIn("Post a listing for free", page)

    def test_the_feed_back_link_returns_to_the_housing_home(self) -> None:
        page = self.client.get("/app/home/list/?country=CA").content.decode()
        self.assertIn('class="page-back" href="/app/home/"', page)


class BackButtonTests(MiniAppCase):
    def test_housing_pages_have_housing_parents(self) -> None:
        import miniapp

        script = (Path(miniapp.__file__).parent / "static" / "miniapp" / "nav.js").read_text(encoding="utf-8")
        self.assertIn('if (path === "/app/home/" || path === "/app/home") return "";', script)
        self.assertIn('return "/app/home/list/";', script)
        self.assertIn('if (path.indexOf("/app/home/") === 0) return "/app/home/";', script)
        self.assertIn('if (explore) return "/app/explore/";', script)


class LoginLandingTests(MiniAppCase):
    @override_settings(TELEGRAM_BOT_TOKEN=COURIER_TOKEN)
    def test_opening_from_the_housing_bot_lands_on_the_housing_home(self) -> None:
        self.client.logout()
        HomeSettings.objects.update_or_create(pk=1, defaults={"bot_token": HOME_TOKEN})
        data = build_init_data(telegram_user_id=880777, first_name="H", bot_token=HOME_TOKEN)
        response = self.client.post("/app/login/", {"init_data": data})
        self.assertEqual(response["Location"], "/app/home/")

    @override_settings(TELEGRAM_BOT_TOKEN=COURIER_TOKEN)
    def test_opening_from_the_courier_bot_still_lands_on_the_courier_home(self) -> None:
        self.client.logout()
        HomeSettings.objects.update_or_create(pk=1, defaults={"bot_token": HOME_TOKEN})
        data = build_init_data(telegram_user_id=880778, first_name="C", bot_token=COURIER_TOKEN)
        response = self.client.post("/app/login/", {"init_data": data})
        self.assertEqual(response["Location"], "/app/")

    @override_settings(TELEGRAM_BOT_TOKEN=COURIER_TOKEN)
    def test_a_start_link_wins_over_the_bot(self) -> None:
        self.client.logout()
        HomeSettings.objects.update_or_create(pk=1, defaults={"bot_token": HOME_TOKEN})
        data = build_init_data(telegram_user_id=880779, first_name="S", bot_token=HOME_TOKEN)
        response = self.client.post("/app/login/", {"init_data": data, "startapp": "home_12"})
        self.assertEqual(response["Location"], "/app/home/12/")


class PostListingTests(MiniAppCase):
    def setUp(self) -> None:
        super().setUp()
        self.user.telegram_username = "sara_home"
        self.user.save()

    def test_form_renders_in_the_chamedoon_style(self) -> None:
        page = self.client.get("/app/home/new/").content.decode()
        self.assertIn('class="stack"', page)
        self.assertIn('enctype="multipart/form-data"', page)
        self.assertIn("@sara_home", page)
        self.assertIn('name="photos"', page)
        self.assertIn("canvas.toBlob", page)

    def test_an_offer_is_created_and_shows_in_the_feed(self) -> None:
        response = self.client.post("/app/home/new/", OFFER)
        listing = HomeListing.objects.get(post__source__username="app_ca")
        self.assertEqual(response["Location"], f"/app/home/{listing.pk}/?created=1")
        self.assertEqual((listing.kind, listing.city, listing.price_amount, listing.price_currency), ("offer", "Toronto", 950, "CAD"))
        self.assertEqual((listing.contact_telegram, listing.lister_type, listing.roommate_gender), ("sara_home", "owner", "female"))
        self.assertEqual(listing.summary_fa, OFFER["description"])
        self.assertFalse(listing.needs_review)
        self.assertEqual(listing.post.author_id, self.user.telegram_user_id)
        self.assertFalse(HomeSource.objects.get(username="app_ca").enabled)
        self.assertIn(f"/app/home/{listing.pk}/", self.feed("?country=CA"))
        page = self.client.get(f"/app/home/{listing.pk}/?created=1").content.decode()
        self.assertIn("آگهی شما ثبت شد", page)
        self.assertIn("https://t.me/sara_home", page)
        self.assertIn(listing, list(publishable()))

    def test_english_posts_keep_an_english_summary(self) -> None:
        self.login(self.user, "en")
        self.client.post("/app/home/new/", {**OFFER, "description": "Bright room in North York near the subway, bills included."})
        listing = HomeListing.objects.get(post__source__username="app_ca")
        self.assertEqual((listing.summary_fa, listing.summary_en[:12]), ("", "Bright room "))

    def test_a_wanted_post_keeps_the_budget(self) -> None:
        self.client.post("/app/home/new/", {**OFFER, "kind": "wanted", "price": "1200"})
        listing = HomeListing.objects.get(post__source__username="app_ca")
        self.assertEqual((listing.kind, listing.price_max, listing.price_amount, listing.lister_type), ("wanted", 1200, None, "unknown"))

    def test_an_odd_price_waits_for_review(self) -> None:
        self.client.post("/app/home/new/", {**OFFER, "price": "95000"})
        listing = HomeListing.objects.get(post__source__username="app_ca")
        self.assertTrue(listing.needs_review)
        self.assertNotIn(f"/app/home/{listing.pk}/", self.feed("?country=CA"))
        page = self.client.get(f"/app/home/{listing.pk}/?created=1").content.decode()
        self.assertIn("بعد از یک بررسی کوتاه", page)

    def test_form_errors_are_shown_and_values_kept(self) -> None:
        for field, value, message in (
            ("city", "", "شهر را بنویسید"),
            ("description", "کوتاه", "توضیحات کوتاه است"),
            ("price", "", "مبلغ اجاره را بنویسید"),
            ("country", "FR", "کشور را انتخاب کنید"),
            ("property_type", "castle", "نوع ملک را انتخاب کنید"),
        ):
            page = self.client.post("/app/home/new/", {**OFFER, field: value}).content.decode()
            self.assertIn(message, page, field)
            self.assertIn("Yonge &amp; Finch", page)
        self.assertFalse(HomeListing.objects.exists())

    def test_without_a_telegram_username_a_phone_is_needed(self) -> None:
        self.user.telegram_username = None
        self.user.save()
        page = self.client.post("/app/home/new/", OFFER).content.decode()
        self.assertIn("یک راه تماس لازم است", page)
        self.client.post("/app/home/new/", {**OFFER, "phone": "647-555-0142"})
        listing = HomeListing.objects.get(post__source__username="app_ca")
        self.assertEqual((listing.contact_telegram, listing.contact_phone), ("", "+16475550142"))

    def test_photos_are_stored(self) -> None:
        with tempfile.TemporaryDirectory() as root, override_settings(
            HOME_MEDIA_BACKEND="local", HOME_MEDIA_LOCAL_DIR=root, HOME_MEDIA_LOCAL_BASE="https://m.test"
        ):
            photos = [SimpleUploadedFile(f"p{i}.jpg", pattern_image(i), content_type="image/jpeg") for i in range(3)]
            broken = SimpleUploadedFile("x.jpg", b"not an image", content_type="image/jpeg")
            self.client.post("/app/home/new/", {**OFFER, "photos": photos + [broken]})
            listing = HomeListing.objects.get(post__source__username="app_ca")
            media = list(HomeMedia.objects.filter(post=listing.post).order_by("position"))
            self.assertEqual([item.position for item in media], [0, 1, 2])
            self.assertTrue(all(item.status == MediaStatus.STORED for item in media))
            self.assertTrue((Path(root) / media[0].storage_key).exists())
            listing.post.refresh_from_db()
            self.assertEqual(listing.post.photo_count, 3)
            self.assertIn("https://m.test/", self.feed("?country=CA"))

    def test_without_photo_storage_the_listing_still_posts(self) -> None:
        photo = SimpleUploadedFile("p.jpg", pattern_image(1), content_type="image/jpeg")
        self.client.post("/app/home/new/", {**OFFER, "photos": [photo]})
        listing = HomeListing.objects.get(post__source__username="app_ca")
        self.assertEqual(HomeMedia.objects.filter(post=listing.post).count(), 0)

    def test_app_sources_are_never_read_from_telegram(self) -> None:
        self.client.post("/app/home/new/", OFFER)
        from home.ingest import ingest_sources
        from tests.test_home import FakeReader

        result = ingest_sources(days=30, reader=FakeReader([]))
        self.assertNotIn("app_ca", result["sources"])


class MyListingsTests(MiniAppCase):
    def setUp(self) -> None:
        super().setUp()
        self.user.telegram_username = "sara_home"
        self.user.save()

    def test_mine_lists_only_my_posts_and_can_close_them(self) -> None:
        self.client.post("/app/home/new/", OFFER)
        mine = HomeListing.objects.get(post__source__username="app_ca")
        self.ca_listing(5)
        page = self.client.get("/app/home/mine/").content.decode()
        self.assertIn(f'href="/app/home/{mine.pk}/"', page)
        self.assertEqual(page.count('class="card listing-card house-card"'), 1)
        self.assertIn("بستن آگهی", page)
        self.client.post("/app/home/mine/", {"listing": mine.pk})
        mine.refresh_from_db()
        self.assertEqual(mine.status, ListingStatus.EXPIRED)
        self.assertIn("بسته شده", self.client.get("/app/home/mine/").content.decode())

    def test_nobody_can_close_someone_elses_listing(self) -> None:
        self.client.post("/app/home/new/", OFFER)
        mine = HomeListing.objects.get(post__source__username="app_ca")
        self.login(self.other)
        self.client.post("/app/home/mine/", {"listing": mine.pk})
        mine.refresh_from_db()
        self.assertEqual(mine.status, ListingStatus.ACTIVE)
        self.assertIn("هنوز آگهی‌ای ثبت نکرده‌اید", self.client.get("/app/home/mine/").content.decode())

    def test_a_bad_id_is_ignored(self) -> None:
        self.assertEqual(self.client.post("/app/home/mine/", {"listing": "abc"}).status_code, 302)
