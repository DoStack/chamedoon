"""The join gate with a city group: the contact and a new post both need the city's channel and its group."""

from __future__ import annotations

from unittest.mock import patch

from home import bot
from home.models import ChannelMembership, ContactReveal, GateChat, HomeChannel, HomeSettings
from tests.helpers import bearer_auth
from tests.test_contact_gate import CONTACT_PATTERN, HANDLE, GateCase

JOIN_CHECK = "/api/v1/me/join-check/"
GROUP_ID = -1004488776441
INVITE = "https://t.me/+AbCdEfGh123"


def telegram(channel: str = "member", group: str = "member", *, group_error: bot.BotError | None = None, invite: str = INVITE):
    """bot.call answering per method and chat: the channel by its username, the group by its id."""

    def answer(method, payload=None, **_kwargs):
        if method == "getChatMember":
            if payload["chat_id"] == GROUP_ID:
                if group_error:
                    raise group_error
                return {"status": group}
            return {"status": channel}
        if method == "createChatInviteLink":
            return {"invite_link": invite}
        raise AssertionError(f"unexpected {method}")

    return patch("home.gate.bot.call", side_effect=answer)


class GroupGateCase(GateCase):
    def setUp(self) -> None:
        super().setUp()
        self.channel.group_chat_id = GROUP_ID
        self.channel.group_title = "چمدون - خانه | تورنتو · گفت‌وگو"
        self.channel.save()

    def poster_gate(self, on: bool = True) -> None:
        HomeSettings.objects.filter(pk=1).update(join_gate_poster=on)

    def join_check(self, country: str = "CA", city: str = "Toronto"):
        return self.client.post(JOIN_CHECK, {"country": country, "city": city}, content_type="application/json", **bearer_auth(self.user))


class ContactWithGroupTests(GroupGateCase):
    def test_member_of_both_sees_the_contact(self) -> None:
        with telegram() as call:
            body = self.contact().json()
        self.assertEqual((body["gate"], body["telegram"]), ("member", HANDLE))
        self.assertEqual(call.call_count, 2)

    def test_missing_the_group_asks_only_for_the_group(self) -> None:
        with telegram(group="left"):
            body = self.assert_error(self.contact(), "not_member", CONTACT_PATTERN)
        self.assertEqual([chat["kind"] for chat in body["chats"]], ["group"])
        self.assertEqual(body["chats"][0]["join_url"], INVITE)
        self.assertEqual(body["join_url"], INVITE)
        self.assertEqual(body["chats"][0]["title"], "چمدون - خانه | تورنتو · گفت‌وگو")
        self.assertEqual(body["chats"][0]["photo_url"], f"/api/v1/chats/{self.channel.pk}/group/photo/")
        self.assertFalse(ContactReveal.objects.exists())

    def test_missing_both_lists_channel_then_group(self) -> None:
        with telegram(channel="left", group="left"):
            body = self.assert_error(self.contact(), "not_member", CONTACT_PATTERN)
        self.assertEqual([chat["kind"] for chat in body["chats"]], ["channel", "group"])
        self.assertEqual(body["chats"][0]["join_url"], "https://t.me/chamedoon_toronto")

    def test_the_bot_makes_one_invite_link_and_keeps_it(self) -> None:
        with telegram(group="left") as call:
            self.contact()
            self.contact()
        made = [c for c in call.call_args_list if c.args[0] == "createChatInviteLink"]
        self.assertEqual(len(made), 1)
        self.assertEqual(made[0].args[1]["chat_id"], GROUP_ID)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.group_invite_link, INVITE)

    def test_a_public_group_is_joined_by_its_username(self) -> None:
        HomeChannel.objects.filter(pk=self.channel.pk).update(group_username="chamedoon_khane_toronto_chat")
        with telegram(group="left") as call:
            body = self.assert_error(self.contact(), "not_member", CONTACT_PATTERN)
        self.assertEqual(body["chats"][0]["join_url"], "https://t.me/chamedoon_khane_toronto_chat")
        self.assertNotIn("createChatInviteLink", [c.args[0] for c in call.call_args_list])

    def test_a_group_with_no_link_to_join_lets_the_person_through(self) -> None:
        def answer(method, payload=None, **_kwargs):
            if method == "createChatInviteLink":
                raise bot.BotError("Bad Request: not enough rights to manage chat invite link", code=400)
            return {"status": "left" if payload["chat_id"] == GROUP_ID else "member"}

        with patch("home.gate.bot.call", side_effect=answer), self.assertLogs("home.gate", level="WARNING"):
            self.assertEqual(self.contact().json()["gate"], "member")

    def test_a_group_error_fails_open(self) -> None:
        with telegram(group_error=bot.BotError("Telegram unreachable: timed out")), self.assertLogs("home.gate", level="WARNING"):
            body = self.contact().json()
        self.assertEqual((body["gate"], body["telegram"]), ("failed_open", HANDLE))
        self.assertTrue(ContactReveal.objects.get().failed_open)

    def test_membership_is_kept_per_chat(self) -> None:
        with telegram():
            self.contact()
        self.assertEqual(set(ChannelMembership.objects.values_list("chat", flat=True)), {GateChat.CHANNEL, GateChat.GROUP})
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

    def test_not_member_gets_both_rows(self) -> None:
        self.poster_gate()
        with telegram(channel="left", group="left"):
            body = self.join_check().json()
        self.assertEqual(body["gate"], "not_member")
        self.assertEqual([(chat["kind"], chat["join_url"]) for chat in body["chats"]], [("channel", "https://t.me/chamedoon_toronto"), ("group", INVITE)])

    def test_member_of_both_goes_ahead(self) -> None:
        self.poster_gate()
        with telegram():
            self.assertEqual(self.join_check().json(), {"chats": [], "gate": "member"})

    def test_telegram_error_lets_the_post_through(self) -> None:
        self.poster_gate()
        with telegram(group_error=bot.BotError("Too Many Requests", code=429)), self.assertLogs("home.gate", level="WARNING"):
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
