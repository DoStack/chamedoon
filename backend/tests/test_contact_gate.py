from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from home import alerts, bot, botactions, botchat, freshness, gate, publish, status_posts
from home.models import (
    ChannelMembership,
    ContactReveal,
    HomeAlert,
    HomeChannel,
    HomeListing,
    HomePost,
    HomePublication,
    HomeSettings,
    HomeSource,
    ListingStatus,
    MembershipStatus,
    StatusPost,
)
from tests.helpers import TEST_SECRET, bearer_auth, make_user
from tests.test_home_api import ApiCase

CONTACT_PATTERN = "/api/v1/me/listings/{id}/contact/"
AVAILABILITY_PATTERN = "/api/v1/me/listings/{id}/availability/"
HANDLE = "roomsbyali1"
PHONE = "+14165550142"


def set_gate(on: bool = True) -> None:
    HomeSettings.objects.update_or_create(pk=1, defaults={"contact_gate_on": on, "bot_token": "777:HOMEBOT"})


def tg_answer(status: str, **extra):
    return patch("home.gate.bot.call", return_value={"status": status, **extra})


class GateCase(ApiCase):
    def setUp(self) -> None:
        super().setUp()
        set_gate(True)
        self.home = self.listing(contact_telegram=HANDLE, contact_phone="416-555-0142")

    def contact(self, pk: int | None = None, user=None):
        return self.client.post(f"/api/v1/me/listings/{pk or self.home.pk}/contact/", **bearer_auth(user or self.user))


