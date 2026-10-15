from __future__ import annotations

import io
import json
import tempfile
import urllib.error
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User as StaffUser
from django.test import TestCase, override_settings
from django.utils import timezone

from home import bot
from home.guard import courier_channel_names, is_courier_channel
from home.job import run_home_job
from home.models import (
    HomeChannel,
    HomeListing,
    HomeMedia,
    HomeSettings,
    HomeSource,
    ListingStatus,
    MediaStatus,
)
from home.publish import (
    CAPTION_LIMIT,
    format_listing_text,
    persian_date,
    publish_listing,
    publish_pending,
    publish_preview,
    publishable,
    verify_channel,
)
from home.review import apply_review
from tests.test_home import ACCEPT, make_post, make_source
from tests.test_home_photos import CA_ACCEPT, CA_TEXT, CA_TEXT_NO_PHONE, ca_source

TOKEN = "123456:TESTTOKEN"


def turn_on(**extra) -> HomeSettings:
    values = {"publish_enabled": True, "bot_token": TOKEN, **extra}
    row, _ = HomeSettings.objects.update_or_create(pk=1, defaults=values)
    return row


def listing_for(source: HomeSource, message_id: int, text: str, data: dict, *, days_ago: float = 1.0) -> HomeListing:
    post = make_post(source, message_id, text=text, posted_at=timezone.now() - timedelta(days=days_ago, minutes=-message_id))
    apply_review(post, {**data})
    return HomeListing.objects.get(post=post)


def de_listing(message_id: int = 1, **data) -> HomeListing:
    source = HomeSource.objects.filter(username="berlin_khane").first() or make_source()
    text = f"اتاق ۱۶ متری مبله در برلین نویکلن، اجاره گرم ۵۲۰ یورو، کاتسیون یک ماه، از اول نوامبر شماره {message_id}. @sara_b"
    data.setdefault("contact_telegram", "sara_b" if message_id == 1 else f"user{message_id}")
    return listing_for(source, message_id, text, {**ACCEPT, **data})


def ca_listing(message_id: int = 1, **data) -> HomeListing:
    source = HomeSource.objects.filter(username="RentToronto").first() or ca_source()
    data.setdefault("contact_telegram", f"ca_user{message_id}")
    return listing_for(source, message_id, CA_TEXT_NO_PHONE + f" شماره {message_id}", {**CA_ACCEPT, "contact_phone": "", **data})


class FormatTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()

    def test_an_offer_reads_like_a_channel_post(self) -> None:
        text = format_listing_text(de_listing(), phone=False)
        self.assertIn("🏠 <b>اتاق در خانه مشترک · Berlin · Neukölln</b>", text)
        self.assertIn("<b>520 €</b> در ماه (گرم، با قبوض)", text)
        self.assertIn("📐 ۱۶ متر", text)
        self.assertIn("مبله", text)
        self.assertIn("ملد ✅", text)
        self.assertIn("📅 از ۱ نوامبر ۲۰۲۶", text)
        self.assertIn("👤 فقط خانم", text)
        self.assertIn("🧾 مالک یا مستاجر", text)
        self.assertIn("📞 تماس: @sara_b", text)
        self.assertIn("#Berlin #اتاق_در_خانه_مشترک", text)
        self.assertNotIn("<script", text)

    def test_a_wanted_post_shows_the_budget(self) -> None:
        listing = de_listing(kind="wanted", price_amount=None, price_max=1000, property_type="apartment", city="Frankfurt")
        text = format_listing_text(listing, phone=False)
        self.assertIn("🔎 <b>دنبال خونه: Frankfurt", text)
        self.assertIn("بودجه تا <b>1,000 €</b>", text)
        self.assertIn("#دنبال_خانه", text)
        self.assertNotIn("🧾", text)

    def test_missing_price_says_to_ask(self) -> None:
        text = format_listing_text(de_listing(price_amount=None), phone=False)
        self.assertIn("قیمت: از آگهی‌دهنده بپرسید", text)

    def test_canadian_post_uses_cad_and_the_ontario_warning(self) -> None:
        text = format_listing_text(ca_listing(), phone=False)
        self.assertIn("<b>950 CA$</b>", text)
        self.assertIn("⚠️ در انتاریو فقط ودیعه‌ی ماه آخر مجاز است", text)

    def test_the_phone_is_hidden_unless_the_setting_allows_it(self) -> None:
        listing = ca_listing(contact_phone="647-555-0142", contact_telegram="")
        listing.contact_phone = "+16475550142"
        listing.contact_telegram = ""
        listing.save()
        listing.post.source_url = "https://t.me/RentToronto/77"
        listing.post.save()
        hidden = format_listing_text(listing, phone=False)
        shown = format_listing_text(listing, phone=True)
        self.assertNotIn("+16475550142", hidden)
        self.assertIn("پست اصلی", hidden)
        self.assertIn("<code>+16475550142</code>", shown)

    def test_a_private_source_link_is_never_shown(self) -> None:
        listing = ca_listing()
        listing.contact_telegram = ""
        listing.save()
        listing.post.source_url = "https://t.me/c/123456/77"
        listing.post.save()
        text = format_listing_text(listing, phone=False)
        self.assertNotIn("t.me/c/", text)
        self.assertIn("ربات خانه", text)

    def test_summary_is_escaped(self) -> None:
        listing = de_listing()
        listing.summary_fa = "اتاق <b>عالی</b> & ارزان"
        listing.save()
        text = format_listing_text(listing, phone=False)
        self.assertIn("&lt;b&gt;عالی&lt;/b&gt; &amp; ارزان", text)

    def test_a_long_post_drops_low_priority_lines_first_and_fits_the_caption(self) -> None:
        listing = de_listing()
        # Longer than the database allows, on purpose: only the formatter sees it, nothing is saved.
        listing.summary_fa = "خلاصه " * 150
        listing.area = "محله " * 6
        text = format_listing_text(listing, limit=CAPTION_LIMIT, phone=False)
        self.assertLessEqual(len(text), CAPTION_LIMIT)
        self.assertIn("520 €", text)
        self.assertIn("📞 تماس", text)
        self.assertNotIn("خلاصه", text)

    def test_dates_use_persian_digits_and_month_names(self) -> None:
        from datetime import date

        self.assertEqual(persian_date(date(2027, 1, 5)), "۵ ژانویه ۲۰۲۷")

    def test_round_numbers_are_never_in_scientific_notation(self) -> None:
        listing = de_listing(price_amount=1450, size_m2=60, rooms=20, deposit_months=3)
        text = format_listing_text(listing, phone=False)
        self.assertIn("📐 ۶۰ متر", text)
        self.assertIn("🛏 ۲۰ اتاق", text)
        self.assertIn("🔐 ودیعه: ۳ ماه اجاره", text)
        self.assertNotIn("E+", text)

    def test_digits_in_the_summary_are_persian(self) -> None:
        listing = de_listing()
        listing.summary_fa = "آپارتمان 60 متری، 3500 یورو"
        listing.save()
        self.assertIn("آپارتمان ۶۰ متری، ۳۵۰۰ یورو", format_listing_text(listing, phone=False))

    def test_money_has_thousands_separators(self) -> None:
        listing = de_listing(price_amount=1450)
        self.assertIn("1,450 €", format_listing_text(listing, phone=False))


class WhatGoesOutTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()

    def test_only_trusted_active_unposted_recent_listings_are_candidates(self) -> None:
        good = de_listing(1)
        review = de_listing(2)
        review.needs_review = True
        review.save()
        dup = de_listing(3)
        dup.status = ListingStatus.DUPLICATE
        dup.save()
        posted = de_listing(4)
        posted.published_at = timezone.now()
        posted.save()
        old = de_listing(5)
        HomeListing.objects.filter(pk=old.pk).update(last_seen=timezone.now() - timedelta(days=10))
        tries = de_listing(6)
        tries.publish_attempts = 3
        tries.save()
        self.assertEqual([item.pk for item in publishable()], [good.pk])

    def test_a_country_without_a_channel_is_skipped(self) -> None:
        HomeChannel.objects.filter(country="DE").update(enabled=False)
        de_listing(1)
        self.assertEqual(list(publishable()), [])

    def test_preview_sends_nothing(self) -> None:
        de_listing(1)
        with patch("home.publish.bot.call") as call:
            rows = publish_preview()
        call.assert_not_called()
        self.assertEqual(len(rows), 1)
        self.assertIn("520 €", rows[0]["text"])


