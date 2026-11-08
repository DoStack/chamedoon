"""The join gate with a city group: since 2026-11-08 the contact and a new post need only the city's channel, never its group."""

from __future__ import annotations

from unittest.mock import patch

from home import bot
from home.models import ChannelMembership, ContactReveal, GateChat, HomeChannel, HomeSettings
from tests.helpers import bearer_auth
from tests.test_contact_gate import CONTACT_PATTERN, HANDLE, GateCase

JOIN_CHECK = "/api/v1/me/join-check/"
GROUP_ID = -1004488776441
GROUP_URL = "https://t.me/chamedoon_toronto_khane_chat"


def telegram(channel: str = "member", group: str = "member", *, group_error: bot.BotError | None = None, channel_error: bot.BotError | None = None):
    """bot.call answering per method and chat: the channel by its username, the group by its id."""

    def answer(method, payload=None, **_kwargs):
        if method == "getChatMember":
            if payload["chat_id"] == GROUP_ID:
                if group_error:
                    raise group_error
                return {"status": group}
            if channel_error:
                raise channel_error
            return {"status": channel}
        raise AssertionError(f"unexpected {method}")

    return patch("home.gate.bot.call", side_effect=answer)


class GroupGateCase(GateCase):
    def setUp(self) -> None:
        super().setUp()
        self.channel.group_chat_id = GROUP_ID
        self.channel.group_username = "chamedoon_toronto_khane_chat"
        self.channel.group_title = "چمدون - خانه | تورنتو · گفت‌وگو"
        self.channel.save()

    def poster_gate(self, on: bool = True) -> None:
        HomeSettings.objects.filter(pk=1).update(join_gate_poster=on)

    def join_check(self, country: str = "CA", city: str = "Toronto"):
        return self.client.post(JOIN_CHECK, {"country": country, "city": city}, content_type="application/json", **bearer_auth(self.user))


class ContactWithGroupTests(GroupGateCase):
    """Decision 2026-11-08: a channel that has a group still asks only for the channel."""

    def test_channel_member_sees_the_contact_without_a_group_check(self) -> None:
        with telegram(group="left") as call:
            body = self.contact().json()
        self.assertEqual((body["gate"], body["telegram"]), ("member", HANDLE))
        self.assertEqual([c.args[1]["chat_id"] for c in call.call_args_list], [self.channel.target()])

    def test_missing_the_channel_asks_only_for_the_channel(self) -> None:
        with telegram(channel="left", group="left"):
            body = self.assert_error(self.contact(), "not_member", CONTACT_PATTERN)
        self.assertEqual([chat["kind"] for chat in body["chats"]], ["channel"])
        self.assertEqual(body["chats"][0]["join_url"], "https://t.me/chamedoon_toronto")
        self.assertFalse(ContactReveal.objects.exists())

    def test_no_bot_invite_link_is_ever_made(self) -> None:
        with telegram(channel="left", group="left") as call:
            self.contact()
        self.assertEqual({c.args[0] for c in call.call_args_list}, {"getChatMember"})

    def test_a_group_error_is_never_reached(self) -> None:
        with telegram(group_error=bot.BotError("Telegram unreachable: timed out")):
            body = self.contact().json()
        self.assertEqual((body["gate"], body["telegram"]), ("member", HANDLE))

    def test_membership_is_kept_per_chat(self) -> None:
        with telegram():
            self.contact()
        self.assertEqual(set(ChannelMembership.objects.values_list("chat", flat=True)), {GateChat.CHANNEL})
        with telegram() as call:
            self.contact()
        call.assert_not_called()

    def test_the_check_waits_two_seconds_at_most(self) -> None:
        with telegram() as call:
            self.contact()
        self.assertTrue(all(c.kwargs["timeout"] == 2 for c in call.call_args_list))


class JoinCheckTests(GroupGateCase):
    def test_off_lets_every_post_through_without_asking_telegram(self) -> None:
        self.poster_gate(False)
        with telegram(channel="left", group="left") as call:
            body = self.join_check().json()
        self.assertEqual(body, {"chats": [], "gate": "gate_off"})
        call.assert_not_called()

    def test_not_member_gets_only_the_channel_row(self) -> None:
        self.poster_gate()
        with telegram(channel="left", group="left"):
            body = self.join_check().json()
        self.assertEqual(body["gate"], "not_member")
        self.assertEqual([(chat["kind"], chat["join_url"]) for chat in body["chats"]], [("channel", "https://t.me/chamedoon_toronto")])

    def test_member_of_both_goes_ahead(self) -> None:
        self.poster_gate()
        with telegram():
            self.assertEqual(self.join_check().json(), {"chats": [], "gate": "member"})

    def test_telegram_error_lets_the_post_through(self) -> None:
        self.poster_gate()
        with telegram(channel_error=bot.BotError("Too Many Requests", code=429)), self.assertLogs("home.gate", level="WARNING"):
            self.assertEqual(self.join_check().json(), {"chats": [], "gate": "failed_open"})

    def test_a_city_without_a_channel_asks_for_the_country_channel(self) -> None:
        self.poster_gate()
        with telegram(channel="left") as call:
            body = self.join_check(city="Ottawa").json()
        self.assertEqual([chat["join_url"] for chat in body["chats"]], ["https://t.me/chamedoon_ca"])
        self.assertEqual(call.call_count, 1)

    def test_a_country_with_no_channel_goes_ahead(self) -> None:
        self.poster_gate()
        with telegram(channel="left") as call:
            self.assertEqual(self.join_check(country="DE", city="Munich").json(), {"chats": [], "gate": "no_channel"})
        call.assert_not_called()

    def test_banned_poster_is_not_held_back(self) -> None:
        self.poster_gate()
        with telegram(channel="kicked"):
            self.assertEqual(self.join_check().json()["chats"], [])

    def test_bad_body(self) -> None:
        self.poster_gate()
        response = self.client.post(JOIN_CHECK, {}, content_type="application/json", **bearer_auth(self.user))
        self.assertEqual(response.json()["code"], "validation_failed")


class ChatPhotoTests(GroupGateCase):
    def test_photo_comes_through_the_bot_and_is_cached(self) -> None:
        with patch("home_api.views.home_bot.chat_photo", return_value=b"\xff\xd8jpeg") as photo:
            response = self.client.get(f"/api/v1/chats/{self.channel.pk}/group/photo/")
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "image/jpeg"))
        self.assertIn("s-maxage", response["Cache-Control"])
        self.assertEqual(photo.call_args.args[0], GROUP_ID)

    def test_no_photo_or_unknown_chat_is_404(self) -> None:
        with patch("home_api.views.home_bot.chat_photo", return_value=None):
            self.assertEqual(self.client.get(f"/api/v1/chats/{self.channel.pk}/channel/photo/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/chats/{self.country_channel.pk}/group/photo/").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/chats/999999/channel/photo/").status_code, 404)