class ContactEndpointTests(GateCase):
    def test_member_sees_everything_and_it_counts_once(self) -> None:
        with tg_answer("member") as telegram:
            response = self.contact()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["telegram"], body["telegram_url"]), (HANDLE, f"https://t.me/{HANDLE}"))
        self.assertTrue(body["phone"])
        self.assertTrue(body["source_url"])
        self.assertEqual((body["gate"], body["membership_checked"], body["has_any"], body["reveals_left"]), ("member", True, True, 29))
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(telegram.call_args.args[0], "getChatMember")
        self.assertEqual(telegram.call_args.args[1]["user_id"], self.user.telegram_user_id)

    def test_double_tap_or_retry_counts_once(self) -> None:
        with tg_answer("member"):
            lefts = [self.contact().json()["reveals_left"] for _ in range(3)]
        self.assertEqual(lefts, [29, 29, 29])
        self.assertEqual(ContactReveal.objects.count(), 1)

    def test_every_member_status_passes(self) -> None:
        for status, extra in (("creator", {}), ("administrator", {}), ("restricted", {"is_member": True})):
            ChannelMembership.objects.all().delete()
            with self.subTest(status=status), tg_answer(status, **extra):
                self.assertEqual(self.contact().status_code, 200)

    def test_not_member_gives_the_channel_and_no_contact(self) -> None:
        for status, extra in (("left", {}), ("restricted", {"is_member": False})):
            with self.subTest(status=status), tg_answer(status, **extra):
                response = self.contact()
                body = self.assert_error(response, "not_member", CONTACT_PATTERN)
                self.assertEqual(body["join_url"], "https://t.me/chamedoon_toronto")
                self.assertEqual(body["channel"]["city"], "Toronto")
                for leaked in (HANDLE, "555", "RentToronto"):
                    self.assertNotIn(leaked, response.content.decode())
        self.assertFalse(ContactReveal.objects.exists())

    def test_telegram_saying_it_never_met_this_person_is_not_a_failure(self) -> None:
        with patch("home.gate.bot.call", side_effect=bot.BotError("Bad Request: user not found", code=400)):
            self.assert_error(self.contact(), "not_member", CONTACT_PATTERN)

    def test_joined_and_came_back(self) -> None:
        with tg_answer("left"):
            self.assert_error(self.contact(), "not_member", CONTACT_PATTERN)
        with tg_answer("member"):
            self.assertEqual(self.contact().status_code, 200)

    def test_member_answer_holds_24_hours_then_asks_again(self) -> None:
        with tg_answer("member") as first:
            self.contact()
            self.contact()
        first.assert_called_once()
        ChannelMembership.objects.update(checked_at=timezone.now() - timedelta(hours=25))
        with tg_answer("left"):
            self.assert_error(self.contact(), "not_member", CONTACT_PATTERN)

    def test_a_not_member_answer_is_never_reused(self) -> None:
        with tg_answer("left") as telegram:
            self.contact()
            self.contact()
        self.assertEqual(telegram.call_count, 2)

    def test_banned(self) -> None:
        with tg_answer("kicked"):
            response = self.contact()
        self.assert_error(response, "gate_banned", CONTACT_PATTERN)
        self.assertNotIn(HANDLE, response.content.decode())
        self.assertEqual(ChannelMembership.objects.get().status, MembershipStatus.BANNED)

    def test_telegram_error_or_bot_not_admin_fails_open_and_logs(self) -> None:
        for error in (bot.BotError("Telegram unreachable: timed out"), bot.BotError("Bad Request: member list is inaccessible", code=400), bot.BotError("Too Many Requests", code=429, retry_after=9)):
            ContactReveal.objects.all().delete()
            with self.subTest(error=str(error)), patch("home.gate.bot.call", side_effect=error), self.assertLogs("home.gate", level="WARNING") as logs:
                body = self.contact().json()
                self.assertEqual((body["gate"], body["membership_checked"], body["telegram"]), ("failed_open", False, HANDLE))
                self.assertIn("gate_failed_open", logs.output[0])
                self.assertTrue(ContactReveal.objects.get().failed_open)
                self.assertFalse(ChannelMembership.objects.exists())

    def test_city_without_a_channel_uses_the_country_channel(self) -> None:
        ottawa = self.listing(2, city="Ottawa", area="", contact_telegram="other_person")
        with tg_answer("left") as telegram:
            body = self.assert_error(self.contact(ottawa.pk), "not_member", CONTACT_PATTERN)
        self.assertEqual(body["join_url"], "https://t.me/chamedoon_ca")
        self.assertEqual(telegram.call_args.args[1]["chat_id"], self.country_channel.target())

    def test_a_city_listing_does_not_accept_the_country_channel(self) -> None:
        ChannelMembership.objects.create(user=self.user, channel=self.country_channel, status="member", checked_at=timezone.now())
        with tg_answer("left"):
            self.assert_error(self.contact(), "not_member", CONTACT_PATTERN)

    def test_no_channel_at_all_opens_and_logs(self) -> None:
        HomeChannel.objects.all().delete()
        with patch("home.gate.bot.call") as telegram, self.assertLogs("home.gate", level="WARNING") as logs:
            body = self.contact().json()
        telegram.assert_not_called()
        self.assertEqual((body["gate"], body["membership_checked"]), ("no_channel", False))
        self.assertIn("gate_no_channel", logs.output[0])

    def test_gate_off_opens_with_no_telegram_call(self) -> None:
        set_gate(False)
        with patch("home.gate.bot.call") as telegram:
            self.assertEqual(self.contact().json()["gate"], "gate_off")
        telegram.assert_not_called()

    def test_own_listing_opens_uncounted(self) -> None:
        HomePost.objects.filter(pk=self.home.post_id).update(author_id=self.user.telegram_user_id)
        with patch("home.gate.bot.call") as telegram:
            self.assertEqual(self.contact().json()["gate"], "owner")
        telegram.assert_not_called()
        self.assertFalse(ContactReveal.objects.exists())

    def test_no_handle_no_phone_with_and_without_source(self) -> None:
        HomeListing.objects.filter(pk=self.home.pk).update(contact_telegram="", contact_phone="")
        with tg_answer("member"):
            body = self.contact().json()
            self.assertEqual((body["telegram"], body["phone"], body["has_any"]), ("", "", True))
            HomePost.objects.filter(pk=self.home.post_id).update(source_url="")
            self.assertFalse(self.contact().json()["has_any"])

    def test_private_group_source_link_never_opens(self) -> None:
        HomePost.objects.filter(pk=self.home.post_id).update(source_url="https://t.me/c/123/4")
        with tg_answer("member"):
            self.assertEqual(self.contact().json()["source_url"], "")

    def test_listing_closed_and_gone_since_the_page_was_cached(self) -> None:
        for status in (ListingStatus.RENTED, ListingStatus.EXPIRED):
            HomeListing.objects.filter(pk=self.home.pk).update(status=status)
            with self.subTest(status=status), patch("home.gate.bot.call") as telegram:
                response = self.contact()
                self.assert_error(response, "listing_closed", CONTACT_PATTERN)
                self.assertNotIn(HANDLE, response.content.decode())
                telegram.assert_not_called()
        HomeListing.objects.filter(pk=self.home.pk).update(status=ListingStatus.REPORTED)
        self.assert_error(self.contact(), "listing_gone", CONTACT_PATTERN)
        self.assert_error(self.contact(999999), "listing_gone", CONTACT_PATTERN)

    def test_merged_listing_opens_the_one_it_became(self) -> None:
        other = self.listing(2, contact_telegram="other_person")
        HomeListing.objects.filter(pk=self.home.pk).update(status=ListingStatus.ACTIVE, canonical=None)
        HomeListing.objects.filter(pk=other.pk).update(status=ListingStatus.DUPLICATE, canonical=self.home)
        with tg_answer("member"):
            self.assertEqual(self.contact(other.pk).json()["telegram"], HANDLE)

    def test_rate_limited(self) -> None:
        with patch("home_api.views.ContactThrottle.rate", "1/min"), tg_answer("member"):
            self.contact()
            self.assert_error(self.contact(), "rate_limited", CONTACT_PATTERN)

    def test_needs_a_token_and_a_live_account(self) -> None:
        self.assert_error(self.client.post(f"/api/v1/me/listings/{self.home.pk}/contact/"), "auth_required", CONTACT_PATTERN)
        header = bearer_auth(self.user)
        self.user.is_active = False
        self.user.save()
        self.assert_error(self.client.post(f"/api/v1/me/listings/{self.home.pk}/contact/", **header), "user_blocked", CONTACT_PATTERN)


