from __future__ import annotations

import tempfile
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from home.models import HomeListing, HomeMedia, HomeReport, HomeSettings, HomeSource, ListingStatus, MediaStatus
from home.publish import format_listing_text, publish_pending
from home.review import apply_review
from item_requests.seed import seed_catalog
from miniapp.auth import SESSION_USER_KEY, startapp_from_request, startapp_path
from miniapp.i18n import SESSION_LOCALE_KEY
from tests.helpers import make_user
from tests.test_home import ACCEPT, make_post, make_source
from tests.test_home_photos import CA_ACCEPT, CA_TEXT_NO_PHONE, ca_source
from tests.test_telegram_auth import build_init_data

COURIER_TOKEN = "courier-bot-token"
HOME_TOKEN = "777:HOMEBOT"


def recent(listing_source: HomeSource, message_id: int, text: str, data: dict, *, hours: int = 5) -> HomeListing:
    post = make_post(
        listing_source,
        message_id,
        text=text,
        posted_at=timezone.now() - timedelta(hours=hours, minutes=message_id),
        source_url=f"https://t.me/{listing_source.username}/{message_id}",
    )
    apply_review(post, data)
    return HomeListing.objects.get(post=post)


class MiniAppCase(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.ca = ca_source()
        self.de = make_source()
        self.user = make_user(telegram_user_id=880001, first_name="Sara")
        self.other = make_user(telegram_user_id=880002, first_name="Ali")
        self.login(self.user)

    def login(self, user, locale: str = "fa") -> None:
        session = self.client.session
        session[SESSION_USER_KEY] = user.pk
        session[SESSION_LOCALE_KEY] = locale
        session.save()

    def ca_listing(self, number: int = 1, **data) -> HomeListing:
        data.setdefault("contact_telegram", f"ca_user{number}")
        data.setdefault("contact_phone", "")
        return recent(self.ca, number, CA_TEXT_NO_PHONE + f" شماره {number}", {**CA_ACCEPT, **data})

    def de_listing(self, number: int = 1, **data) -> HomeListing:
        data.setdefault("contact_telegram", f"de_user{number}")
        text = f"اتاق در برلین نویکلن، ۵۲۰ یورو گرم، از اول نوامبر، آگهی شماره {number} برای اجاره."
        return recent(self.de, number, text, {**ACCEPT, **data})

    def feed(self, query: str = "") -> str:
        return self.client.get(f"/app/home/list/{query}", HTTP_X_CHAMEDOON_LIST="1").content.decode()


class FeedTests(MiniAppCase):
    def test_signed_out_users_go_to_login_and_come_back_to_home(self) -> None:
        self.client.logout()
        response = self.client.get("/app/home/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/app/login/?startapp=home")
        response = self.client.get("/app/home/12/")
        self.assertEqual(response["Location"], "/app/login/?startapp=home_12")

    def test_page_uses_the_chamedoon_layout_and_persian_text(self) -> None:
        self.ca_listing()
        page = self.client.get("/app/home/list/?country=CA").content.decode()
        self.assertIn('dir="rtl"', page)
        self.assertIn("IRANSansWeb_FaNum", page)
        self.assertIn("miniapp/app.css", page)
        self.assertIn("آگهی‌ها", page)
        self.assertIn("دنبال خونه", page)
        self.assertIn('class="house-country-btn"', page)
        self.assertIn("listing-frame", page)
        self.assertNotIn("house-country-seg", page)

    def test_english_works_too(self) -> None:
        self.login(self.user, "en")
        self.ca_listing()
        page = self.client.get("/app/home/list/?country=CA").content.decode()
        self.assertIn('dir="ltr"', page)
        self.assertIn("Listings", page)
        self.assertIn("Wanted", page)
        fragment = self.feed("?country=CA")
        self.assertIn("Room in a shared home", fragment)
        self.assertIn("950 CA$", fragment)
        self.assertIn("per month", fragment)

    def test_feed_lists_cards_for_the_chosen_country(self) -> None:
        self.ca_listing(1)
        self.de_listing(2)
        canada = self.feed("?country=CA")
        self.assertIn("chamedoon-list", canada)
        self.assertIn('data-list-count="1"', canada)
        self.assertIn("950 CA$", canada)
        self.assertIn("اتاق در خانه مشترک", canada)
        germany = self.feed("?country=DE")
        self.assertIn("520 €", germany)
        self.assertNotIn("CA$", germany)

    def test_the_chosen_country_is_remembered(self) -> None:
        self.de_listing(2)
        self.client.get("/app/home/list/?country=DE")
        self.assertIn("520 €", self.feed(""))

    def test_hidden_listings_never_show(self) -> None:
        shown = self.ca_listing(1)
        review = self.ca_listing(2)
        review.needs_review = True
        review.save()
        expired = self.ca_listing(3)
        expired.status = ListingStatus.EXPIRED
        expired.save()
        dup = self.ca_listing(4)
        dup.status = ListingStatus.DUPLICATE
        dup.save()
        old = self.ca_listing(5)
        HomeListing.objects.filter(pk=old.pk).update(last_seen=timezone.now() - timedelta(days=40))
        fragment = self.feed("?country=CA")
        self.assertIn(f'href="/app/home/{shown.pk}/"', fragment)
        for hidden in (review, expired, dup, old):
            self.assertNotIn(f'href="/app/home/{hidden.pk}/"', fragment)

    def test_filters(self) -> None:
        toronto = self.ca_listing(1, city="Toronto", price_amount=900)
        montreal = self.ca_listing(2, city="Montreal", region="QC", price_amount=1500, property_type="apartment")
        agent = self.ca_listing(3, city="Toronto", price_amount=800, lister_type="agent")
        agent.lister_type = "agent"
        agent.save()
        later = self.ca_listing(4, city="Toronto", price_amount=700, available_from="2027-03-01", furnished="no")
        self.assertNotIn(f"/app/home/{montreal.pk}/", self.feed("?country=CA&city=Toronto"))
        self.assertIn(f"/app/home/{montreal.pk}/", self.feed("?country=CA&type=apartment"))
        self.assertNotIn(f"/app/home/{toronto.pk}/", self.feed("?country=CA&type=apartment"))
        self.assertNotIn(f"/app/home/{montreal.pk}/", self.feed("?country=CA&max=1000"))
        self.assertNotIn(f"/app/home/{agent.pk}/", self.feed("?country=CA&owner=1"))
        self.assertNotIn(f"/app/home/{later.pk}/", self.feed("?country=CA&furnished=1"))
        by = (date.today() + timedelta(days=40)).isoformat()
        self.assertNotIn(f"/app/home/{later.pk}/", self.feed(f"?country=CA&by={by}"))
        self.assertIn(f"/app/home/{toronto.pk}/", self.feed(f"?country=CA&by={by}"))

    def test_wanted_tab_shows_budgets(self) -> None:
        wanted = self.de_listing(1, kind="wanted", price_amount=None, price_max=1000, property_type="apartment", city="Frankfurt")
        fragment = self.feed("?country=DE&kind=wanted")
        self.assertIn(f"/app/home/{wanted.pk}/", fragment)
        self.assertIn("بودجه تا", fragment)
        self.assertNotIn(f"/app/home/{wanted.pk}/", self.feed("?country=DE&kind=offer"))

    def test_filter_sheet_offers_countries_cities_and_types_with_counts(self) -> None:
        self.ca_listing(1, city="Toronto")
        self.ca_listing(2, city="Toronto")
        page = self.client.get("/app/home/list/?country=CA").content.decode()
        self.assertIn('href="/app/home/list/?country=DE&amp;kind=offer"', page)
        self.assertIn('data-city="Toronto"', page)
        self.assertIn("تورنتو", page)
        self.assertIn('value="room"', page)

    def test_empty_feed_offers_to_clear_filters(self) -> None:
        fragment = self.feed("?country=CA&city=Nowhere")
        self.assertIn("آگهی‌ای با این فیلترها پیدا نشد", fragment)
        self.assertIn("پاک کردن فیلترها", fragment)


@override_settings(HOME_MEDIA_BACKEND="local", HOME_MEDIA_LOCAL_BASE="https://m.test")
class PhotoTests(MiniAppCase):
    def test_cards_and_detail_show_stored_photos(self) -> None:
        listing = self.ca_listing(1)
        with tempfile.TemporaryDirectory() as root, override_settings(HOME_MEDIA_LOCAL_DIR=root):
            for position in range(3):
                HomeMedia.objects.create(
                    source=self.ca,
                    post=listing.post,
                    telegram_message_id=500 + position,
                    position=position,
                    status=MediaStatus.STORED,
                    storage_key=f"home/ca/{position}-f.jpg",
                    thumb_key=f"home/ca/{position}-t.jpg",
                )
            HomeMedia.objects.create(source=self.ca, post=listing.post, telegram_message_id=599, position=9, status=MediaStatus.PENDING)
            fragment = self.feed("?country=CA")
            self.assertIn('src="https://m.test/home/ca/0-t.jpg"', fragment)
            self.assertIn('class="house-thumb-count">3<', fragment)
            page = self.client.get(f"/app/home/{listing.pk}/").content.decode()
            self.assertEqual(page.count('class="house-gallery-item"'), 3)
            self.assertIn("https://m.test/home/ca/2-f.jpg", page)
            self.assertIn("disableVerticalSwipes", page)


class DetailTests(MiniAppCase):
    def test_detail_shows_the_facts_contact_and_warning(self) -> None:
        listing = self.ca_listing(1, contact_telegram="sara_home", flags={"damage_deposit": True})
        listing.contact_phone = "+16475550142"
        listing.save()
        page = self.client.get(f"/app/home/{listing.pk}/").content.decode()
        self.assertIn("950 CA$", page)
        self.assertIn("فقط خانم", page)
        self.assertIn('href="https://t.me/sara_home"', page)
        self.assertIn('href="tel:+16475550142"', page)
        self.assertIn("کپی شماره", page)
        self.assertIn("در انتاریو فقط ودیعه", page)
        self.assertIn("هرگز قبل از دیدن خانه", page)
        self.assertIn(f"https://t.me/RentToronto/1", page)
        self.assertIn("اجاره رفت", page)

    def test_private_group_links_are_never_shown(self) -> None:
        listing = self.ca_listing(1, contact_telegram="")
        listing.contact_telegram = ""
        listing.save()
        listing.post.source_url = "https://t.me/c/123/9"
        listing.post.save()
        page = self.client.get(f"/app/home/{listing.pk}/").content.decode()
        self.assertNotIn("t.me/c/", page)
        self.assertIn("راه تماسی در آگهی نیست", page)

    def test_english_page_uses_the_english_summary(self) -> None:
        self.login(self.user, "en")
        listing = self.ca_listing(1, summary_en="Furnished room in North York, bills included.")
        page = self.client.get(f"/app/home/{listing.pk}/").content.decode()
        self.assertIn("Furnished room in North York, bills included.", page)
        self.assertIn("Message on Telegram", page)
        self.assertIn("Never send money before you have seen the place.", page)

    def test_english_falls_back_to_the_persian_summary(self) -> None:
        self.login(self.user, "en")
        listing = self.ca_listing(1, summary_en="")
        page = self.client.get(f"/app/home/{listing.pk}/").content.decode()
        self.assertIn(listing.summary_fa, page)

    def test_a_repost_opens_the_original(self) -> None:
        original = self.ca_listing(1)
        repost = self.ca_listing(2)
        repost.status = ListingStatus.DUPLICATE
        repost.canonical = original
        repost.save()
        response = self.client.get(f"/app/home/{repost.pk}/")
        self.assertEqual(response["Location"], f"/app/home/{original.pk}/")

    def test_rejected_or_missing_listings_are_not_found(self) -> None:
        self.assertEqual(self.client.get("/app/home/999999/").status_code, 404)
        listing = self.ca_listing(1)
        listing.post.status = "rejected"
        listing.post.save()
        self.assertEqual(self.client.get(f"/app/home/{listing.pk}/").status_code, 404)

    def test_two_people_saying_it_is_rented_expire_the_listing(self) -> None:
        listing = self.ca_listing(1)
        response = self.client.post(f"/app/home/{listing.pk}/", {"reason": "taken"})
        self.assertEqual(response["Location"], f"/app/home/{listing.pk}/?reported=1")
        self.client.post(f"/app/home/{listing.pk}/", {"reason": "taken"})
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.ACTIVE)
        self.login(self.other)
        self.client.post(f"/app/home/{listing.pk}/", {"reason": "taken"})
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.EXPIRED)
        self.assertEqual(HomeReport.objects.filter(listing=listing).count(), 2)
        self.assertIn("ممنون، ثبت شد", self.client.get(f"/app/home/{listing.pk}/?reported=1").content.decode())

    def test_a_problem_report_sends_the_listing_to_review(self) -> None:
        listing = self.ca_listing(1)
        self.client.post(f"/app/home/{listing.pk}/", {"reason": "problem"})
        listing.refresh_from_db()
        self.assertTrue(listing.needs_review)
        self.assertNotIn(f"/app/home/{listing.pk}/", self.feed("?country=CA"))

    def test_unknown_report_reasons_are_ignored(self) -> None:
        listing = self.ca_listing(1)
        self.client.post(f"/app/home/{listing.pk}/", {"reason": "delete_everything"})
        self.assertFalse(HomeReport.objects.exists())


