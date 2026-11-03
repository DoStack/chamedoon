"""The channel post format v2: exact output behind `HomeSettings.channel_v2`, byte-identical output without it."""

from __future__ import annotations

import io
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from home import channel_format, publish, status_posts
from home.models import HomeChannel, HomeListing, HomeSettings, HomePublication, ListingStatus, StatusPost
from tests.test_home_publish import ca_listing, de_listing

TOKEN = "123456:TESTTOKEN"
BIDI = str.maketrans("", "", "‎‏")


V1_POST = (
    "💶 <b>۹۵۰ CA$\u200e</b> در ماه · <b>اتاق</b>\n"
    "📍 نورث یورک، تورنتو\n"
    "✅ قبوض داخل · بدون بنگاه\n"
    "👤 فقط خانم\n"
    "⚠️ ماه اول و آخر · در انتاریو فقط ودیعه\u200cی ماه آخر مجاز است و ودیعه\u200cی خسارت قانونی نیست.\n"
    "📞 تماس: \u200e@ca_user2\n"
    "#تورنتو\u200f #نورث_یورک\u200f #اتاق\u200f #زیر_۱۰۰۰\u200f"
)


def settings(**values) -> HomeSettings:
    row, _ = HomeSettings.objects.update_or_create(pk=1, defaults={"bot_token": TOKEN, "bot_username": "chamedoon_home_bot", "publish_enabled": True, **values})
    return row


def toronto() -> HomeChannel:
    row, _ = HomeChannel.objects.update_or_create(
        country="CA", city="Toronto", defaults={"username": "chamedoon_home_toronto", "aliases": "North York, Richmond Hill", "enabled": True}
    )
    return row


def berlin() -> HomeChannel:
    row, _ = HomeChannel.objects.update_or_create(country="DE", city="Berlin", defaults={"username": "chamedoon_home_berlin", "aliases": "Neukölln", "enabled": True})
    return row


def plain(text: str) -> str:
    return text.translate(BIDI)


class FlagOffIsByteIdentical(TestCase):
    """With the flag off every path that builds a post returns what the v1 functions return, byte for byte."""

    def setUp(self) -> None:
        settings(channel_v2=False)
        self.channel = toronto()
        self.listing = ca_listing(contact_phone="", furnished="yes", available_from="2026-12-01")

    def test_the_flag_defaults_to_off(self) -> None:
        HomeSettings.objects.all().delete()
        self.assertFalse(HomeSettings.load().channel_v2)

    def test_post_text_is_the_v1_text_at_every_limit(self) -> None:
        for limit in (publish.MESSAGE_LIMIT, publish.CAPTION_LIMIT, 200):
            for photos in (0, 3):
                self.assertEqual(
                    publish.channel_post_text(self.listing, limit=limit, metro="Toronto", photos=photos),
                    publish.format_listing_text(self.listing, limit=limit, metro="Toronto", photos=photos),
                )

    def test_a_v1_post_is_pinned_to_its_exact_text(self) -> None:
        """A change to the v1 post fails here: this is today's output, marks included."""
        listing = ca_listing(2, contact_phone="", area="North York", property_type="room", price_amount=950, available_from=None)
        self.assertEqual(
            publish.channel_post_text(listing, metro="Toronto"),
            V1_POST,
        )

    def test_buttons_are_the_v1_buttons(self) -> None:
        buttons = publish.post_buttons(self.listing, self.channel, "Toronto")
        self.assertEqual([[button["text"] for button in row] for row in buttons], [["📷 جزئیات و تماس", "🏘 مشابه‌ها"], ["🔔 آگهی‌های این‌شکلی رو خبرم کن"]])

    def test_what_goes_to_telegram_is_unchanged(self) -> None:
        with patch("home.publish.bot.send_message", return_value=[9]) as send, patch("home.publish.photo_urls", return_value=[]):
            publish.publish_to_city(self.listing, self.channel)
        args, kwargs = send.call_args
        self.assertEqual(args[1], publish.format_listing_text(self.listing, metro="Toronto", photos=0))
        self.assertEqual(kwargs["buttons"][1][0]["text"], publish.ALERT_BUTTON)

    def test_a_rented_edit_is_unchanged(self) -> None:
        self.listing.status_post = StatusPost.RENTED
        self.listing.rented_at = timezone.now()
        text, caption, short, buttons, reply = status_posts._contents(self.listing, self.channel)
        self.assertEqual(text, status_posts.rented_text(self.listing))
        self.assertEqual(caption, text)
        self.assertEqual(short, "✅ اجاره رفت")
        self.assertIsNone(buttons)
        self.assertIsNone(reply)

    def test_a_restored_edit_is_unchanged(self) -> None:
        self.listing.status_post = StatusPost.RESTORED
        text, caption, short, buttons, _reply = status_posts._contents(self.listing, self.channel)
        self.assertEqual(text, publish.format_listing_text(self.listing, metro="Toronto"))
        self.assertEqual(caption, publish.format_listing_text(self.listing, limit=publish.CAPTION_LIMIT, metro="Toronto"))
        self.assertEqual(short, f"👆 {publish.DETAILS_BUTTON}")
        self.assertEqual(buttons[0][0]["text"], publish.DETAILS_BUTTON)


