"""A poster without a Telegram username: phone-only posts, E.164 numbers, and the number Telegram shares with the bot."""

from __future__ import annotations

from django.test import SimpleTestCase

from home.botchat import shared_contact
from home.models import HomeListing, HomeUserPrefs
from home.phones import to_e164
from tests.helpers import bearer_auth, make_user
from tests.test_home_p4 import LISTINGS, P4Case

PHONE = "/api/v1/me/phone/"


class E164Tests(SimpleTestCase):
    def test_numbers(self) -> None:
        cases = [
            ("00989356706868", "", "+989356706868"),
            ("00989356706868", "CA", "+989356706868"),
            ("+98 935 670 6868", "DE", "+989356706868"),
            ("۰۰۹۸۹۳۵۶۷۰۶۸۶۸", "CA", "+989356706868"),
            ("09356706868", "CA", "+989356706868"),
            ("416-555-0142", "CA", "+14165550142"),
            ("(416) 555 0142", "US", "+14165550142"),
            ("1 416 555 0142", "CA", "+14165550142"),
            ("0151 2345 6789", "DE", "+4915123456789"),
            ("+49 151 23456789", "CA", "+4915123456789"),
        ]
        for raw, country, want in cases:
            with self.subTest(raw=raw, country=country):
                self.assertEqual(to_e164(raw, country), want)

    def test_not_a_number(self) -> None:
        for raw in ("", "abc", "@sara_home", "12345", "+1234567890123456", "0151 23", "+1 416 555 0142 ext 3"):
            with self.subTest(raw=raw):
                self.assertEqual(to_e164(raw, "CA"), "")


class PhoneOnlyPostTests(P4Case):
    def test_phone_only_post_publishes_with_the_number_in_e164(self) -> None:
        self.auth = bearer_auth(make_user(telegram_user_id=880031))
        response = self.send(self.form(phone="00989356706868"))
        self.assertEqual(response.status_code, 201, response.content)
        listing = HomeListing.objects.get(pk=response.json()["listing_id"])
        self.assertEqual((listing.contact_telegram, listing.contact_phone), ("", "+989356706868"))

    def test_local_number_reads_in_the_listings_country(self) -> None:
        listing = self.posted(phone="416 555 0142")
        self.assertEqual(listing.contact_phone, "+14165550142")

    def test_a_typed_number_that_is_not_a_phone_is_refused(self) -> None:
        body = self.assert_error(self.send(self.form(phone="call me")), "validation_failed", LISTINGS)
        self.assertEqual(body["fields"], {"phone": ["no_contact"]})
        self.assertFalse(HomeListing.objects.exists())


def contact_update(sender: int, owner: int | None, number: str = "989356706868", chat_type: str = "private") -> dict:
    contact = {"phone_number": number, "first_name": "Sara"}
    if owner is not None:
        contact["user_id"] = owner
    return {"update_id": 1, "message": {"message_id": 5, "from": {"id": sender}, "chat": {"id": sender, "type": chat_type}, "contact": contact}}


class SharedContactTests(P4Case):
    def test_own_contact_is_kept_and_read_back(self) -> None:
        self.assertEqual(self.client.get(PHONE, **self.auth).json(), {"phone": ""})
        tid = self.user.telegram_user_id
        self.assertTrue(shared_contact(contact_update(tid, tid)))
        self.assertEqual(HomeUserPrefs.objects.get(user=self.user).verified_phone, "+989356706868")
        response = self.client.get(PHONE, **self.auth)
        self.assertEqual((response.status_code, response.json()), (200, {"phone": "+989356706868"}))

    def test_someone_elses_contact_is_not_theirs(self) -> None:
        tid = self.user.telegram_user_id
        self.assertFalse(shared_contact(contact_update(tid, tid + 1)))
        self.assertFalse(shared_contact(contact_update(tid, None)))
        self.assertFalse(shared_contact(contact_update(tid, tid, chat_type="group")))
        self.assertFalse(HomeUserPrefs.objects.filter(user=self.user).exclude(verified_phone="").exists())

    def test_plain_messages_pass_through(self) -> None:
        self.assertFalse(shared_contact({"update_id": 2, "message": {"from": {"id": 1}, "chat": {"id": 1, "type": "private"}, "text": "سلام"}}))

    def test_needs_a_token(self) -> None:
        self.assertEqual(self.client.get(PHONE).status_code, 401)