class StartLinkTests(TestCase):
    def test_start_parameters_route_to_housing(self) -> None:
        self.assertEqual(startapp_path("home"), "/app/home/")
        self.assertEqual(startapp_path("home_42"), "/app/home/42/")
        self.assertEqual(startapp_path("home_x"), "/app/")

    def test_housing_paths_become_start_parameters(self) -> None:
        from django.test import RequestFactory

        factory = RequestFactory()
        self.assertEqual(startapp_from_request(factory.get("/app/home/42/")), "home_42")
        self.assertEqual(startapp_from_request(factory.get("/app/home/")), "home")

    def test_the_client_script_knows_the_housing_routes(self) -> None:
        from pathlib import Path

        import miniapp

        script = (Path(miniapp.__file__).parent / "static" / "miniapp" / "startapp.js").read_text(encoding="utf-8")
        self.assertIn('home: "/app/home/"', script)
        self.assertIn("/^home_(\\d+)$/", script)
        self.assertIn('"/app/home/": true', script)


@override_settings(TELEGRAM_BOT_TOKEN=COURIER_TOKEN)
class LoginBothBotsTests(TestCase):
    def test_the_courier_bot_still_logs_in(self) -> None:
        from users.telegram import parse_and_validate_init_data

        identity = parse_and_validate_init_data(build_init_data(telegram_user_id=1, first_name="A", bot_token=COURIER_TOKEN))
        self.assertEqual(identity.telegram_user_id, 1)

    def test_the_housing_bot_logs_in_once_its_token_is_saved(self) -> None:
        from users.exceptions import TelegramAuthError
        from users.telegram import parse_and_validate_init_data

        data = build_init_data(telegram_user_id=2, first_name="B", bot_token=HOME_TOKEN)
        with self.assertRaises(TelegramAuthError):
            parse_and_validate_init_data(data)
        HomeSettings.objects.update_or_create(pk=1, defaults={"bot_token": HOME_TOKEN})
        self.assertEqual(parse_and_validate_init_data(data).telegram_user_id, 2)

    def test_a_third_bot_is_refused(self) -> None:
        from users.exceptions import TelegramAuthError
        from users.telegram import parse_and_validate_init_data

        HomeSettings.objects.update_or_create(pk=1, defaults={"bot_token": HOME_TOKEN})
        with self.assertRaises(TelegramAuthError):
            parse_and_validate_init_data(build_init_data(telegram_user_id=3, first_name="C", bot_token="999:OTHER"))