class PostFormat(TestCase):
    def setUp(self) -> None:
        settings(channel_v2=True, publish_phone=True)
        toronto()
        berlin()

    def test_an_apartment_reads_as_on_the_board(self) -> None:
        listing = ca_listing(
            property_type="apartment", rooms=1, price_amount=1850, area="North York", furnished="yes", bills_included="yes",
            available_from="2027-12-01", min_term_months=12, flags={"couples_ok": True}, roommate_gender="any", lister_type="unknown",
            deposit_amount=None, deposit_months=None,
        )
        self.assertEqual(
            plain(channel_format.post_text(listing, metro="Toronto")),
            "💶 <b>۱٬۸۵۰ CA$</b> در ماه\n"
            "🏠 <b>آپارتمان ۱ خوابه</b> · نورث یورک\n\n"
            "<blockquote>🛋 مبله · قبوض داخل\n"
            "📅 از ۱ دسامبر ۲۰۲۷ · حداقل ۱۲ ماه\n"
            "👤 مناسب زوج</blockquote>\n\n"
            "#تورنتو #نورث_یورک #یک_خوابه #زیر_۲۰۰۰",
        )

    def test_no_contact_and_no_source_link_at_any_setting(self) -> None:
        listing = ca_listing(contact_phone="647-555-0142")
        listing.contact_phone = "+16475550142"
        listing.post.source_url = "https://t.me/RentToronto/77"
        listing.post.save()
        listing.save()
        for gate in (True, False):
            settings(channel_v2=True, contact_gate_on=gate)
            text = publish.channel_post_text(listing, metro="Toronto")
            for leak in ("+1647", "ca_user1", "t.me", "پست اصلی", "تماس:"):
                self.assertNotIn(leak, text)

    def test_no_price_asks(self) -> None:
        listing = ca_listing(price_amount=None, area="Richmond Hill", property_type="room")
        lines = plain(channel_format.post_text(listing, metro="Toronto")).split("\n")
        self.assertEqual(lines[0], "💶 <b>قیمت: بپرسید</b>")
        self.assertEqual(lines[1], "🏠 <b>اتاق</b> · ریچموند هیل")
        self.assertNotIn("#زیر", lines[-1])

    def test_a_sublet_leads_the_quote_with_its_dates_and_gets_its_hashtag(self) -> None:
        listing = ca_listing(
            property_type="studio", price_amount=1600, area="Downtown", rental_mode="sublet",
            available_from="2027-12-01", available_to="2028-02-28",
        )
        text = plain(channel_format.post_text(listing, metro="Toronto"))
        self.assertIn("<blockquote>🔁 <b>سابلت</b> · ۱ دسامبر ۲۰۲۷ تا ۲۸ فوریه ۲۰۲۸\n", text)
        self.assertIn("#سابلت", text)
        self.assertIn("#زیر_۲۰۰۰", text)

    def test_germany_says_warm_uses_the_german_words_and_its_own_bands(self) -> None:
        listing = de_listing(
            price_amount=680, price_basis="warm", area="Neukölln", rental_mode="sublet", registration="yes",
            available_from="2027-11-01", available_to="2028-03-31",
        )
        text = plain(channel_format.post_text(listing, metro="Berlin"))
        self.assertTrue(text.startswith("💶 <b>۶۸۰ €</b> در ماه · گرم\n🏠 <b>اتاق WG</b>"), text)
        self.assertIn("🔁 <b>Zwischenmiete</b>", text)
        self.assertIn("✅ ملد (Anmeldung) ممکن", text)
        self.assertIn("#Zwischenmiete", text)
        self.assertIn("#زیر_۷۰۰", text)
        self.assertIn("#ملد_دارد", text)
        self.assertEqual(channel_format.band_tag(de_listing(2, price_amount=1300, price_basis="warm")), "#بالای_۱۲۰۰")

    def test_a_wanted_post_shows_the_budget_and_its_own_buttons(self) -> None:
        listing = ca_listing(kind="wanted", price_amount=None, price_max=1000, property_type="room", area="North York", available_from="2027-12-01", min_term_months=6)
        text = plain(channel_format.post_text(listing, metro="Toronto"))
        self.assertTrue(text.startswith("🔎 <b>دنبال خانه</b> · بودجه تا <b>۱٬۰۰۰ CA$</b>\n🏠 <b>اتاق</b> · نورث یورک\n"), text)
        self.assertIn("#دنبال_خانه", text)
        self.assertIn("#زیر_۱۰۰۰", text)
        buttons = publish.post_buttons(listing, toronto(), "Toronto")
        self.assertEqual([[button["text"] for button in row] for row in buttons], [["🏠 خانه دارم، معرفی کنم"], ["➕ ثبت آگهی"]])

    def test_an_offer_gets_the_board_buttons(self) -> None:
        buttons = publish.post_buttons(ca_listing(), toronto(), "Toronto")
        self.assertEqual([[button["text"] for button in row] for row in buttons], [["📞 جزئیات و تماس", "🏘 مشابه‌ها"], ["🔔 گوش به زنگ مشابه"]])
        self.assertIn("startapp=home_ca_toronto_l", buttons[0][0]["url"])
        self.assertIn("start=alert_home_ca_toronto", buttons[1][0]["url"])

    def test_a_long_post_loses_whole_lines_and_never_cuts_a_tag(self) -> None:
        listing = ca_listing(
            area="Yonge & Finch", furnished="yes", bills_included="yes", size_m2=40, available_from="2027-12-01", min_term_months=12,
            roommate_gender="female", flags={"no_smoking": True, "students_only": True, "first_last": True, "credit_check": True},
        )
        full = channel_format.post_text(listing, metro="Toronto")
        for limit in (len(full) - 1, 220, 160, 90):
            text = channel_format.post_text(listing, limit=limit, metro="Toronto")
            self.assertLessEqual(len(text), limit)
            self.assertEqual(text.count("<blockquote>"), text.count("</blockquote>"))
            self.assertEqual(text.count("<b>"), text.count("</b>"))
            self.assertIn("💶", text)

    def test_a_caption_goes_out_within_the_telegram_limit_and_an_album_gets_the_reply(self) -> None:
        listing = ca_listing()
        with (
            patch("home.publish.photo_urls", return_value=["https://x/1.jpg", "https://x/2.jpg"]),
            patch("home.publish.bot.send_album", return_value=[5, 6]) as album,
            patch("home.publish.bot.send_message", return_value=[7]) as reply,
        ):
            ids = publish.publish_to_city(listing, toronto())
        self.assertEqual(ids, [5, 6, 7])
        self.assertIn("<blockquote>", album.call_args.args[2])
        self.assertLessEqual(len(album.call_args.args[2]), publish.CAPTION_LIMIT)
        self.assertEqual(reply.call_args.args[1], f"👆 {publish.DETAILS_LABEL}")
        self.assertEqual(reply.call_args.kwargs["buttons"][0][0]["text"], channel_format.DETAILS_BUTTON)

    def test_photos_telegram_refused_are_named_in_the_text_post(self) -> None:
        self.assertIn("📷 ۴ عکس در «جزئیات و تماس»", channel_format.post_text(ca_listing(), metro="Toronto", photos=4))

    def test_the_preview_shows_the_new_format(self) -> None:
        listing = ca_listing()
        with patch("home.publish.publishable", return_value=HomeListing.objects.filter(pk=listing.pk)), patch("home.publish.photo_urls", return_value=[]), patch(
            "home.publish.waiting_for_photos", return_value=False
        ):
            rows = publish.publish_preview()
        self.assertIn("<blockquote>", rows[0]["text"])


