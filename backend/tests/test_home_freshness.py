"""Freshness and safety: relative time, expiry, «هنوز موجوده؟», «اجاره رفت», reports, scam warning."""

from __future__ import annotations

from datetime import timedelta
from importlib import import_module
from unittest.mock import patch

from django.apps import apps as django_apps
from django.test import TestCase
from django.utils import timezone

from home import bot, freshness, status_posts
from home.botchat import handle_update, results_message
from home.job import run_home_job
from home.models import (
    AlertTerm,
    HomeAlert,
    HomeChannel,
    HomeListing,
    HomePublication,
    HomeReport,
    HomeSafetySeen,
    HomeSettings,
    HomeSource,
    ListingStatus,
    ReportReason,
    StatusPost,
)
from home.alerts import ALL_FIELDS, search
from home.submit import create_listing
from tests.helpers import make_user
from tests.test_home_alerts import FakeBot, tap
from tests.test_home_miniapp import MiniAppCase
from tests.test_home_publish import ca_listing

TOKEN = "123456:TESTTOKEN"
OWNER_ID = 6660001
ALI_ID = 6660002
SARA_ID = 6660003


def turn_on(**extra) -> HomeSettings:
    row, _ = HomeSettings.objects.update_or_create(pk=1, defaults={"bot_token": TOKEN, **extra})
    return row


def published(listing: HomeListing, country_ids=(11,), city_ids=(21, 22, 23)) -> HomeListing:
    """Pretend the listing went to the CA country channel and the Toronto city channel (an album + its button reply)."""
    country = HomeChannel.objects.get(country="CA", city="")
    toronto = HomeChannel.objects.get(country="CA", city="Toronto")
    listing.channel = country
    listing.channel_message_id = country_ids[0]
    listing.channel_message_ids = list(country_ids)
    listing.published_at = timezone.now()
    listing.save()
    HomePublication.objects.create(listing=listing, channel=toronto, message_ids=list(city_ids), published_at=timezone.now())
    return listing


def edits(fake: FakeBot) -> list[tuple[str, int]]:
    return [(name, payload["message_id"]) for name, payload in fake.calls if name.startswith("editMessage")]


class RelativeTimeTests(TestCase):
    def test_persian_words(self) -> None:
        now = timezone.now()
        self.assertEqual(freshness.relative_time(now - timedelta(seconds=20), now=now), "همین الان")
        self.assertEqual(freshness.relative_time(now + timedelta(minutes=5), now=now), "همین الان")
        self.assertEqual(freshness.relative_time(now - timedelta(minutes=8), now=now), "۸ دقیقه پیش")
        self.assertEqual(freshness.relative_time(now - timedelta(hours=3, minutes=10), now=now), "۳ ساعت پیش")
        self.assertEqual(freshness.relative_time(now - timedelta(hours=30), now=now), "دیروز")
        self.assertEqual(freshness.relative_time(now - timedelta(days=4, hours=2), now=now), "۴ روز پیش")
        self.assertEqual(freshness.relative_time(None), "")

    def test_english_words(self) -> None:
        now = timezone.now()
        self.assertEqual(freshness.relative_time(now, now=now, locale="en"), "just now")
        self.assertEqual(freshness.relative_time(now - timedelta(minutes=8), now=now, locale="en"), "8 min ago")
        self.assertEqual(freshness.relative_time(now - timedelta(hours=3), now=now, locale="en"), "3 h ago")
        self.assertEqual(freshness.relative_time(now - timedelta(hours=25), now=now, locale="en"), "yesterday")
        self.assertEqual(freshness.relative_time(now - timedelta(days=4), now=now, locale="en"), "4 days ago")

    def test_freshness_line_names_the_last_confirmation(self) -> None:
        HomeSource.objects.all().delete()
        listing = ca_listing(1)
        now = timezone.now()
        listing.last_seen = now - timedelta(hours=3)
        self.assertEqual(freshness.freshness_line(listing, now=now), "🕒 ۳ ساعت پیش")
        listing.last_confirmed_at = now - timedelta(hours=26)
        self.assertEqual(freshness.freshness_line(listing, now=now), "🕒 ۳ ساعت پیش · ✅ هنوز موجود (دیروز تأیید شد)")
        self.assertEqual(
            freshness.freshness_line(listing, now=now, locale="en"), "🕒 3 h ago · ✅ still available (confirmed yesterday)"
        )
        listing.status = ListingStatus.RENTED
        self.assertEqual(freshness.freshness_line(listing, now=now), "🕒 ۳ ساعت پیش")


class ExpiryTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        turn_on()

    def aged(self, number: int, days: float, **data) -> HomeListing:
        listing = ca_listing(number, **data)
        HomeListing.objects.filter(pk=listing.pk).update(last_seen=timezone.now() - timedelta(days=days))
        listing.refresh_from_db()
        return listing

    def test_offers_and_wanted_posts_expire_after_their_days(self) -> None:
        old_offer = self.aged(1, 15)
        fresh_offer = self.aged(2, 13)
        confirmed = self.aged(3, 20)
        HomeListing.objects.filter(pk=confirmed.pk).update(last_confirmed_at=timezone.now() - timedelta(days=2))
        wanted = self.aged(4, 20, kind="wanted", price_max="1200")
        old_wanted = self.aged(5, 22, kind="wanted", price_max="1200")
        result = freshness.expire_stale()
        self.assertEqual(result, {"expired_offer": 1, "expired_wanted": 1})
        status = dict(HomeListing.objects.values_list("pk", "status"))
        self.assertEqual(status[old_offer.pk], ListingStatus.EXPIRED)
        self.assertEqual(status[fresh_offer.pk], ListingStatus.ACTIVE)
        self.assertEqual(status[confirmed.pk], ListingStatus.ACTIVE)
        self.assertEqual(status[wanted.pk], ListingStatus.ACTIVE)
        self.assertEqual(status[old_wanted.pk], ListingStatus.EXPIRED)

    def test_the_days_are_a_setting(self) -> None:
        listing = self.aged(1, 6)
        HomeSettings.objects.filter(pk=1).update(expire_offer_days=5)
        freshness.expire_stale()
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.EXPIRED)

    def test_the_job_step_runs_expiry_and_reports_it(self) -> None:
        self.aged(1, 30)
        with patch("home.bot.call", FakeBot()):
            result = run_home_job(step="freshness")
        self.assertEqual(result["freshness"]["expired_offer"], 1)
        self.assertIn("owners", result["freshness"])
        self.assertIn("status_posts", result["freshness"])

    def test_the_whole_run_and_the_admin_buttons_include_it(self) -> None:
        from api.admin_ops import RUN_JOBS

        self.assertIn("home_freshness", RUN_JOBS)
        with patch("home.job.run_freshness", return_value={"ok": True}) as run, patch("home.job.acquire_lock", return_value=True):
            HomeSettings.objects.filter(pk=1).update(ingest_enabled=False, review_enabled=False, media_enabled=False)
            result = run_home_job(step="all")
        self.assertTrue(run.called)
        self.assertEqual(result["freshness"], {"ok": True})

    def test_rented_reported_and_expired_listings_leave_search_and_alerts(self) -> None:
        listings = [ca_listing(number) for number in range(1, 5)]
        for listing, status in zip(listings, (ListingStatus.RENTED, ListingStatus.REPORTED, ListingStatus.EXPIRED)):
            listing.status = status
            listing.save()
        alert = HomeAlert(telegram_user_id=1, chat_id=1, country="CA", city="Toronto", term=AlertTerm.ANY, answered=list(ALL_FIELDS))
        self.assertEqual([item.pk for item in search(alert)], [listings[3].pk])


class OwnerCheckTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        turn_on(owner_checks_enabled=True)
        self.fake = FakeBot()
        patcher = patch("home.bot.call", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.owner = make_user(telegram_user_id=OWNER_ID, first_name="Owner", telegram_username="owner_t")
        values = {
            "country": "CA",
            "kind": "offer",
            "property_type": "room",
            "city": "Toronto",
            "area": "North York",
            "price": "950",
            "description": "اتاق مبله نزدیک ایستگاه فینچ، قبوض داخل قیمت، از اول ماه.",
        }
        self.listing = create_listing(self.owner, values, [], locale="fa", countries=["CA", "DE"])
        HomeListing.objects.filter(pk=self.listing.pk).update(first_seen=timezone.now() - timedelta(days=8))
        self.listing.refresh_from_db()

    def test_after_seven_days_the_owner_is_asked_once(self) -> None:
        self.assertEqual(freshness.ask_owners()["asked"], 1)
        sent = self.fake.sent()
        self.assertEqual(sent[0]["chat_id"], OWNER_ID)
        self.assertIn("هنوز موجوده؟", sent[0]["text"])
        labels = [button["text"] for row in sent[0]["reply_markup"]["inline_keyboard"] for button in row]
        self.assertEqual(labels, ["✅ بله، هنوز هست", "🏁 اجاره رفت"])
        self.assertEqual(freshness.ask_owners()["asked"], 0)

    def test_a_young_or_crawled_listing_is_not_asked(self) -> None:
        HomeListing.objects.filter(pk=self.listing.pk).update(first_seen=timezone.now() - timedelta(days=3))
        crawled = ca_listing(1)
        HomeListing.objects.filter(pk=crawled.pk).update(first_seen=timezone.now() - timedelta(days=9))
        self.assertEqual(freshness.ask_owners()["asked"], 0)

    def test_yes_refreshes_the_confirmation_and_asks_again_a_week_later(self) -> None:
        freshness.ask_owners()
        handle_update(tap(f"hx:o:{self.listing.pk}:y", user_id=OWNER_ID))
        self.listing.refresh_from_db()
        self.assertIsNotNone(self.listing.last_confirmed_at)
        self.assertEqual(self.listing.status, ListingStatus.ACTIVE)
        self.assertIn("فعال ماند", self.fake.last_text())
        self.assertEqual(freshness.ask_owners()["asked"], 0)
        HomeListing.objects.filter(pk=self.listing.pk).update(
            confirm_asked_at=timezone.now() - timedelta(days=9), last_confirmed_at=timezone.now() - timedelta(days=8)
        )
        self.assertEqual(freshness.ask_owners()["asked"], 1)
        # The confirmation also keeps it from expiring.
        HomeListing.objects.filter(pk=self.listing.pk).update(last_seen=timezone.now() - timedelta(days=20), last_confirmed_at=timezone.now())
        freshness.expire_stale()
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, ListingStatus.ACTIVE)

    def test_rented_from_the_owner_closes_it_at_once(self) -> None:
        published(self.listing, country_ids=(31,), city_ids=(41,))
        handle_update(tap(f"hx:o:{self.listing.pk}:r", user_id=OWNER_ID))
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, ListingStatus.RENTED)
        self.assertEqual(self.listing.status_post, StatusPost.RENTED)
        self.assertIn("اجاره رفت", self.fake.last_text())
        texts = [payload["text"] for name, payload in self.fake.calls if name == "editMessageText" and payload["message_id"] in {31, 41}]
        self.assertEqual(len(texts), 2)
        self.assertIn("صاحب آگهی خبر داد", texts[0])

    def test_someone_else_cannot_answer_for_the_owner(self) -> None:
        handle_update(tap(f"hx:o:{self.listing.pk}:r", user_id=ALI_ID))
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, ListingStatus.ACTIVE)
        self.assertEqual(self.fake.sent("answerCallbackQuery")[-1]["text"], "این دکمه قدیمیه.")

    def test_answers_on_a_closed_listing(self) -> None:
        HomeListing.objects.filter(pk=self.listing.pk).update(status=ListingStatus.EXPIRED)
        handle_update(tap(f"hx:o:{self.listing.pk}:y", user_id=OWNER_ID))
        self.assertIn("دیگر فعال نیست", self.fake.last_text())
        HomeListing.objects.filter(pk=self.listing.pk).update(status=ListingStatus.RENTED)
        handle_update(tap(f"hx:o:{self.listing.pk}:r", user_id=OWNER_ID))
        self.assertIn("دیگر فعال نیست", self.fake.last_text())

    def test_telegram_trouble(self) -> None:
        self.fake.fail = {"sendMessage": bot.BotError("Too Many Requests", code=429, retry_after=7)}
        result = freshness.ask_owners()
        self.assertEqual((result["asked"], result["retry_after"]), (0, 7))
        self.listing.refresh_from_db()
        self.assertIsNone(self.listing.confirm_asked_at)
        self.fake.fail = {"sendMessage": bot.BotError("Forbidden: bot was blocked by the user", code=403)}
        self.assertEqual(freshness.ask_owners()["failed"], 1)
        self.assertEqual(freshness.ask_owners()["failed"], 0)

    def test_no_token_no_messages(self) -> None:
        HomeSettings.objects.filter(pk=1).update(bot_token="")
        with self.settings(HOME_BOT_TOKEN=""):
            self.assertIn("skipped", freshness.ask_owners())
        self.assertEqual(self.fake.calls, [])


class RentedChannelEditTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        turn_on()
        self.fake = FakeBot()
        patcher = patch("home.bot.call", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.listing = published(ca_listing(1))

    def test_two_people_mark_it_rented_and_every_post_is_edited(self) -> None:
        handle_update(tap(f"hx:t:{self.listing.pk}", user_id=ALI_ID))
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, ListingStatus.ACTIVE)
        self.assertEqual(edits(self.fake), [("editMessageReplyMarkup", 77)])
        handle_update(tap(f"hx:t:{self.listing.pk}", user_id=ALI_ID))
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, ListingStatus.ACTIVE)
        handle_update(tap(f"hx:t:{self.listing.pk}", user_id=SARA_ID))
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, ListingStatus.RENTED)
        self.assertIsNotNone(self.listing.rented_at)
        self.assertFalse(self.listing.status_post_pending)
        posts = [(name, payload) for name, payload in self.fake.calls if name in {"editMessageText", "editMessageCaption"}]
        ids = [payload["message_id"] for _name, payload in posts]
        self.assertEqual(ids, [11, 21, 23])
        country_chat = HomeChannel.objects.get(country="CA", city="").target()
        self.assertEqual(posts[0][1]["chat_id"], country_chat)
        text = posts[0][1]["text"]
        self.assertIn("✅ <b>اجاره رفت</b>", text)
        self.assertIn("<s>اتاق در خانه مشترک · Yonge &amp; Finch</s>", text)
        self.assertIn("۲ نفر گزارش دادند", text)
        for _name, payload in posts:
            self.assertEqual(payload["reply_markup"], {"inline_keyboard": []})
        self.assertEqual(posts[2][1]["text"], "✅ اجاره رفت")
        self.assertIn("«اجاره رفت» علامت خورد", self.fake.sent("answerCallbackQuery")[-1]["text"])
        self.assertEqual(HomeReport.objects.filter(listing=self.listing, reason=ReportReason.TAKEN).count(), 2)
        # Already rented: nothing more happens.
        self.assertEqual(freshness.mark_taken(self.listing, 1234), "closed")

    def test_a_photo_post_gets_the_template_as_its_caption(self) -> None:
        no_text = bot.BotError("Bad Request: there is no text in the message to edit", code=400)
        self.fake.fail = {"editMessageText": no_text}
        freshness.set_rented(self.listing)
        names = [name for name, _id in edits(self.fake)]
        self.assertEqual(names.count("editMessageCaption"), 2)
        captions = [payload for name, payload in self.fake.calls if name == "editMessageCaption"]
        self.assertEqual({payload["message_id"] for payload in captions}, {11, 21})
        self.assertIn("اجاره رفت", captions[0]["caption"])

    def test_not_modified_counts_as_done(self) -> None:
        self.fake.fail = {"editMessageText": bot.BotError("Bad Request: message is not modified", code=400)}
        freshness.set_rented(self.listing)
        self.listing.refresh_from_db()
        self.assertFalse(self.listing.status_post_pending)
        self.assertEqual(self.listing.status_post_error, "")

    def test_too_many_requests_waits_for_the_job(self) -> None:
        self.fake.fail = {"editMessageText": bot.BotError("Too Many Requests: retry after 9", code=429, retry_after=9)}
        result = freshness.set_rented(self.listing)
        self.assertTrue(result)
        self.listing.refresh_from_db()
        self.assertTrue(self.listing.status_post_pending)
        self.assertIn("retry", self.listing.status_post_error)
        self.assertEqual(status_posts.retry_pending()["retry_after"], 9)
        self.fake.fail = {}
        out = status_posts.retry_pending()
        self.assertEqual((out["edited"], out["waiting"]), (2, 0))
        self.listing.refresh_from_db()
        self.assertFalse(self.listing.status_post_pending)

    def test_other_refusals_are_noted_and_not_retried(self) -> None:
        self.fake.fail = {"editMessageText": bot.BotError("Bad Request: message to edit not found", code=400)}
        freshness.set_rented(self.listing)
        self.listing.refresh_from_db()
        self.assertFalse(self.listing.status_post_pending)
        self.assertIn("message to edit not found", self.listing.status_post_error)

    def test_without_a_token_the_edit_waits(self) -> None:
        HomeSettings.objects.filter(pk=1).update(bot_token="")
        with self.settings(HOME_BOT_TOKEN=""):
            freshness.set_rented(self.listing)
            self.listing.refresh_from_db()
            self.assertTrue(self.listing.status_post_pending)
            self.assertIn("skipped", status_posts.retry_pending())
        self.assertEqual(edits(self.fake), [])

    def test_an_unpublished_listing_needs_no_edit(self) -> None:
        other = ca_listing(2)
        freshness.set_rented(other)
        other.refresh_from_db()
        self.assertEqual(other.status, ListingStatus.RENTED)
        self.assertFalse(other.status_post_pending)
        self.assertEqual(edits(self.fake), [])

    def test_posts_are_never_deleted(self) -> None:
        freshness.set_rented(self.listing)
        self.assertFalse([name for name, _payload in self.fake.calls if "delete" in name.lower()])


class ReportTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        turn_on()
        self.fake = FakeBot()
        patcher = patch("home.bot.call", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.listing = published(ca_listing(1), city_ids=(21,))

    def test_the_report_button_shows_one_tap_reasons(self) -> None:
        handle_update(tap(f"hx:r:{self.listing.pk}", user_id=ALI_ID))
        markup = self.fake.sent("editMessageReplyMarkup")[-1]["reply_markup"]["inline_keyboard"]
        labels = [button["text"] for row in markup for button in row]
        self.assertEqual(labels, ["کلاهبرداری", "اطلاعات اشتباه", "تکراری", "دیگر", "↩️ برگشت"])
        handle_update(tap(f"hx:k:{self.listing.pk}", user_id=ALI_ID))
        back = self.fake.sent("editMessageReplyMarkup")[-1]["reply_markup"]["inline_keyboard"]
        self.assertIn("🚩 گزارش", [button["text"] for row in back for button in row])

    def test_reasons_are_stored(self) -> None:
        for code, reason in (("w", "wrong"), ("d", "duplicate"), ("o", "other")):
            handle_update(tap(f"hx:r:{self.listing.pk}:{code}", user_id=ALI_ID))
            self.assertTrue(HomeReport.objects.filter(listing=self.listing, reason=reason, telegram_user_id=ALI_ID).exists())
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, ListingStatus.ACTIVE)
        self.assertFalse(self.listing.needs_review)
        self.assertEqual(freshness.report(self.listing, "nonsense", ALI_ID), "ignored")

    def test_two_scam_reports_hide_it_and_edit_the_posts(self) -> None:
        handle_update(tap(f"hx:r:{self.listing.pk}:s", user_id=ALI_ID))
        handle_update(tap(f"hx:r:{self.listing.pk}:s", user_id=ALI_ID))
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, ListingStatus.ACTIVE)
        handle_update(tap(f"hx:r:{self.listing.pk}:s", user_id=SARA_ID))
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.status, ListingStatus.REPORTED)
        self.assertTrue(self.listing.needs_review)
        self.assertIn("scam by 2 people", self.listing.review_note)
        texts = [payload["text"] for name, payload in self.fake.calls if name == "editMessageText"]
        self.assertEqual(len(texts), 2)
        self.assertIn("⚠️ <b>این آگهی گزارش شده</b>", texts[0])
        self.assertIn("هیچ پولی نفرستید", texts[0])
        # A reported listing cannot be marked rented by users; an admin decides.
        self.assertEqual(freshness.mark_taken(self.listing, ALI_ID), "closed")

    def test_an_admin_restores_a_reported_listing(self) -> None:
        for user_id in (ALI_ID, SARA_ID):
            freshness.report(self.listing, ReportReason.SCAM, user_id)
        self.listing.refresh_from_db()
        self.fake.calls.clear()
        freshness.restore(self.listing)
        self.listing.refresh_from_db()
        self.assertEqual((self.listing.status, self.listing.needs_review), (ListingStatus.ACTIVE, False))
        texts = [payload for name, payload in self.fake.calls if name == "editMessageText"]
        self.assertIn("۹۵۰", texts[0]["text"])
        self.assertTrue(texts[0]["reply_markup"]["inline_keyboard"])

    def test_the_admin_filter_and_action(self) -> None:
        from django.contrib.admin.sites import site

        from home.admin import ReportFilter

        for user_id in (ALI_ID, SARA_ID):
            freshness.report(self.listing, ReportReason.SCAM, user_id)
        other = ca_listing(2)
        model_admin = site._registry[HomeListing]
        for value, expected in (("scam", [self.listing.pk]), ("any", [self.listing.pk]), ("taken", []), ("pending", []), (None, None)):
            found = ReportFilter(None, {"reports": [value]} if value else {}, HomeListing, model_admin).queryset(None, HomeListing.objects.all())
            if expected is None:
                self.assertEqual(found.count(), 2)
            else:
                self.assertEqual(sorted(found.values_list("pk", flat=True)), expected)
        self.assertTrue(other.pk)


class BotContactTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        turn_on()
        self.fake = FakeBot()
        patcher = patch("home.bot.call", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.listing = ca_listing(1, contact_telegram="sara_home")
        self.listing.contact_phone = "+16475550142"
        self.listing.save()

    def test_full_scam_warning_the_first_time_then_a_short_line(self) -> None:
        handle_update(tap(f"hx:c:{self.listing.pk}", user_id=ALI_ID))
        first = self.fake.last_text()
        self.assertIn("هیچ پولی (ودیعه، رزرو، کارت هدیه) نفرستید", first)
        self.assertIn("صاحبش را تأیید نکرده‌ایم", first)
        self.assertIn("در انتاریو فقط ودیعه", first)
        self.assertIn("<code>+16475550142</code>", first)
        self.assertIn("@sara_home", first)
        handle_update(tap(f"hx:c:{self.listing.pk}", user_id=ALI_ID))
        second = self.fake.last_text()
        self.assertIn("⚠️ قبل از دیدن خانه پولی نفرستید.", second)
        self.assertNotIn("کارت هدیه", second)
        self.assertIn("<code>+16475550142</code>", second)
        self.assertEqual(HomeSafetySeen.objects.filter(telegram_user_id=ALI_ID).count(), 1)

    def test_a_listing_with_no_way_to_reach_anyone(self) -> None:
        self.listing.contact_telegram = ""
        self.listing.contact_phone = ""
        self.listing.region = "BC"
        self.listing.save()
        self.listing.post.source_url = "https://t.me/c/1/2"
        self.listing.post.save()
        handle_update(tap(f"hx:c:{self.listing.pk}", user_id=ALI_ID))
        self.assertIn("راه تماسی در آگهی نیست", self.fake.last_text())
        self.assertNotIn("انتاریو", self.fake.last_text())

    def test_detail_shows_freshness_and_the_buttons(self) -> None:
        HomeListing.objects.filter(pk=self.listing.pk).update(last_seen=timezone.now() - timedelta(hours=3))
        handle_update(tap(f"hx:d:{self.listing.pk}", user_id=ALI_ID))
        text = self.fake.last_text()
        self.assertIn("🕒 ۳ ساعت پیش", text)
        labels = [button["text"] for button in self.fake.buttons()]
        self.assertEqual(
            labels,
            ["💬 تماس", "✅ اجاره رفته؟ خبر بده", "🚩 گزارش", "👍 هنوز موجوده", "🏠 جزئیات و عکس‌ها در چمدون خانه"],
        )
        handle_update(tap(f"hx:s:{self.listing.pk}", user_id=ALI_ID))
        self.listing.refresh_from_db()
        self.assertIsNotNone(self.listing.last_confirmed_at)
        self.assertIn("هنوز موجود است", self.fake.sent("answerCallbackQuery")[-1]["text"])
        handle_update(tap(f"hx:d:{self.listing.pk}", user_id=ALI_ID))
        self.assertIn("✅ هنوز موجود (همین الان تأیید شد)", self.fake.last_text())

    def test_detail_of_a_rented_listing_has_no_actions(self) -> None:
        self.listing.status = ListingStatus.RENTED
        self.listing.save()
        handle_update(tap(f"hx:d:{self.listing.pk}", user_id=ALI_ID))
        self.assertIn("این آگهی اجاره رفته است", self.fake.last_text())
        self.assertNotIn("🚩 گزارش", [button["text"] for button in self.fake.buttons()])
        handle_update(tap(f"hx:s:{self.listing.pk}", user_id=ALI_ID))
        self.assertEqual(self.fake.sent("answerCallbackQuery")[-1]["text"], "این آگهی دیگر فعال نیست.")
        handle_update(tap(f"hx:t:{self.listing.pk}", user_id=ALI_ID))
        self.assertEqual(self.fake.sent("answerCallbackQuery")[-1]["text"], "این آگهی دیگر فعال نیست.")

    def test_old_or_broken_buttons(self) -> None:
        for data in ("hx:d:999999", "hx:d:abc", "hx:z", f"hx:z:{self.listing.pk}", f"hx:r:{self.listing.pk}:q"):
            handle_update(tap(data, user_id=ALI_ID))
            self.assertEqual(self.fake.sent("answerCallbackQuery")[-1]["text"], "این دکمه قدیمیه.")
        handle_update({"callback_query": {"id": "x", "data": f"hx:d:{self.listing.pk}", "from": {}}})
        self.assertEqual(self.fake.sent("answerCallbackQuery")[-1]["callback_query_id"], "x")

    def test_a_repost_opens_its_original(self) -> None:
        repost = ca_listing(2)
        repost.status = ListingStatus.DUPLICATE
        repost.canonical = self.listing
        repost.save()
        handle_update(tap(f"hx:d:{repost.pk}", user_id=ALI_ID))
        self.assertIn(f"home_{self.listing.pk}", str(self.fake.buttons()))

    def test_buttons_without_a_message_send_new_ones(self) -> None:
        update = tap(f"hx:r:{self.listing.pk}", user_id=ALI_ID)
        del update["callback_query"]["message"]
        handle_update(update)
        self.assertIn("دلیل گزارش", self.fake.last_text())

    def test_search_results_carry_freshness_and_detail_buttons(self) -> None:
        criteria = HomeAlert(telegram_user_id=ALI_ID, chat_id=ALI_ID, country="CA", city="Toronto", term=AlertTerm.ANY, answered=list(ALL_FIELDS))
        text, keyboard = results_message(criteria, 0, "sr:c:1")
        self.assertIn("🕒", text)
        self.assertEqual(keyboard[0][0]["callback_data"], f"hx:d:{self.listing.pk}")

    def test_alerts_offer_contact_and_report(self) -> None:
        from home.alerts import alert_keyboard

        alert = HomeAlert.objects.create(telegram_user_id=ALI_ID, chat_id=ALI_ID, country="CA", city="Toronto")
        data = [button.get("callback_data") for row in alert_keyboard(alert, self.listing, None)["inline_keyboard"] for button in row]
        self.assertIn(f"hx:c:{self.listing.pk}", data)
        self.assertIn(f"hx:r:{self.listing.pk}", data)


class MiniAppFreshnessTests(MiniAppCase):
    def setUp(self) -> None:
        super().setUp()
        turn_on()
        self.fake = FakeBot()
        patcher = patch("home.bot.call", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_cards_and_detail_show_the_freshness_line(self) -> None:
        listing = self.ca_listing(1)
        self.assertIn("🕒 ۵ ساعت پیش", self.feed("?country=CA"))
        self.client.post(f"/app/home/{listing.pk}/", {"reason": "available"})
        page = self.client.get(f"/app/home/{listing.pk}/").content.decode()
        self.assertIn("✅ هنوز موجود (همین الان تأیید شد)", page)

    def test_detail_offers_rented_still_there_and_report_reasons(self) -> None:
        listing = self.ca_listing(1)
        page = self.client.get(f"/app/home/{listing.pk}/").content.decode()
        for label in ("✅ اجاره رفته؟ خبر بده", "👍 هنوز موجوده", "🚩 گزارش", "کلاهبرداری", "اطلاعات اشتباه", "تکراری", "دیگر"):
            self.assertIn(label, page)
        self.assertIn('id="contact-sheet"', page)

    def test_phone_numbers_stay_visible(self) -> None:
        listing = self.ca_listing(1, contact_telegram="sara_home")
        listing.contact_phone = "+16475550142"
        listing.save()
        for _visit in range(2):
            page = self.client.get(f"/app/home/{listing.pk}/").content.decode()
            self.assertIn('href="tel:+16475550142"', page)
            self.assertIn(">+16475550142<", page)
        self.assertNotIn('id="contact-sheet"', page)

    def test_two_scam_reports_hide_it_from_the_feed(self) -> None:
        listing = self.ca_listing(1)
        response = self.client.post(f"/app/home/{listing.pk}/", {"reason": "scam"})
        self.assertEqual(response["Location"], f"/app/home/{listing.pk}/?reported=1")
        self.login(self.other)
        response = self.client.post(f"/app/home/{listing.pk}/", {"reason": "scam"})
        self.assertEqual(response["Location"], f"/app/home/{listing.pk}/?reported=1&outcome=hidden")
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.REPORTED)
        self.assertNotIn(f"/app/home/{listing.pk}/", self.feed("?country=CA"))
        self.assertIn("تا بررسی پنهان شد", self.client.get(f"/app/home/{listing.pk}/?reported=1&outcome=hidden").content.decode())

    def test_a_rented_listing_still_opens_with_a_banner(self) -> None:
        listing = self.ca_listing(1)
        self.client.post(f"/app/home/{listing.pk}/", {"reason": "taken"})
        self.login(self.other)
        response = self.client.post(f"/app/home/{listing.pk}/", {"reason": "taken"})
        self.assertIn("outcome=rented", response["Location"])
        page = self.client.get(response["Location"]).content.decode()
        self.assertIn("این آگهی اجاره رفته است", page)
        self.assertIn("علامت خورد", page)
        self.assertNotIn('value="taken"', page)
        self.assertNotIn(f"/app/home/{listing.pk}/", self.feed("?country=CA"))
        self.assertEqual(self.client.post(f"/app/home/{listing.pk}/", {"reason": "available"})["Location"], f"/app/home/{listing.pk}/?reported=1&outcome=closed")

    def test_the_owner_sees_owner_buttons_and_closes_it_alone(self) -> None:
        self.user.telegram_username = "sara_t"
        self.user.save()
        values = {
            "country": "CA",
            "kind": "offer",
            "property_type": "room",
            "city": "Toronto",
            "price": "900",
            "description": "اتاق مبله نزدیک ایستگاه فینچ، قبوض داخل قیمت، از اول ماه.",
        }
        listing = create_listing(self.user, values, [], locale="fa", countries=["CA", "DE"])
        page = self.client.get(f"/app/home/{listing.pk}/").content.decode()
        self.assertIn("🏁 اجاره رفت", page)
        self.assertIn("✅ بله، هنوز هست", page)
        self.assertNotIn('value="scam"', page)
        self.client.post(f"/app/home/{listing.pk}/", {"reason": "taken"})
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.RENTED)
        self.login(self.other)
        other_page = self.client.get(f"/app/home/{listing.pk}/").content.decode()
        self.assertIn("این آگهی اجاره رفته است", other_page)

    def test_english_labels(self) -> None:
        self.login(self.user, "en")
        listing = self.ca_listing(1)
        page = self.client.get(f"/app/home/{listing.pk}/").content.decode()
        self.assertIn("Rented already? Tell us", page)
        self.assertIn("Wrong information", page)
        self.assertIn("🕒 5 h ago", page)