class ChannelLinkTests(MiniAppCase):
    def test_posts_carry_three_buttons_into_the_housing_bot(self) -> None:
        HomeSettings.objects.update_or_create(pk=1, defaults={"publish_enabled": True, "bot_token": "1:x", "bot_username": "chamedoon_home_bot"})
        listing = self.de_listing(1, price_amount=520)
        sent = {}
        with patch("home.publish.bot.send_message", side_effect=lambda chat, text, **kwargs: sent.update({chat: kwargs}) or [5]):
            publish_pending()
        rows = sent["@Chamedoon_Germany"]["buttons"]
        self.assertEqual([[button["text"] for button in row] for row in rows], [["جزئیات و عکس‌ها"], ["مشابه‌ها", "🔔 گوش به زنگ"]])
        self.assertEqual(rows[0][0]["url"], f"https://t.me/chamedoon_home_bot?startapp=home_de_berlin_l{listing.pk}_src-chde")
        self.assertEqual(rows[1][0]["url"], "https://t.me/chamedoon_home_bot?startapp=home_de_berlin_t-room_p400-650_src-chde")
        self.assertEqual(rows[1][1]["url"], "https://t.me/chamedoon_home_bot?start=alert_home_de_berlin")
        city_rows = next(kwargs["buttons"] for chat, kwargs in sent.items() if chat != "@Chamedoon_Germany")
        self.assertTrue(city_rows[0][0]["url"].endswith(f"startapp=home_de_berlin_l{listing.pk}_src-chber"))

    def test_the_old_mini_app_links_still_work(self) -> None:
        HomeSettings.objects.update_or_create(pk=1, defaults={"bot_username": "chamedoon_home_bot"})
        from home import conf

        self.assertEqual(conf.mini_app_link(7), "https://t.me/chamedoon_home_bot?startapp=home_7")
        self.assertEqual(conf.mini_app_link(), "https://t.me/chamedoon_home_bot?startapp=home")

    def test_the_channel_check_learns_the_bot_username(self) -> None:
        from home.models import HomeChannel
        from home.publish import verify_channel

        HomeSettings.objects.update_or_create(pk=1, defaults={"bot_token": "1:x"})
        with patch("home.publish.bot.check_channel", return_value={"chat_id": -1005, "title": "T", "bot": "chamedoon_home_bot"}):
            self.assertEqual(verify_channel(HomeChannel.objects.get(country="DE", city="")), "")
        self.assertEqual(HomeSettings.load().bot_username, "chamedoon_home_bot")


class CourierUntouchedTests(MiniAppCase):
    def test_the_main_chamedoon_home_is_unchanged(self) -> None:
        page = self.client.get("/app/").content.decode()
        self.assertNotIn("/app/home/", page)
        self.assertNotIn("چمدون خانه", page)

    def test_explore_still_works(self) -> None:
        self.assertEqual(self.client.get("/app/explore/").status_code, 200)