class ClosedPost(TestCase):
    def setUp(self) -> None:
        settings(channel_v2=True)
        toronto()

    def test_a_rented_post_is_edited_to_the_closed_card_with_no_buttons(self) -> None:
        listing = ca_listing(property_type="room", area="Richmond Hill")
        listing.status = ListingStatus.RENTED
        listing.status_post = StatusPost.RENTED
        listing.rented_at = timezone.now().replace(year=2027, month=10, day=26)
        text, caption, short, buttons, reply = status_posts._contents(listing, toronto())
        self.assertEqual(plain(text), "🚫 <b>بسته شد</b>\n<s>🏠 اتاق · ریچموند هیل</s>\n\n۲۶ اکتبر ۲۰۲۷")
        self.assertEqual(caption, text)
        self.assertEqual(short, channel_format.CLOSED_LINE)
        self.assertIsNone(buttons)
        self.assertIsNone(reply)

    def test_a_reported_post_keeps_its_warning(self) -> None:
        listing = ca_listing()
        listing.status_post = StatusPost.REPORTED
        self.assertEqual(status_posts._contents(listing, toronto())[0], status_posts.reported_text(listing))

    def test_a_restored_post_comes_back_in_the_new_format(self) -> None:
        listing = ca_listing()
        listing.status_post = StatusPost.RESTORED
        text, _caption, short, buttons, _reply = status_posts._contents(listing, toronto())
        self.assertIn("<blockquote>", text)
        self.assertEqual(short, "👆 📞 جزئیات و تماس")
        self.assertEqual(buttons[0][0]["text"], "📞 جزئیات و تماس")