class RevealCapTests(GateCase):
    def fill(self, count: int, *, age: timedelta = timedelta(0)) -> None:
        now = timezone.now() - age
        rows = []
        for number in range(count):
            listing = self.listing(1000 + number, contact_telegram=f"owner_{number}")
            rows.append(ContactReveal(user=self.user, listing=listing, day=now.date()))
        ContactReveal.objects.bulk_create(rows)
        ContactReveal.objects.update(created_at=now)

    def test_cap_stops_a_new_listing_only(self) -> None:
        self.fill(30)
        with tg_answer("member"):
            response = self.contact()
        self.assert_error(response, "reveal_cap", CONTACT_PATTERN)
        self.assertNotIn(HANDLE, response.content.decode())
        already = ContactReveal.objects.first().listing_id
        with tg_answer("member"):
            body = self.contact(already).json()
        self.assertEqual(body["reveals_left"], 0)

    def test_29_leaves_room_for_one(self) -> None:
        self.fill(29)
        with tg_answer("member"):
            self.assertEqual(self.contact().json()["reveals_left"], 0)

    def test_the_window_rolls(self) -> None:
        self.fill(30, age=timedelta(hours=25))
        with tg_answer("member"):
            self.assertEqual(self.contact().json()["reveals_left"], 29)

    def test_cap_is_per_person(self) -> None:
        self.fill(30)
        other = make_user(telegram_user_id=880077, first_name="Ali")
        with tg_answer("member"):
            self.assertEqual(self.contact(user=other).status_code, 200)


class AvailabilityTests(GateCase):
    def answer(self, answer: str, pk: int | None = None, user=None):
        return self.client.post(
            f"/api/v1/me/listings/{pk or self.home.pk}/availability/",
            {"answer": answer},
            content_type="application/json",
            **bearer_auth(user or self.user),
        )

    def test_outcomes_are_live(self) -> None:
        self.assertEqual(self.answer("available").json()["outcome"], "confirmed")
        self.assertEqual(self.answer("taken").json()["outcome"], "noted")
        self.assertEqual(self.answer("taken").json()["outcome"], "noted")
        second = make_user(telegram_user_id=880078, first_name="Ali")
        self.assertEqual(self.answer("taken", user=second).json()["outcome"], "rented")
        self.home.refresh_from_db()
        self.assertEqual(self.home.status, ListingStatus.RENTED)
        self.assertEqual(self.answer("taken").json()["outcome"], "closed")
        self.assertEqual(self.answer("available").json()["outcome"], "closed")

    def test_app_listing_only_its_owner_can_answer(self) -> None:
        app = HomeSource.objects.create(username="app_home", country="CA", kind="group")
        HomePost.objects.filter(pk=self.home.post_id).update(source=app, author_id=880099)
        self.assert_error(self.answer("taken"), "confirm_not_allowed", AVAILABILITY_PATTERN)
        owner = make_user(telegram_user_id=880099, first_name="Owner")
        self.assertEqual(self.answer("available", user=owner).json()["outcome"], "confirmed")

    def test_bad_answer_and_gone_listing(self) -> None:
        got = self.assert_error(self.answer("maybe"), "validation_failed", AVAILABILITY_PATTERN)
        self.assertEqual(got["fields"], {"answer": ["invalid_choice"]})
        self.assert_error(self.answer("taken", 999999), "listing_gone", AVAILABILITY_PATTERN)

    def test_detail_says_when_to_ask(self) -> None:
        body = self.client.get(f"/api/v1/public/listings/{self.home.pk}/").json()
        self.assertTrue(body["can_confirm"])
        HomeListing.objects.filter(pk=self.home.pk).update(status=ListingStatus.RENTED)
        body = self.client.get(f"/api/v1/public/listings/{self.home.pk}/").json()
        self.assertFalse(body["can_confirm"])
        self.assertEqual(body["closed_reason"], "owner")

    def test_poster_count(self) -> None:
        url = f"/api/v1/public/listings/{self.home.pk}/"
        self.assertIsNone(self.client.get(url).json()["poster_count"])
        HomePost.objects.filter(pk=self.home.post_id).update(author_id=555)
        self.assertEqual(self.client.get(url).json()["poster_count"], 1)


