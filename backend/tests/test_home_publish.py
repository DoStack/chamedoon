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

from home import bot, conf
from home.guard import courier_channel_names, is_courier_channel
from home.job import run_home_job
from home.models import (
    HomeChannel,
    HomeListing,
    HomeMedia,
    HomePublication,
    HomeSettings,
    HomeSource,
    ListingStatus,
    MediaStatus,
)
from home.publish import (
    CAPTION_LIMIT,
    band_tag,
    format_listing_text,
    money_fa,
    persian_date,
    publish_listing,
    city_publishable,
    publish_pending,
    publish_preview,
    publishable,
    route,
    short_date,
    tag,
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


def visible(listing: HomeListing, **kwargs) -> str:
    """The post as a reader sees it: without the bidi marks that keep Latin bits in place."""
    return format_listing_text(listing, **kwargs).replace("\u200e", "").replace("\u200f", "")


class FormatTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()

    def test_an_offer_reads_price_first_like_a_marketplace_post(self) -> None:
        text = visible(de_listing(), phone=False)
        lines = text.split("\n")
        self.assertEqual(lines[0], "💶 <b>۵۲۰ €</b> در ماه، گرم · <b>اتاق WG ۱۶ متری مبله</b>")
        self.assertEqual(lines[1], "📍 نویکلن، برلین")
        self.assertEqual(lines[2], "✅ ملد (Anmeldung) دارد · بدون بنگاه")
        self.assertTrue(lines[3].startswith("📅 از ۱ نوامبر"))
        self.assertEqual(lines[4], "👤 فقط خانم")
        self.assertEqual(lines[5], "⚠️ ودیعه ۱ ماه")
        self.assertEqual(lines[6], "📞 تماس: @sara_b")
        self.assertEqual(lines[7], "#برلین #نویکلن #اتاق_WG #زیر_۷۰۰ #ملد_دارد")
        self.assertNotIn("<script", text)

    def test_every_line_starts_with_one_fixed_emoji_and_the_post_has_at_most_8_lines(self) -> None:
        listing = de_listing(flags={"parking": True, "no_smoking": True, "credit_check": True})
        text = visible(listing, phone=True, photos=4)
        body = text.split("\n")[:-1]
        self.assertLessEqual(len(body), 8)
        for line in body:
            self.assertTrue(line.startswith(("💶", "🔎", "📍", "✅", "📅", "👤", "⚠️", "📞", "📷")), line)

    def test_a_wanted_post_leads_with_the_search_and_the_budget(self) -> None:
        # No Berlin neighbourhood here: Neukölln is an alias of the Berlin city channel and would route the post there.
        listing = de_listing(
            kind="wanted", price_amount=None, price_max=1000, property_type="apartment", city="Frankfurt", area=""
        )
        text = visible(listing, phone=False)
        lines = text.split("\n")
        self.assertEqual(lines[0], "🔎 <b>دنبال خونه</b> · <b>آپارتمان</b> · بودجه تا <b>۱٬۰۰۰ €</b>")
        self.assertEqual(lines[1], "📍 فرانکفورت")
        self.assertEqual(lines[-1], "#فرانکفورت #دنبال_خانه #آپارتمان")
        self.assertNotIn("بنگاه", text)
        self.assertNotIn("✅", text)

    def test_a_wanted_post_without_type_or_budget(self) -> None:
        listing = de_listing(kind="wanted", price_amount=None, property_type="other", city="Frankfurt", area="")
        text = visible(listing, phone=False)
        self.assertTrue(text.startswith("🔎 <b>دنبال خونه</b>\n"))
        self.assertTrue(text.endswith("#فرانکفورت #دنبال_خانه"))

    def test_missing_price_says_to_ask_on_line_one(self) -> None:
        text = visible(de_listing(price_amount=None), phone=False)
        self.assertEqual(text.split("\n")[0], "💶 قیمت: بپرسید · <b>اتاق WG ۱۶ متری مبله</b>")
        self.assertNotIn("#زیر_", text)

    def test_germany_always_says_warm_or_cold(self) -> None:
        cases = [
            ({"price_basis": "warm"}, "در ماه، گرم"),
            ({"price_basis": "cold"}, "در ماه، سرد"),
            ({"price_basis": "unknown", "bills_included": "yes"}, "در ماه، گرم"),
            ({"price_basis": "unknown", "bills_included": "no"}, "در ماه، سرد"),
            ({"price_basis": "unknown", "bills_included": "unknown"}, "در ماه، گرم/سرد نامشخص"),
        ]
        for number, (values, expected) in enumerate(cases, start=1):
            listing = de_listing(number, **values)
            self.assertIn(f"</b> {expected} ·", visible(listing, phone=False), values)

    def test_anmeldung_shows_as_an_advantage_and_a_hashtag_only_when_given(self) -> None:
        yes = visible(de_listing(1, registration="yes"), phone=False)
        self.assertIn("✅ ملد (Anmeldung) دارد", yes)
        self.assertIn("#ملد_دارد", yes)
        no = visible(de_listing(2, registration="no"), phone=False)
        self.assertNotIn("#ملد_دارد", no)
        self.assertIn("⚠️ بدون ملد", no)

    def test_canadian_post_uses_cad_the_ontario_warning_and_the_district_hashtag(self) -> None:
        listing = ca_listing()  # first and last month and a damage deposit, in Ontario
        text = visible(listing, phone=False)
        lines = text.split("\n")
        self.assertEqual(lines[0], "💶 <b>۹۵۰ CA$</b> در ماه · <b>اتاق</b>")
        self.assertEqual(lines[1], "📍 Yonge &amp; Finch، نورث یورک، تورنتو")
        self.assertIn("✅ قبوض داخل · بدون بنگاه", text)
        self.assertIn("⚠️ ماه اول و آخر · در انتاریو فقط ودیعه‌ی ماه آخر مجاز است", text)
        self.assertEqual(lines[-1], "#تورنتو #نورث_یورک #اتاق #زیر_۱۰۰۰")

    def test_bills_extra_in_canada_sits_next_to_the_price(self) -> None:
        text = visible(ca_listing(bills_included="no"), phone=False)
        self.assertTrue(text.startswith("💶 <b>۹۵۰ CA$</b> در ماه، قبوض جدا · "))

    def test_the_phone_and_the_original_post_are_shown(self) -> None:
        listing = ca_listing(contact_phone="647-555-0142")
        listing.contact_phone = "+16475550142"
        listing.save()
        listing.post.source_url = "https://t.me/RentToronto/77"
        listing.post.save()
        shown = visible(listing, phone=True)
        self.assertIn(
            '📞 تماس: @ca_user1 · <code>+16475550142</code> · <a href="https://t.me/RentToronto/77">پست اصلی</a>', shown
        )
        hidden = visible(listing, phone=False)
        self.assertNotIn("+16475550142", hidden)
        self.assertIn("پست اصلی", hidden)

    def test_the_phone_is_shown_by_default(self) -> None:
        self.assertTrue(conf.publish_phone())
        listing = ca_listing(contact_phone="647-555-0142", contact_telegram="")
        listing.contact_phone = "+16475550142"
        listing.contact_telegram = ""
        listing.save()
        self.assertIn("📞 تماس: <code>+16475550142</code>", visible(listing))

    def test_no_contact_links_to_the_original_post(self) -> None:
        listing = ca_listing()
        listing.contact_telegram = ""
        listing.save()
        listing.post.source_url = "https://t.me/RentToronto/77"
        listing.post.save()
        self.assertIn('📞 تماس: در <a href="https://t.me/RentToronto/77">پست اصلی</a>', visible(listing, phone=True))

    def test_a_private_source_link_is_never_shown(self) -> None:
        listing = ca_listing()
        listing.contact_telegram = ""
        listing.save()
        listing.post.source_url = "https://t.me/c/123456/77"
        listing.post.save()
        text = visible(listing, phone=False)
        self.assertNotIn("t.me/c/", text)
        self.assertIn("ربات خانه", text)

    def test_the_ai_summary_stays_out_of_the_channel_post(self) -> None:
        listing = de_listing()
        listing.summary_fa = "اتاق <b>عالی</b> & ارزان"
        listing.save()
        text = visible(listing, phone=False)
        self.assertNotIn("عالی", text)
        self.assertNotIn("<i>", text)

    def test_a_long_post_drops_low_priority_lines_first_and_fits_the_caption(self) -> None:
        listing = de_listing()
        listing.flags = {**listing.flags, "deposit_warning": True, "parking": True}
        # Longer than the database allows, on purpose: only the formatter sees it, nothing is saved.
        listing.area = "محله " * 150
        listing.region = "ON"
        text = visible(listing, limit=CAPTION_LIMIT, phone=False)
        self.assertLessEqual(len(text), CAPTION_LIMIT)
        for kept in ("💶 <b>۵۲۰ €</b>", "اتاق WG", "📍 محله", "📞 تماس"):
            self.assertIn(kept, text)
        self.assertNotIn("⚠️", text)
        self.assertNotIn("#برلین", text)

    def test_an_oversized_place_is_cut_at_the_limit_once_nothing_else_can_go(self) -> None:
        listing = de_listing()
        listing.area = "محله " * 300
        text = format_listing_text(listing, limit=CAPTION_LIMIT, phone=False)
        self.assertEqual(len(text), CAPTION_LIMIT)
        self.assertNotIn("#", text)
        self.assertTrue(text.startswith("💶 <b>۵۲۰ €</b>"))

    def test_dates_use_persian_digits_and_month_names(self) -> None:
        from datetime import date

        self.assertEqual(persian_date(date(2027, 1, 5)), "۵ ژانویه ۲۰۲۷")
        self.assertEqual(short_date(date(2027, 1, 5), today=date(2026, 10, 25)), "۵ ژانویه ۲۰۲۷")
        self.assertEqual(short_date(date(2026, 12, 1), today=date(2026, 10, 25)), "۱ دسامبر")

    def test_round_numbers_are_never_in_scientific_notation(self) -> None:
        listing = de_listing(price_amount=1450, size_m2=60, rooms=20, deposit_months=3, property_type="apartment")
        text = visible(listing, phone=False)
        self.assertIn("<b>آپارتمان ۲۰ اتاقه ۶۰ متری مبله</b>", text)
        self.assertIn("⚠️ ودیعه ۳ ماه", text)
        self.assertNotIn("E+", text)

    def test_money_has_persian_digits_separators_and_the_unit_after(self) -> None:
        self.assertEqual(money_fa(Decimal("1450"), "EUR"), "۱٬۴۵۰ €")
        self.assertEqual(money_fa(Decimal("12500.5"), "CAD"), "۱۲٬۵۰۰٫۵۰ CA$\u200e")
        self.assertEqual(money_fa(None, "CAD"), "")
        self.assertIn("<b>۱٬۴۵۰ €</b>", visible(de_listing(price_amount=1450), phone=False))


class BidiTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()

    def test_latin_bits_are_held_in_place_in_a_right_to_left_post(self) -> None:
        text = format_listing_text(ca_listing(), phone=False)
        self.assertIn("<b>۹۵۰ CA$\u200e</b>", text)  # not «$CA ۹۵۰»
        self.assertIn("📞 تماس: \u200e@ca_user1", text)  # not «ca_user1@»
        self.assertTrue(text.endswith("#تورنتو\u200f #نورث_یورک\u200f #اتاق\u200f #زیر_۱۰۰۰\u200f"))
        german = format_listing_text(de_listing(), phone=False)
        self.assertIn("<b>اتاق WG\u200f ۱۶ متری مبله</b>", german)  # not «اتاق ۱۶ WG متری»
        self.assertIn("<b>۵۲۰ €</b>", german)
        self.assertIn("#اتاق_WG\u200f", german)


class HashtagTests(TestCase):
    def test_hashtags_keep_letters_digits_and_underscores_only(self) -> None:
        self.assertEqual(tag("اقامت کوتاه‌مدت"), "#اقامت_کوتاه_مدت")
        self.assertEqual(tag("Yonge & Finch"), "#Yonge_Finch")
        self.assertEqual(tag(" نورث  یورک! "), "#نورث_یورک")
        self.assertEqual(tag("زیر_۱۰۰۰"), "#زیر_۱۰۰۰")
        self.assertEqual(tag("&&"), "")

    def test_the_fixed_price_bands_per_currency(self) -> None:
        cases = [
            ("CAD", 750, "#زیر_۸۰۰"),
            ("CAD", 800, "#زیر_۸۰۰"),
            ("CAD", 950, "#زیر_۱۰۰۰"),
            ("CAD", 1200, "#زیر_۱۵۰۰"),
            ("CAD", 2900, "#زیر_۳۰۰۰"),
            ("CAD", 3200, "#بالای_۳۰۰۰"),
            ("EUR", 450, "#زیر_۵۰۰"),
            ("EUR", 520, "#زیر_۷۰۰"),
            ("EUR", 1900, "#زیر_۲۰۰۰"),
            ("EUR", 2400, "#بالای_۲۰۰۰"),
        ]
        for currency, amount, expected in cases:
            listing = HomeListing(kind="offer", price_amount=Decimal(amount), price_currency=currency, price_period="month")
            self.assertEqual(band_tag(listing), expected, (currency, amount))

    def test_no_band_without_a_monthly_price_in_a_known_currency(self) -> None:
        for values in (
            {"price_amount": None, "price_currency": "CAD", "price_period": "month"},
            {"price_amount": Decimal(80), "price_currency": "CAD", "price_period": "night"},
            {"price_amount": Decimal(900), "price_currency": "USD", "price_period": "month"},
        ):
            self.assertEqual(band_tag(HomeListing(kind="offer", **values)), "", values)

    def test_the_order_is_city_area_type_mode_band(self) -> None:
        HomeSource.objects.all().delete()
        text = visible(ca_listing(property_type="apartment", rental_mode="lease_transfer", price_amount=1800), phone=False)
        self.assertTrue(text.endswith("#تورنتو #نورث_یورک #آپارتمان #انتقال_لیز #زیر_۲۰۰۰"))
        lease = visible(ca_listing(2, property_type="apartment", rental_mode="long_term", price_amount=1800), phone=False)
        self.assertTrue(lease.endswith("#تورنتو #نورث_یورک #آپارتمان #زیر_۲۰۰۰"))


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
        self.assertIn("۵۲۰ €", rows[0]["text"])


@override_settings(TELEGRAM_CHANNEL_USERNAME="chamed0on", TELEGRAM_CHANNEL_ID="-1001234567890")
class PublishTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        # These tests cover the country channels; CityRoutingTests covers the city pass.
        HomeChannel.objects.exclude(city="").update(enabled=False)
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
        self.assertEqual(photo.call_args.kwargs["buttons"][0][0]["text"], "📷 جزئیات و تماس")

    def test_several_photos_go_as_one_album_with_the_buttons_as_a_reply(self) -> None:
        listing = de_listing(1)
        self.add_photos(listing, 4)
        with self.storage, patch("home.publish.bot.send_album", return_value=[10, 11, 12, 13]) as album, patch(
            "home.publish.bot.send_message", return_value=[14]
        ) as send:
            publish_pending()
        self.assertEqual(len(album.call_args.args[1]), 4)
        self.assertNotIn("📷", album.call_args.args[2])
        self.assertEqual(send.call_args.kwargs["reply_to"], 10)
        self.assertEqual(send.call_args.args[1], "👆 جزئیات و تماس")
        rows = send.call_args.kwargs["buttons"]
        self.assertEqual([[button["text"] for button in row] for row in rows], [["📷 جزئیات و تماس", "🏘 مشابه‌ها"], ["🔔 آگهی‌های این‌شکلی رو خبرم کن"]])
        listing.refresh_from_db()
        self.assertEqual((listing.channel_message_id, listing.channel_message_ids), (10, [10, 11, 12, 13, 14]))

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
        self.assertIn("📷 ۲ عکس در «جزئیات و تماس»", send.call_args.args[1])
        self.assertEqual(len(send.call_args.kwargs["buttons"]), 2)
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
        self.assertIn("not a member", HomeChannel.objects.get(country="DE", city="").last_error)

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
        channel = HomeChannel.objects.get(country="DE", city="")
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
        self.channel = HomeChannel.objects.get(country="DE", city="")

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
        channel = HomeChannel.objects.get(country="CA", city="")
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
        channel = HomeChannel.objects.get(country="CA", city="")
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
        self.assertIn("۵۲۰ €", page.content.decode())
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


@override_settings(TELEGRAM_CHANNEL_USERNAME="chamed0on")
class CityRoutingTests(TestCase):
    """Every listing goes to its city channel as well as to the country channel."""

    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        turn_on()

    def channel(self, city: str) -> HomeChannel:
        return HomeChannel.objects.get(city=city)

    def test_the_six_city_channels_are_seeded_with_bot_api_ids(self) -> None:
        rows = dict(HomeChannel.objects.exclude(city="").values_list("username", "chat_id"))
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows["chamedoon_toronto_khane"], -1003975467696)
        self.assertEqual(rows["chamedoon_koln_khane"], -1003213231959)

    def test_suburbs_go_to_their_metro(self) -> None:
        channels = list(HomeChannel.objects.exclude(city=""))
        cases = {
            ("CA", "North York"): "Toronto",
            ("CA", "Richmond Hill"): "Toronto",
            ("CA", "Newmarket"): "Toronto",
            ("CA", "Aurora"): "Toronto",
            ("CA", "Holland Landing"): "Toronto",
            ("CA", "Longueuil"): "Montreal",
            ("CA", "Laval"): "Montreal",
            ("CA", "Coquitlam"): "Vancouver",
            ("CA", "North Vancouver"): "Vancouver",
            ("CA", "West Vancouver"): "Vancouver",
            ("CA", "Burnaby"): "Vancouver",
            ("DE", "Köln"): "Cologne",
            ("DE", "Hamburg"): "Hamburg",
        }
        for (country, city), metro in cases.items():
            listing = HomeListing(country=country, city=city)
            self.assertEqual(route(listing, channels).city, metro, city)
        for country, city in (("DE", "Frankfurt"), ("DE", "Bochum"), ("CA", "Ottawa")):
            self.assertIsNone(route(HomeListing(country=country, city=city), channels), city)

    def test_the_area_routes_when_the_city_is_blank(self) -> None:
        channels = list(HomeChannel.objects.exclude(city=""))
        self.assertEqual(route(HomeListing(country="CA", city="", area="Laval"), channels).city, "Montreal")

    def test_a_listing_goes_to_its_city_and_its_country(self) -> None:
        listing = ca_listing(1)  # North York
        sent = []
        with patch("home.publish.bot.send_message", side_effect=lambda chat, text, **kwargs: sent.append(chat) or [len(sent)]):
            result = publish_pending()
        self.assertEqual(sorted(sent, key=str), [-1003975467696, "@Chamedoon_CA"])
        self.assertEqual((result["published"], result["city"]["published"]), (1, 1))
        publication = HomePublication.objects.get(listing=listing)
        self.assertEqual(publication.channel, self.channel("Toronto"))
        self.assertIsNotNone(publication.published_at)
        with patch("home.publish.bot.send_message") as send:
            publish_pending()
        send.assert_not_called()

    def test_a_city_without_a_channel_only_goes_to_the_country(self) -> None:
        de_listing(1, city="Frankfurt", area="")
        sent = []
        with patch("home.publish.bot.send_message", side_effect=lambda chat, text, **kwargs: sent.append(chat) or [1]):
            publish_pending()
        self.assertEqual(sent, ["@Chamedoon_Germany"])

    def test_listings_copied_by_hand_before_the_cutover_are_not_posted_again(self) -> None:
        copied = de_listing(1)
        fresh = de_listing(2)
        cutover = self.channel("Berlin").active_from
        HomeListing.objects.filter(pk=copied.pk).update(published_at=cutover - timedelta(hours=1))
        HomeListing.objects.filter(pk=fresh.pk).update(published_at=cutover + timedelta(hours=1))
        self.assertEqual([listing.pk for listing, _channel in city_publishable()], [fresh.pk])

    def test_a_listing_older_than_the_window_is_not_posted(self) -> None:
        old = de_listing(1)
        HomeListing.objects.filter(pk=old.pk).update(last_seen=timezone.now() - timedelta(days=11))
        self.assertEqual(city_publishable(), [])

    def test_a_failed_city_post_is_retried_then_given_up(self) -> None:
        listing = de_listing(1)
        HomeListing.objects.filter(pk=listing.pk).update(published_at=timezone.now())
        error = bot.BotError("Forbidden: bot is not a member of the channel chat", code=403)
        for _ in range(3):
            with patch("home.publish.bot.send_message", side_effect=error):
                publish_pending()
        self.assertEqual(HomePublication.objects.get(listing=listing).attempts, 3)
        self.assertEqual(city_publishable(), [])

    def test_a_disabled_city_channel_gets_nothing(self) -> None:
        de_listing(1)
        HomeChannel.objects.filter(city="Berlin").update(enabled=False)
        self.assertEqual(city_publishable(), [])


@override_settings(TELEGRAM_CHANNEL_USERNAME="chamed0on")
class CityPostsNotStarvedTests(TestCase):
    """A run that spends its time on the country channel still posts each listing to its city."""

    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        turn_on()

    def test_the_city_post_follows_the_country_post_in_the_same_loop(self) -> None:
        listing = ca_listing(1)  # North York
        sent = []
        with patch("home.publish.bot.send_message", side_effect=lambda chat, text, **kwargs: sent.append(chat) or [len(sent)]), patch(
            "home.publish._publish_cities", side_effect=lambda limit, stop_at, counts, today: (counts, 0)
        ):
            result = publish_pending()
        self.assertEqual(sorted(sent, key=str), [-1003975467696, "@Chamedoon_CA"])
        self.assertEqual((result["published"], result["city"]["published"]), (1, 1))
        self.assertTrue(HomePublication.objects.filter(listing=listing, published_at__isnull=False).exists())

    def test_a_city_post_is_not_repeated_by_the_city_pass(self) -> None:
        ca_listing(1)
        with patch("home.publish.bot.send_message", return_value=[1]) as send:
            publish_pending()
            publish_pending()
        self.assertEqual(send.call_count, 2)