class NonListingPosts(TestCase):
    def setUp(self) -> None:
        settings(channel_v2=True)

    def test_the_pinned_post_lists_the_bands_of_the_country(self) -> None:
        canada, germany = plain(channel_format.pinned_text(toronto())), plain(channel_format.pinned_text(berlin()))
        self.assertIn("📌 <b>چمدون خانه · تورنتو</b>", canada)
        self.assertIn("#زیر_۸۰۰ #زیر_۱۰۰۰ #زیر_۱۵۰۰ #زیر_۲۰۰۰ #زیر_۳۰۰۰ #بالای_۳۰۰۰", canada)
        self.assertIn("گرم یا سرد همیشه نوشته می‌شود", germany)
        self.assertIn("#زیر_۵۰۰ #زیر_۷۰۰ #زیر_۹۰۰ #زیر_۱۲۰۰ #بالای_۱۲۰۰", germany)

    def test_the_welcome_names_the_person_and_escapes_the_name(self) -> None:
        self.assertIn("سلام &lt;b&gt; 👋", channel_format.welcome_text("<b>", toronto()))
        self.assertTrue(channel_format.welcome_text("", toronto()).startswith("سلام 👋 به گروه"))

    def test_the_weekly_report_medians_only_count_stated_monthly_rents(self) -> None:
        toronto()
        for number, price in enumerate((1000, 1200, 1400), start=1):
            ca_listing(number, property_type="room", area="North York", price_amount=price)
        ca_listing(10, property_type="room", area="North York", price_amount=None)
        ca_listing(11, property_type="room", area="North York", price_amount=90, price_period="night")
        HomeListing.objects.update(first_seen=timezone.now() - timedelta(days=1))
        report = channel_format.weekly_report(toronto())
        self.assertEqual(report["new"], 5)
        self.assertEqual(report["priced"], 3)
        self.assertEqual(report["rows"][0]["median"], Decimal("1200"))
        self.assertEqual(report["rows"][0]["count"], 3)
        text = plain(channel_format.weekly_text(toronto()))
        self.assertIn("🏠 اتاق: میانه‌ی قیمت ۱٬۲۰۰ CA$ (از ۳ آگهی)", text)
        self.assertNotIn("۱ خوابه", text)

    def test_the_weekly_report_with_nothing_to_say_still_reads(self) -> None:
        text = plain(channel_format.weekly_text(toronto()))
        self.assertIn("آگهی تازه: ۰ · آگهی با قیمت: ۰", text)
        self.assertIn("🚫 بسته شد: ۰", text)

    def test_the_command_is_a_dry_run_unless_told(self) -> None:
        toronto()
        out = io.StringIO()
        with patch("home.bot.send_message") as send:
            call_command("home_channel_post", "pinned", "--country", "CA", "--city", "Toronto", stdout=out)
        send.assert_not_called()
        self.assertIn("Dry run", out.getvalue())

    def test_the_command_sends_only_with_apply_and_the_flag_on(self) -> None:
        toronto()
        with patch("home.bot.send_message", return_value=[1]) as send:
            call_command("home_channel_post", "report", "--country", "CA", "--city", "Toronto", "--apply", stdout=io.StringIO())
        send.assert_called_once()
        settings(channel_v2=False)
        with patch("home.bot.send_message") as send, self.assertRaises(CommandError):
            call_command("home_channel_post", "report", "--country", "CA", "--city", "Toronto", "--apply", stdout=io.StringIO())
        send.assert_not_called()

    def test_the_command_refuses_a_channel_it_does_not_know(self) -> None:
        with self.assertRaises(CommandError):
            call_command("home_channel_post", "pinned", "--country", "CA", "--city", "Atlantis", stdout=io.StringIO())


class Publications(TestCase):
    def test_each_listing_is_recorded_once_as_before(self) -> None:
        settings(channel_v2=True)
        listing = ca_listing()
        with patch("home.publish.photo_urls", return_value=[]), patch("home.publish.bot.send_message", return_value=[4]):
            publish.publish_to_city(listing, toronto())
        row = HomePublication.objects.get(listing=listing)
        self.assertEqual(row.message_ids, [4])