class LeakTests(GateCase):
    """With the gate on, nothing public or sent by a bot holds a handle, a phone or the source link."""

    def assert_clean(self, text: object, where: str) -> None:
        text = json.dumps(text, ensure_ascii=False) if not isinstance(text, str) else text
        for leaked in (HANDLE, "4165550142", "416-555", "t.me/RentToronto", "RentToronto", f"t.me/{HANDLE}"):
            self.assertNotIn(leaked, text, where)

    def alert(self) -> HomeAlert:
        return HomeAlert.objects.create(user=self.user, telegram_user_id=self.user.telegram_user_id, chat_id=self.user.telegram_user_id, country="CA", city="Toronto")

    def test_channel_post_text_and_caption(self) -> None:
        for kwargs in ({}, {"limit": publish.CAPTION_LIMIT}, {"phone": True}):
            text = publish.format_listing_text(self.home, **kwargs)
            self.assert_clean(text, f"post {kwargs}")
            self.assertNotIn("📞", text)

    def test_contact_in_the_place_stays_out_of_the_post(self) -> None:
        HomeListing.objects.filter(pk=self.home.pk).update(area=f"Yonge {HANDLE}")
        self.home.refresh_from_db()
        self.assert_clean(publish.format_listing_text(self.home), "post area")

    def test_post_buttons(self) -> None:
        self.assert_clean(publish.post_buttons(self.home, self.channel), "post buttons")

    def test_alert_message_and_keyboard(self) -> None:
        alert = self.alert()
        self.assert_clean(alerts.alert_text(alert, self.home), "alert card")
        keyboard = alerts.alert_keyboard(alert, self.home)
        self.assert_clean(keyboard, "alert keyboard")
        button = keyboard["inline_keyboard"][0][0]
        self.assertEqual(button["text"], alerts.GATED_CONTACT_BUTTON)
        self.assertIn(str(self.home.pk), button["url"])
        self.assertNotIn("🚩", json.dumps(keyboard, ensure_ascii=False))

    def test_bot_search_results(self) -> None:
        self.assert_clean(botchat.result_lines(self.home), "bot results")

    def test_bot_detail_and_contact_tap(self) -> None:
        self.assert_clean(botactions.detail_text(self.home), "bot detail")
        keyboard = botactions.detail_keyboard(self.home)
        self.assert_clean(keyboard, "bot detail keyboard")
        self.assertNotIn("🚩", json.dumps(keyboard, ensure_ascii=False))
        text, buttons = botactions.contact_text(self.home, self.user.telegram_user_id)
        self.assert_clean(text, "bot contact")
        self.assert_clean(buttons, "bot contact keyboard")
        self.assertEqual(buttons[0][0]["text"], alerts.GATED_CONTACT_BUTTON)

    def test_alert_contact_tap_sends_no_contact(self) -> None:
        with patch("home.botchat.send") as sent:
            botchat.handle_callback(
                {"id": "1", "data": f"al:ct:{self.home.pk}", "from": {"id": self.user.telegram_user_id}, "message": {"message_id": 1, "chat": {"id": 5, "type": "private"}}}
            )
        sent.assert_called_once()
        self.assert_clean(sent.call_args.args[1:], "alert contact tap")

    def test_scam_warning_no_longer_asks_for_a_report(self) -> None:
        self.assertNotIn("گزارش", freshness.contact_warning(1)["text"])
        self.assertNotIn("Report", freshness.contact_warning(2, locale="en")["text"])

    def test_public_json(self) -> None:
        for url in (
            f"/api/v1/public/listings/{self.home.pk}/",
            f"/api/v1/public/deeplink/?startapp=home_{self.home.pk}",
            "/api/v1/public/cities/",
            "/api/v1/public/meta/",
        ):
            self.assert_clean(self.client.get(url).content.decode(), url)

    def test_summary_vectors(self) -> None:
        for vector in (HANDLE, "+49 176 12345678", "0176 1234567", "wa.me/14165550142", "tg://resolve?domain=x", f"t.me/{HANDLE}"):
            with self.subTest(vector=vector):
                HomeListing.objects.filter(pk=self.home.pk).update(summary_fa=f"اتاق خوب {vector} لطفا")
                text = self.client.get(f"/api/v1/public/listings/{self.home.pk}/").json()["summary_fa"]
                self.assertEqual(text, "اتاق خوب لطفا")

    def test_with_the_gate_off_every_surface_stays_as_it_was(self) -> None:
        set_gate(False)
        self.assertIn(HANDLE, publish.format_listing_text(self.home))
        self.assertIn(HANDLE, botchat.result_lines(self.home))
        self.assertIn("🚩", json.dumps(botactions.detail_keyboard(self.home), ensure_ascii=False))
        self.assertIn(HANDLE, json.dumps(alerts.alert_keyboard(self.alert(), self.home), ensure_ascii=False))
        self.assertIn("گزارش", freshness.contact_warning(3)["text"])