@override_settings(TELEGRAM_CHANNEL_USERNAME="chamed0on", TELEGRAM_CHANNEL_ID="-1001234567890")
class PublishTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        turn_on()
        self.dir = tempfile.TemporaryDirectory()
        self.storage = override_settings(
            HOME_MEDIA_BACKEND="local", HOME_MEDIA_LOCAL_DIR=self.dir.name, HOME_MEDIA_LOCAL_BASE="https://m.test"
        )

    def tearDown(self) -> None:
        self.dir.cleanup()

    def add_photos(self, listing: HomeListing, count: int, status=MediaStatus.STORED) -> None:
        for position in range(count):
            HomeMedia.objects.create(
                source=listing.post.source,
                post=listing.post,
                telegram_message_id=900 + listing.pk * 20 + position,
                position=position,
                status=status,
                storage_key=f"home/de/00000/{listing.pk}-{position}-f.jpg",
            )

    def test_blockers_are_explained(self) -> None:
        de_listing(1)
        HomeSettings.objects.filter(pk=1).update(publish_enabled=False)
        self.assertIn("switched off", publish_pending()["skipped"])
        HomeSettings.objects.filter(pk=1).update(publish_enabled=True, bot_token="")
        self.assertIn("token", publish_pending()["skipped"])
        HomeSettings.objects.filter(pk=1).update(bot_token=TOKEN)
        HomeChannel.objects.update(enabled=False)
        self.assertIn("channel", publish_pending()["skipped"])
        HomeChannel.objects.update(enabled=True)
        HomeSettings.objects.filter(pk=1).update(enabled=False)
        self.assertIn("switched off", publish_pending()["skipped"])

    def test_a_text_only_post_goes_to_its_countrys_channel(self) -> None:
        listing = de_listing(1)
        with patch("home.publish.bot.send_message", return_value=[501]) as send:
            result = publish_pending()
        self.assertEqual(result["published"], 1)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], "@Chamedoon_Germany")
        listing.refresh_from_db()
        self.assertEqual((listing.channel.username, listing.channel_message_id, listing.channel_message_ids), ("Chamedoon_Germany", 501, [501]))
        self.assertIsNotNone(listing.published_at)

    def test_each_country_has_its_own_channel(self) -> None:
        de = de_listing(1)
        ca = ca_listing(2)
        sent = []
        with patch("home.publish.bot.send_message", side_effect=lambda chat, text, **kwargs: sent.append(chat) or [len(sent)]):
            publish_pending()
        self.assertEqual(sorted(sent), ["@Chamedoon_CA", "@Chamedoon_Germany"])
        de.refresh_from_db()
        ca.refresh_from_db()
        self.assertEqual((de.channel.country, ca.channel.country), ("DE", "CA"))

    def test_a_listing_is_posted_once(self) -> None:
        de_listing(1)
        with patch("home.publish.bot.send_message", return_value=[1]) as send:
            publish_pending()
            publish_pending()
        self.assertEqual(send.call_count, 1)

    def test_one_photo_goes_as_a_photo_with_the_caption(self) -> None:
        listing = de_listing(1)
        self.add_photos(listing, 1)
        with self.storage, patch("home.publish.bot.send_photo", return_value=[7]) as photo:
            publish_pending()
        self.assertEqual(photo.call_args.args[1], f"https://m.test/home/de/00000/{listing.pk}-0-f.jpg")
        self.assertLessEqual(len(photo.call_args.args[2]), CAPTION_LIMIT)

    def test_several_photos_go_as_one_album(self) -> None:
        listing = de_listing(1)
        self.add_photos(listing, 4)
        with self.storage, patch("home.publish.bot.send_album", return_value=[10, 11, 12, 13]) as album:
            publish_pending()
        self.assertEqual(len(album.call_args.args[1]), 4)
        listing.refresh_from_db()
        self.assertEqual((listing.channel_message_id, listing.channel_message_ids), (10, [10, 11, 12, 13]))

    def test_a_post_waits_until_its_photos_are_stored(self) -> None:
        listing = de_listing(1)
        self.add_photos(listing, 2, status=MediaStatus.PENDING)
        with self.storage, patch("home.publish.bot.send_message") as send, patch("home.publish.bot.send_album") as album:
            result = publish_pending()
        self.assertEqual((result["published"], result["held_for_photos"]), (0, 1))
        send.assert_not_called()
        album.assert_not_called()

    def test_without_photo_storage_the_post_goes_out_as_text(self) -> None:
        listing = de_listing(1)
        self.add_photos(listing, 2, status=MediaStatus.PENDING)
        with patch("home.publish.bot.send_message", return_value=[3]) as send:
            publish_pending()
        send.assert_called_once()

    def test_telegram_refusing_the_photos_falls_back_to_text(self) -> None:
        listing = de_listing(1)
        self.add_photos(listing, 2)
        error = bot.BotError("Bad Request: failed to get HTTP URL content", code=400)
        with self.storage, patch("home.publish.bot.send_album", side_effect=error), patch(
            "home.publish.bot.send_message", return_value=[9]
        ) as send:
            result = publish_pending()
        self.assertEqual(result["published"], 1)
        send.assert_called_once()
        listing.refresh_from_db()
        self.assertIn("photos skipped", listing.publish_error)

    def test_a_forbidden_bot_stops_that_listing_and_counts_the_attempt(self) -> None:
        listing = de_listing(1)
        error = bot.BotError("Forbidden: bot is not a member of the channel chat", code=403)
        with patch("home.publish.bot.send_message", side_effect=error):
            result = publish_pending()
        self.assertEqual((result["published"], result["failed"]), (0, 1))
        listing.refresh_from_db()
        self.assertEqual(listing.publish_attempts, 1)
        self.assertIn("not a member", listing.publish_error)
        self.assertIn("not a member", HomeChannel.objects.get(country="DE").last_error)

    def test_a_rate_limit_stops_the_run(self) -> None:
        de_listing(1)
        de_listing(2)
        error = bot.BotError("Too Many Requests", code=429, retry_after=30)
        with patch("home.publish.bot.send_message", side_effect=error) as send:
            result = publish_pending()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result["retry_after"], 30)

    def test_the_batch_and_the_daily_cap_limit_a_run(self) -> None:
        for number in range(1, 5):
            de_listing(number)
        turn_on(publish_batch=2)
        with patch("home.publish.bot.send_message", return_value=[1]) as send:
            result = publish_pending()
        self.assertEqual((send.call_count, result["remaining"]), (2, 2))
        turn_on(publish_batch=10, publish_daily_cap=3)
        with patch("home.publish.bot.send_message", return_value=[1]) as send:
            result = publish_pending()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result["over_daily_cap"], 1)

    def test_housing_is_never_posted_to_the_courier_channel(self) -> None:
        listing = de_listing(1)
        channel = HomeChannel.objects.get(country="DE")
        channel.username = "Chamed0on"
        channel.save()
        with patch("home.publish.bot.send_message") as send:
            result = publish_pending()
        send.assert_not_called()
        self.assertEqual(result["failed"], 1)
        listing.refresh_from_db()
        self.assertIn("courier channel", listing.publish_error)
        channel.username = "Chamedoon_Germany"
        channel.chat_id = -1001234567890
        channel.save()
        with self.assertRaises(bot.BotError):
            publish_listing(listing, channel)

    def test_the_job_step_posts_and_respects_its_switch(self) -> None:
        de_listing(1)
        with patch("home.publish.bot.send_message", return_value=[1]):
            result = run_home_job(step="publish")
        self.assertEqual(result["publish"]["published"], 1)
        HomeSettings.objects.filter(pk=1).update(publish_enabled=False)
        self.assertIn("switched off", run_home_job(step="publish")["publish"]["skipped"])