class MigrationTests(TestCase):
    def test_old_reports_get_the_telegram_id(self) -> None:
        HomeSource.objects.all().delete()
        listing = ca_listing(1)
        user = make_user(telegram_user_id=4440001)
        report = HomeReport.objects.create(listing=listing, user=user, reason=ReportReason.TAKEN)
        migration = import_module("home.migrations.0019_freshness")
        migration.copy_telegram_ids(django_apps, None)
        report.refresh_from_db()
        self.assertEqual(report.telegram_user_id, 4440001)
        # The same person in the bot is not counted twice.
        freshness.mark_taken(listing, 4440001)
        self.assertEqual(freshness.reporters(listing, ReportReason.TAKEN), 1)


class SwitchTests(TestCase):
    """freshness_enabled (on by default): expiry and channel edits. owner_checks_enabled (off by default): owner DMs."""

    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        turn_on()
        self.fake = FakeBot()
        patcher = patch("home.bot.call", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def owner_listing(self) -> HomeListing:
        owner = make_user(telegram_user_id=OWNER_ID, telegram_username="owner_t")
        values = {
            "country": "CA",
            "kind": "offer",
            "property_type": "room",
            "city": "Toronto",
            "price": "950",
            "description": "اتاق مبله نزدیک ایستگاه فینچ، قبوض داخل قیمت، از اول ماه.",
        }
        listing = create_listing(owner, values, [], locale="fa", countries=["CA", "DE"])
        HomeListing.objects.filter(pk=listing.pk).update(first_seen=timezone.now() - timedelta(days=8))
        return listing

    def test_defaults(self) -> None:
        row = HomeSettings.load()
        self.assertTrue(row.freshness_enabled)
        self.assertFalse(row.owner_checks_enabled)
        fresh = HomeSettings(pk=99)
        self.assertEqual((fresh.freshness_enabled, fresh.owner_checks_enabled), (True, False))

    def test_owner_checks_stay_off_until_switched_on(self) -> None:
        self.owner_listing()
        result = run_home_job(step="freshness")["freshness"]
        self.assertEqual(result["owners"], {"asked": 0, "failed": 0, "retry_after": 0, "skipped": freshness.OWNER_CHECKS_OFF})
        self.assertEqual(self.fake.sent(), [])
        HomeSettings.objects.filter(pk=1).update(owner_checks_enabled=True)
        self.assertEqual(run_home_job(step="freshness")["freshness"]["owners"]["asked"], 1)
        # Freshness off switches the owner checks off too.
        HomeSettings.objects.filter(pk=1).update(freshness_enabled=False)
        self.assertIn("skipped", freshness.ask_owners())

    def test_freshness_off_skips_the_step_and_keeps_listings(self) -> None:
        listing = ca_listing(1)
        HomeListing.objects.filter(pk=listing.pk).update(last_seen=timezone.now() - timedelta(days=40))
        HomeSettings.objects.filter(pk=1).update(freshness_enabled=False)
        result = run_home_job(step="freshness")
        self.assertEqual(result["freshness"], {"ok": True, "skipped": "freshness is switched off in Home settings"})
        self.assertEqual(freshness.run_freshness(), {"ok": True, "skipped": freshness.FRESHNESS_OFF})
        with patch("home.job.acquire_lock", return_value=True):
            HomeSettings.objects.filter(pk=1).update(ingest_enabled=False, review_enabled=False, media_enabled=False)
            whole = run_home_job(step="all")
        self.assertEqual(whole["freshness"]["skipped"], "freshness is switched off in Home settings")
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.ACTIVE)

    def test_freshness_off_holds_channel_edits_until_it_is_back_on(self) -> None:
        listing = published(ca_listing(1))
        HomeSettings.objects.filter(pk=1).update(freshness_enabled=False)
        for user_id in (ALI_ID, SARA_ID):
            freshness.mark_taken(listing, user_id)
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.RENTED)
        self.assertTrue(listing.status_post_pending)
        self.assertEqual(edits(self.fake), [])
        HomeSettings.objects.filter(pk=1).update(freshness_enabled=True)
        result = run_home_job(step="freshness")["freshness"]["status_posts"]
        self.assertEqual((result["edited"], result["waiting"]), (2, 0))
        self.assertEqual([message_id for _name, message_id in edits(self.fake)], [11, 21, 23])

    def test_health_page_and_settings_form_show_both(self) -> None:
        from django.contrib.auth.models import User as StaffUser

        from api.config_check import MISSING, OFF, OK, config_report

        def feature(key):
            return next(item for item in config_report() if item.key == key)

        self.assertEqual(feature("housing_freshness").state, OK)
        self.assertEqual(feature("housing_owner_checks").state, OFF)
        HomeSettings.objects.filter(pk=1).update(owner_checks_enabled=True)
        self.assertEqual(feature("housing_owner_checks").state, OK)
        HomeSettings.objects.filter(pk=1).update(bot_token="")
        with self.settings(HOME_BOT_TOKEN=""):
            self.assertEqual(feature("housing_owner_checks").missing, ["HOME_BOT_TOKEN"])
            self.assertEqual(feature("housing_freshness").state, MISSING)
        HomeSettings.objects.filter(pk=1).update(freshness_enabled=False)
        self.assertEqual(feature("housing_freshness").state, OFF)
        self.assertEqual(feature("housing_owner_checks").state, OFF)

        staff = StaffUser.objects.create_user("ops", password="pw", is_staff=True, is_superuser=True)
        self.client.force_login(staff)
        health = self.client.get("/admin/ops/health/").content.decode()
        self.assertIn("Housing freshness", health)
        self.assertIn("Housing owner checks", health)
        form = self.client.get("/admin/home/homesettings/1/change/").content.decode()
        self.assertIn('name="freshness_enabled"', form)
        self.assertIn('name="owner_checks_enabled"', form)
        from home.status import home_status

        switches = home_status()["switches"]
        self.assertEqual((switches["freshness"], switches["owner_checks"]), (False, False))