@override_settings(SECRET_KEY=TEST_SECRET)
class OldPageTests(GateCase):
    def setUp(self) -> None:
        super().setUp()
        session = self.client.session
        session["chamedoon_user_id"] = self.user.pk
        session.save()
        self.url = f"/app/home/{self.home.pk}/"

    def test_gate_off_page_shows_the_contact_as_before(self) -> None:
        set_gate(False)
        self.assertContains(self.client.get(self.url), HANDLE)

    def test_gate_on_page_holds_no_contact_until_asked(self) -> None:
        with patch("home.gate.bot.call") as telegram:
            response = self.client.get(self.url)
        telegram.assert_not_called()
        self.assertNotContains(response, HANDLE)
        self.assertContains(response, "?contact=1")
        self.assertFalse(ContactReveal.objects.exists())

    def test_asking_as_a_non_member_offers_the_channel(self) -> None:
        with tg_answer("left"):
            response = self.client.get(self.url + "?contact=1")
        self.assertNotContains(response, HANDLE)
        self.assertContains(response, "https://t.me/chamedoon_toronto")

    def test_asking_as_a_member_shows_the_contact(self) -> None:
        with tg_answer("member"):
            response = self.client.get(self.url + "?contact=1")
        self.assertContains(response, HANDLE)
        self.assertEqual(ContactReveal.objects.count(), 1)

    def test_banned_and_cap_and_closed_show_no_contact(self) -> None:
        with tg_answer("kicked"):
            self.assertNotContains(self.client.get(self.url + "?contact=1"), HANDLE)
        with patch("home.gate._record", side_effect=gate.GateRefused("reveal_cap")), tg_answer("member"):
            self.assertNotContains(self.client.get(self.url + "?contact=1"), HANDLE)
        HomeListing.objects.filter(pk=self.home.pk).update(status=ListingStatus.RENTED)
        self.assertNotContains(self.client.get(self.url + "?contact=1"), HANDLE)