class BotClientTests(TestCase):
    class Response:
        def __init__(self, body):
            self.body = json.dumps(body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self.body

    def test_a_good_call_returns_the_result_and_uses_the_given_token(self) -> None:
        captured = {}

        def fake(request, timeout=0):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data)
            return self.Response({"ok": True, "result": {"message_id": 5}})

        turn_on()
        with patch("home.bot.urllib.request.urlopen", fake):
            self.assertEqual(bot.send_message("@x", "hi"), [5])
        self.assertEqual(captured["url"], f"https://api.telegram.org/bot{TOKEN}/sendMessage")
        self.assertEqual(captured["body"]["parse_mode"], "HTML")

    def test_album_puts_the_caption_on_the_first_photo_only(self) -> None:
        captured = {}

        def fake(request, timeout=0):
            captured["body"] = json.loads(request.data)
            return self.Response({"ok": True, "result": [{"message_id": 1}, {"message_id": 2}]})

        turn_on()
        with patch("home.bot.urllib.request.urlopen", fake):
            self.assertEqual(bot.send_album("@x", ["u1", "u2"], "cap"), [1, 2])
        media = captured["body"]["media"]
        self.assertEqual((media[0]["caption"], "caption" in media[1]), ("cap", False))

    def test_telegram_errors_carry_the_code_and_retry_time(self) -> None:
        turn_on()
        body = {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 12}}
        with patch("home.bot.urllib.request.urlopen", return_value=self.Response(body)):
            with self.assertRaises(bot.BotError) as caught:
                bot.call("sendMessage", {})
        self.assertEqual((caught.exception.code, caught.exception.retry_after), (429, 12))

    def test_http_and_network_failures_become_bot_errors(self) -> None:
        turn_on()
        http = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b'{"ok": false, "error_code": 401, "description": "Unauthorized"}'))
        with patch("home.bot.urllib.request.urlopen", side_effect=http):
            with self.assertRaises(bot.BotError) as caught:
                bot.call("getMe")
        self.assertEqual(caught.exception.code, 401)
        with patch("home.bot.urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            with self.assertRaises(bot.BotError):
                bot.call("getMe")

    def test_a_missing_token_is_a_clear_error(self) -> None:
        with self.assertRaises(bot.BotError) as caught:
            bot.call("getMe")
        self.assertIn("token", caught.exception.description)

    @override_settings(HOME_BOT_TOKEN="999:ENV")
    def test_the_environment_token_is_the_fallback_and_settings_win(self) -> None:
        from home import conf

        self.assertEqual(conf.bot_token(), "999:ENV")
        turn_on()
        self.assertEqual(conf.bot_token(), TOKEN)


@override_settings(TELEGRAM_CHANNEL_USERNAME="chamed0on", TELEGRAM_CHANNEL_ID="-1001234567890", TELEGRAM_GROUP_USERNAME="chamedoon_group")
class ChannelCheckTests(TestCase):
    def setUp(self) -> None:
        turn_on()
        self.channel = HomeChannel.objects.get(country="DE")

    def answers(self, member):
        def fake(method, payload=None, token=None):
            return {
                "getChat": {"id": -1009876543210, "title": "Chamedoon Germany"},
                "getMe": {"id": 42, "username": "chamedoon_home_bot"},
                "getChatMember": member,
            }[method]

        return fake

    def test_an_admin_bot_is_accepted_and_the_chat_id_saved(self) -> None:
        with patch("home.bot.call", self.answers({"status": "administrator", "can_post_messages": True})):
            self.assertEqual(verify_channel(self.channel), "")
        self.channel.refresh_from_db()
        self.assertEqual((self.channel.chat_id, self.channel.title), (-1009876543210, "Chamedoon Germany"))
        self.assertIsNotNone(self.channel.verified_at)

    def test_a_bot_that_cannot_post_is_reported(self) -> None:
        with patch("home.bot.call", self.answers({"status": "administrator", "can_post_messages": False})):
            self.assertIn("cannot post", verify_channel(self.channel))
        with patch("home.bot.call", self.answers({"status": "member"})):
            self.assertIn("not an admin", verify_channel(self.channel))

    def test_the_courier_channel_is_refused_by_name_or_id(self) -> None:
        self.assertEqual(courier_channel_names() & {"chamed0on", "1234567890"}, {"chamed0on", "1234567890"})
        self.assertTrue(is_courier_channel("@Chamed0on"))
        self.assertTrue(is_courier_channel("", -1001234567890))
        self.assertTrue(is_courier_channel("chamedoon_group"))
        self.assertFalse(is_courier_channel("Chamedoon_CA"))
        self.channel.username = "chamed0on"
        self.channel.save()
        with patch("home.bot.call") as call:
            self.assertIn("courier channel", verify_channel(self.channel))
        call.assert_not_called()

    def test_a_channel_that_resolves_to_the_courier_chat_is_refused(self) -> None:
        def fake(method, payload=None, token=None):
            return {
                "getChat": {"id": -1001234567890, "title": "x"},
                "getMe": {"id": 1, "username": "b"},
                "getChatMember": {"status": "creator"},
            }[method]

        with patch("home.bot.call", fake):
            self.assertIn("courier channel", verify_channel(self.channel))
        self.channel.refresh_from_db()
        self.assertIsNone(self.channel.chat_id)


@override_settings(TELEGRAM_CHANNEL_USERNAME="chamed0on")
class PublishAdminTests(TestCase):
    def setUp(self) -> None:
        StaffUser.objects.create_superuser("ops", "ops@example.com", "ops-pass")
        self.client.login(username="ops", password="ops-pass")
        HomeSource.objects.all().delete()

    def test_the_channel_form_refuses_the_courier_channel(self) -> None:
        channel = HomeChannel.objects.get(country="CA")
        response = self.client.post(
            f"/admin/home/homechannel/{channel.pk}/change/",
            {"country": "CA", "username": "@Chamed0on", "enabled": "on"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("courier channel", response.content.decode())
        channel.refresh_from_db()
        self.assertEqual(channel.username, "Chamedoon_CA")

    def test_check_access_action_reports_each_channel(self) -> None:
        turn_on()
        channel = HomeChannel.objects.get(country="CA")
        with patch("home.publish.bot.check_channel", return_value={"chat_id": -100777, "title": "CA", "bot": "b"}):
            response = self.client.post(
                "/admin/home/homechannel/",
                {"action": "check_access", "_selected_action": [channel.pk]},
                follow=True,
            )
        self.assertIn("the bot is an admin and can post", response.content.decode())
        channel.refresh_from_db()
        self.assertEqual(channel.chat_id, -100777)

    def test_bot_token_is_kept_when_blank_and_must_look_like_a_token(self) -> None:
        row = turn_on()
        url = f"/admin/home/homesettings/{row.pk}/change/"
        data = {
            "enabled": "on",
            "ingest_enabled": "on",
            "review_enabled": "on",
            "media_enabled": "on",
            "lookback_days": "30",
            "review_batch": "15",
            "media_batch": "12",
            "publish_batch": "5",
            "publish_window_days": "3",
            "publish_daily_cap": "40",
            "publish_enabled": "on",
            "review_workers": "8",
            "review_model": "",
        }
        page = self.client.get(url)
        self.assertNotIn(TOKEN, page.content.decode())
        self.client.post(url, {**data, "bot_token": ""})
        row.refresh_from_db()
        self.assertEqual(row.bot_token, TOKEN)
        bad = self.client.post(url, {**data, "bot_token": "nonsense"})
        self.assertIn("does not look like a bot token", bad.content.decode())
        self.client.post(url, {**data, "bot_token": "555:NEW"})
        row.refresh_from_db()
        self.assertEqual(row.bot_token, "555:NEW")

    def test_run_page_previews_and_posts(self) -> None:
        turn_on()
        de_listing(1)
        page = self.client.post("/admin/home/run/", {"action": "preview"})
        self.assertIn("Next posts (nothing was sent)", page.content.decode())
        self.assertIn("520 €", page.content.decode())
        with patch("home.publish.bot.send_message", return_value=[1]):
            page = self.client.post("/admin/home/run/", {"action": "publish"})
        self.assertIn("1 posted", page.content.decode())
        self.assertEqual(HomeListing.objects.filter(published_at__isnull=False).count(), 1)

    def test_run_page_says_why_posting_is_off(self) -> None:
        page = self.client.get("/admin/home/run/")
        self.assertIn("Channel posting", page.content.decode())
        self.assertIn("switched off", page.content.decode())

    def test_listing_action_can_keep_a_listing_out_of_the_channel(self) -> None:
        listing = de_listing(1)
        self.client.post("/admin/home/homelisting/", {"action": "skip_posting", "_selected_action": [listing.pk]})
        listing.refresh_from_db()
        self.assertIsNotNone(listing.published_at)
        self.assertEqual(list(publishable()), [])
        self.assertEqual(self.client.get("/admin/home/homechannel/").status_code, 200)