class PostEditTests(GateCase):
    def setUp(self) -> None:
        super().setUp()
        HomeListing.objects.filter(pk=self.home.pk).update(published_at=timezone.now(), channel=self.country_channel, channel_message_id=11, channel_message_ids=[11])
        HomePublication.objects.create(listing=self.home, channel=self.channel, published_at=timezone.now(), message_ids=[22])
        self.sleep = patch("home.status_posts.time.sleep").start()
        self.addCleanup(patch.stopall)

    def queue(self):
        return status_posts.queue_contact_edits(HomeListing.objects.filter(pk=self.home.pk))

    def test_refuses_while_the_gate_is_off_and_touches_nothing(self) -> None:
        set_gate(False)
        with patch("home.status_posts.bot.call") as telegram:
            self.assertEqual(self.queue(), {"queued": 0, "skipped": "contact gate is off"})
        telegram.assert_not_called()
        self.home.refresh_from_db()
        self.assertFalse(self.home.status_post_pending)
        self.assertIsNone(self.home.contact_edit_queued_at)

    def test_flag_on_alone_edits_nothing(self) -> None:
        with patch("home.status_posts.bot.call") as telegram:
            status_posts.retry_pending()
        telegram.assert_not_called()

    def test_edits_every_post_with_no_contact_then_stays_done(self) -> None:
        with patch("home.status_posts.bot.call", return_value={}) as telegram:
            result = self.queue()
        self.assertEqual((result["queued"], result["edited"]), (1, 2))
        self.assertEqual(telegram.call_count, 2)
        for call in telegram.call_args_list:
            self.assertNotIn(HANDLE, call.args[1]["text"])
            self.assertNotIn("RentToronto", call.args[1]["text"])
        self.sleep.assert_called_once_with(status_posts.CONTACT_EDIT_PAUSE_SECONDS)
        self.home.refresh_from_db()
        self.assertFalse(self.home.status_post_pending)
        self.assertIsNotNone(self.home.contact_edit_done_at)
        with patch("home.status_posts.bot.call") as again:
            self.assertEqual(self.queue()["queued"], 0)
        again.assert_not_called()

    def test_rented_and_reported_posts_are_left_alone(self) -> None:
        HomeListing.objects.filter(pk=self.home.pk).update(status_post=StatusPost.RENTED)
        with patch("home.status_posts.bot.call") as telegram:
            self.assertEqual(self.queue()["queued"], 0)
        telegram.assert_not_called()

    def test_429_keeps_it_queued_and_stops_the_batch(self) -> None:
        with patch("home.status_posts.bot.call", side_effect=bot.BotError("Too Many Requests", code=429, retry_after=30)):
            result = self.queue()
        self.assertEqual(result["retry_after"], 30)
        self.home.refresh_from_db()
        self.assertTrue(self.home.status_post_pending)
        self.assertIsNone(self.home.contact_edit_done_at)

    def test_a_refused_edit_is_recorded_and_can_be_queued_again(self) -> None:
        with patch("home.status_posts.bot.call", side_effect=bot.BotError("Bad Request: message to edit not found", code=400)):
            self.queue()
        self.home.refresh_from_db()
        self.assertIsNone(self.home.contact_edit_done_at)
        self.assertIn("not found", self.home.status_post_error)
        with patch("home.status_posts.bot.call", return_value={}):
            self.assertEqual(self.queue()["queued"], 1)

    def test_turning_the_gate_off_pauses_the_queue_so_no_post_regains_the_contact(self) -> None:
        with patch("home.status_posts.bot.call", side_effect=bot.BotError("Too Many Requests", code=429, retry_after=30)):
            self.queue()
        set_gate(False)
        with patch("home.status_posts.bot.call") as telegram:
            status_posts.retry_pending()
        telegram.assert_not_called()

    def test_a_status_edit_waiting_goes_before_the_queue(self) -> None:
        other = self.listing(2, contact_telegram="other_person")
        HomeListing.objects.filter(pk=other.pk).update(published_at=timezone.now(), channel=self.country_channel, channel_message_id=33, channel_message_ids=[33], status_post=StatusPost.RENTED, status_post_pending=True)
        HomeListing.objects.filter(pk=self.home.pk).update(status_post=StatusPost.RESTORED, status_post_pending=True, contact_edit_queued_at=timezone.now())
        with patch("home.status_posts.bot.call", return_value={}) as telegram:
            status_posts.retry_pending(limit=1)
        self.assertEqual(telegram.call_args.args[1]["message_id"], 33)

    def test_admin_action(self) -> None:
        staff = make_staff()
        self.client.force_login(staff)
        with patch("home.status_posts.bot.call", return_value={}) as telegram:
            response = self.client.post(
                "/admin/home/homelisting/",
                {"action": "take_contact_out_of_posts", "_selected_action": [self.home.pk]},
                follow=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(telegram.call_count, 2)


def make_staff():
    from django.contrib.auth.models import User as StaffUser

    return StaffUser.objects.create_superuser("gate_admin", "gate@example.invalid", "not-a-real-secret")

