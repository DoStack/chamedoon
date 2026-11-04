from __future__ import annotations

from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from telethon.errors import FloodWaitError

from home import botactions, botchat, publish, snapshots
from home.contact_text import handle_in_text
from home.ingest import _store
from home.management.commands.poster_backfill import apply_message, clean_contacts, fill_from_stored, read_batch, report, reread
from home.models import HomeListing, HomePost, HomeSettings
from home.public import contact_handle
from home.review import apply_review, build_prompt
from home.telegram import home_message_to_raw
from market.migrate import author_identity
from market.models import MarketPost
from market.poster import poster_contact, read_poster
from market.telegram_history import message_to_raw
from tests.helpers import bearer_auth, readable_text
from tests.test_contact_gate import set_gate, tg_answer
from tests.test_home import make_post
from tests.test_home_api import ApiCase
from tests.test_home_photos import CA_ACCEPT, CA_TEXT_NO_PHONE
from tests.test_snapshots import FakeStorage

POSTER = "real_poster7"
GROUP_ID = 1500000001
MARKED_GROUP = -1001500000001


def person(username: str = POSTER, user_id: int = 4242, **extra):
    return SimpleNamespace(id=user_id, username=username, first_name="Sara", last_name="B", bot=False, **extra)


def channel(username: str = "some_channel", channel_id: int = 7777):
    return SimpleNamespace(id=channel_id, username=username, title="A channel")


def message(sender=None, sender_id=None, forward=None, post=False):
    return SimpleNamespace(sender=sender, sender_id=sender_id, forward=forward, post=post)


class ReadPosterTests(SimpleTestCase):
    def read(self, *args, **kwargs) -> dict:
        return read_poster(message(*args, **kwargs), chat_id=GROUP_ID)

    def test_a_person(self) -> None:
        row = self.read(person(), 4242)
        self.assertEqual(
            (row["poster_user_id"], row["poster_username"], row["poster_name"], row["poster_kind"], row["poster_via"]),
            (4242, POSTER, "Sara B", "person", "sender"),
        )
        self.assertIsNotNone(row["poster_read_at"])

    def test_a_person_with_only_a_collectible_username(self) -> None:
        names = [SimpleNamespace(username="old_one", active=False), SimpleNamespace(username="live_one", active=True)]
        self.assertEqual(self.read(person(username=None, usernames=names), 4242)["poster_username"], "live_one")

    def test_a_bot(self) -> None:
        bot = SimpleNamespace(id=9, username="rent_bot", first_name="Rent", last_name=None, bot=True)
        self.assertEqual(self.read(bot, 9)["poster_kind"], "bot")

    def test_a_channels_own_post(self) -> None:
        row = self.read(channel("my_board", GROUP_ID), MARKED_GROUP, post=True)
        self.assertEqual((row["poster_kind"], row["poster_username"]), ("channel", "my_board"))

    def test_an_anonymous_admin_writes_as_the_group(self) -> None:
        self.assertEqual(self.read(channel("the_group", GROUP_ID), MARKED_GROUP)["poster_kind"], "anonymous")
        self.assertEqual(self.read(None, None)["poster_kind"], "anonymous")

    def test_telegrams_stand_in_for_an_anonymous_admin(self) -> None:
        stand_in = SimpleNamespace(id=1087968824, username="GroupAnonymousBot", first_name="Group", last_name=None, bot=True)
        self.assertEqual(self.read(stand_in, 1087968824)["poster_kind"], "anonymous")

    def test_someone_writing_as_another_channel(self) -> None:
        self.assertEqual(self.read(channel("their_page"), -1007777)["poster_kind"], "channel")

    def test_no_entity_from_telegram_says_unknown(self) -> None:
        self.assertEqual(self.read(None, 4242)["poster_kind"], "unknown")

    def test_a_forward_belongs_to_its_author_when_that_is_a_person(self) -> None:
        forward = SimpleNamespace(sender=person("the_author", 55), sender_id=55)
        row = self.read(person("the_forwarder", 66), 66, forward=forward)
        self.assertEqual((row["poster_username"], row["poster_user_id"], row["poster_via"]), ("the_author", 55, "forward_author"))

    def test_a_hidden_author_leaves_the_forwarder(self) -> None:
        forward = SimpleNamespace(sender=None, sender_id=None, from_name="Hidden")
        row = self.read(person("the_forwarder", 66), 66, forward=forward)
        self.assertEqual((row["poster_username"], row["poster_kind"], row["poster_via"]), ("the_forwarder", "person", "forwarder"))

    def test_a_forward_from_a_channel_or_a_bot_leaves_the_forwarder(self) -> None:
        for author in (channel(), SimpleNamespace(id=3, username="x_bot", first_name="X", last_name=None, bot=True)):
            forward = SimpleNamespace(sender=author, sender_id=3)
            row = self.read(person("the_forwarder", 66), 66, forward=forward)
            self.assertEqual((row["poster_username"], row["poster_via"]), ("the_forwarder", "forwarder"))

    def test_a_channel_forwarding_from_a_hidden_author_is_a_channel(self) -> None:
        forward = SimpleNamespace(sender=None, sender_id=None)
        row = self.read(channel("my_board", GROUP_ID), MARKED_GROUP, forward=forward, post=True)
        self.assertEqual((row["poster_kind"], row["poster_via"]), ("channel", "forwarder"))


class PosterContactTests(SimpleTestCase):
    def contact(self, kind: str, username: str = POSTER, source: str = "RentToronto") -> str:
        return poster_contact(SimpleNamespace(poster_kind=kind, poster_username=username), source_username=source)

    def test_only_a_person_counts(self) -> None:
        self.assertEqual(self.contact("person"), POSTER)
        for kind in ("bot", "channel", "anonymous", "unknown", ""):
            self.assertEqual(self.contact(kind), "", kind)

    def test_never_a_bot_name_or_the_source_or_a_broken_name(self) -> None:
        for username in ("rent_bot", "RentBOT", "renttoronto", "", "two words", "ab"):
            self.assertEqual(self.contact("person", username), "", username)


class HandleInTextTests(SimpleTestCase):
    def test_written_in_the_post(self) -> None:
        for text in ("call @Sara_B now", "t.me/sara_b", "https://t.me/sara_b/", "آیدی sara_b"):
            self.assertTrue(handle_in_text("sara_b", text), text)

    def test_not_a_handle_or_not_in_the_post(self) -> None:
        for handle, text in (("unknown", "unknown"), ("null", "null"), ("gmail", "me@gmail.com"), ("Musa Gholame", "Musa Gholame"), ("Y", "Y"), ("pv", "pv"), ("sara_b", "sara_bb"), ("sara_b", "")):
            self.assertFalse(handle_in_text(handle, text), handle)


class CrawlTests(ApiCase):
    def raw(self, message_id: int, *, sender, sender_id, text: str = CA_TEXT_NO_PHONE) -> dict:
        crawled = SimpleNamespace(id=message_id, date=timezone.now(), message=text, action=None, views=1, photo=None, grouped_id=None, sender=sender, sender_id=sender_id, forward=None, post=False)
        return home_message_to_raw("RentToronto", crawled, public_username="RentToronto", chat_id=GROUP_ID)

    def test_a_new_post_stores_its_poster(self) -> None:
        self.assertTrue(_store(self.source, self.raw(1, sender=person(), sender_id=4242)))
        post = HomePost.objects.get(telegram_message_id=1)
        self.assertEqual((post.poster_user_id, post.poster_username, post.poster_kind, post.poster_via), (4242, POSTER, "person", "sender"))
        self.assertIsNotNone(post.poster_read_at)

    def test_a_post_with_a_contact_in_its_text_still_stores_its_poster(self) -> None:
        _store(self.source, self.raw(2, sender=person(), sender_id=4242, text=f"{CA_TEXT_NO_PHONE} @someone_else"))
        self.assertEqual(HomePost.objects.get(telegram_message_id=2).poster_username, POSTER)

    def test_a_courier_post_stores_its_poster_and_a_bot_is_no_author(self) -> None:
        base = {"id": 3, "date": timezone.now(), "message": "x", "action": None, "views": 1, "photo": None, "post_author": "", "forward": None, "post": False}
        raw = message_to_raw("board", SimpleNamespace(**base, sender=person(), sender_id=4242), public_username="board", chat_id=GROUP_ID)
        self.assertEqual((raw["author_username"], raw["poster_username"], raw["poster_kind"]), (POSTER, POSTER, "person"))
        bot = SimpleNamespace(id=9, username="rent_bot", first_name="Rent", last_name=None, bot=True)
        raw = message_to_raw("board", SimpleNamespace(**base, sender=bot, sender_id=9), public_username="board", chat_id=GROUP_ID)
        self.assertEqual((raw["author_username"], raw["poster_username"], raw["poster_kind"]), ("", "rent_bot", "bot"))

    def test_the_courier_contact_order(self) -> None:
        now = timezone.now()
        post = MarketPost(channel_username="board", telegram_message_id=1, posted_at=now, text="from @in_the_text", author_username=POSTER, poster_username=POSTER, poster_kind="person")
        self.assertEqual(author_identity(post)[0], "in_the_text")
        post.text = "nothing written"
        self.assertEqual(author_identity(post)[0], POSTER)
        post.poster_kind = "bot"
        self.assertEqual(author_identity(post)[0], "")
        # A web preview's author is the post's own contact button, with no poster known.
        preview = MarketPost(channel_username="board", telegram_message_id=2, posted_at=now, text="from @in_the_text", author_username="button_user")
        self.assertEqual(author_identity(preview)[0], "button_user")


class ReviewKeepsItsHandsOffTests(ApiCase):
    def post(self, number: int = 1, text: str = CA_TEXT_NO_PHONE, **extra) -> HomePost:
        return make_post(self.source, number, text=text, posted_at=timezone.now() - timedelta(hours=1), source_url=f"https://t.me/RentToronto/{number}", **extra)

    def test_the_model_never_sees_who_posted(self) -> None:
        prompt = build_prompt(self.post(author_username=POSTER, author_name="Sara B", poster_username=POSTER, poster_name="Sara B", poster_kind="person"))
        self.assertNotIn(POSTER, prompt)
        self.assertNotIn("Sara B", prompt)

    def test_what_the_model_says_about_the_poster_is_ignored(self) -> None:
        post = self.post(poster_username=POSTER, poster_kind="person", poster_user_id=4242)
        apply_review(post, {**CA_ACCEPT, "contact_phone": "", "contact_telegram": "", "poster_username": "made_up", "poster_kind": "bot", "poster_user_id": 1})
        post.refresh_from_db()
        self.assertEqual((post.poster_username, post.poster_kind, post.poster_user_id), (POSTER, "person", 4242))

    def test_a_contact_the_model_made_up_is_dropped(self) -> None:
        for number, value in enumerate(("unknown", "null", "Musa Gholame", "🎾", "some_guess1", POSTER), start=1):
            post = self.post(number, text=f"{CA_TEXT_NO_PHONE} {number}", poster_username=POSTER, poster_kind="person")
            apply_review(post, {**CA_ACCEPT, "contact_phone": "", "contact_telegram": value})
            self.assertEqual(HomeListing.objects.get(post=post).contact_telegram, "", value)

    def test_a_contact_in_the_text_is_kept(self) -> None:
        post = self.post(text=f"{CA_TEXT_NO_PHONE} @sara_home", poster_username=POSTER, poster_kind="person")
        apply_review(post, {**CA_ACCEPT, "contact_phone": "", "contact_telegram": "sara_home"})
        self.assertEqual(HomeListing.objects.get(post=post).contact_telegram, "sara_home")

    def test_a_listing_posted_in_the_app_keeps_the_contact_its_owner_gave(self) -> None:
        app = type(self.source).objects.create(username="app_ca", country="CA", default_currency="CAD")
        post = make_post(app, 1, text=CA_TEXT_NO_PHONE, posted_at=timezone.now())
        apply_review(post, {**CA_ACCEPT, "contact_phone": "", "contact_telegram": "owner_handle"})
        self.assertEqual(HomeListing.objects.get(post=post).contact_telegram, "owner_handle")


class PosterCase(ApiCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()

    def poster_listing(self, number: int = 1, *, kind: str = "person", username: str = POSTER, phone: str = "", text_handle: str = "") -> HomeListing:
        listing = self.listing(number, contact_telegram=text_handle, contact_phone=phone)
        HomePost.objects.filter(pk=listing.post_id).update(poster_username=username, poster_kind=kind, poster_via="sender", poster_user_id=4242)
        return HomeListing.objects.select_related("post__source").get(pk=listing.pk)

    def reveal(self, listing: HomeListing):
        return self.client.post(f"/api/v1/me/listings/{listing.pk}/contact/", **bearer_auth(self.user))


class ContactOrderTests(PosterCase):
    def test_text_then_poster_then_the_post(self) -> None:
        self.assertEqual(contact_handle(self.poster_listing(1)), POSTER)
        self.assertEqual(contact_handle(self.poster_listing(2, text_handle="in_the_text")), "in_the_text")
        self.assertEqual(contact_handle(self.poster_listing(3, phone="647-555-0142")), "")
        self.assertEqual(contact_handle(self.poster_listing(4, username="")), "")

    def test_a_bot_channel_or_anonymous_poster_is_no_contact(self) -> None:
        for number, kind in enumerate(("bot", "channel", "anonymous", "unknown", ""), start=1):
            self.assertEqual(contact_handle(self.poster_listing(number, kind=kind)), "", kind)

    def test_the_contact_opens_a_chat_with_the_poster(self) -> None:
        listing = self.poster_listing()
        set_gate(False)
        body = self.reveal(listing).json()
        self.assertEqual((body["telegram"], body["telegram_url"]), (POSTER, f"https://t.me/{POSTER}"))
        self.assertTrue(body["source_url"])

    def test_the_gate_guards_the_poster_like_any_contact(self) -> None:
        listing = self.poster_listing()
        set_gate(True)
        with tg_answer("left"):
            refused = self.reveal(listing)
        self.assertEqual(refused.json()["code"], "not_member")
        self.assertNotIn(POSTER, refused.content.decode())
        with tg_answer("member"):
            body = self.reveal(listing).json()
        self.assertEqual((body["telegram"], body["gate"]), (POSTER, "member"))

    def test_no_username_still_offers_the_post(self) -> None:
        listing = self.poster_listing(username="")
        set_gate(False)
        body = self.reveal(listing).json()
        self.assertEqual(body["telegram"], "")
        self.assertTrue(body["source_url"] and body["has_any"])

    def test_the_bot_opens_the_poster_only_when_asked(self) -> None:
        listing = self.poster_listing()
        set_gate(False)
        self.assertIn(POSTER, botchat.contact_message(listing))
        text, keyboard = botactions.contact_text(listing, 880001)
        self.assertIn(POSTER, text)
        self.assertIn(f"https://t.me/{POSTER}", str(keyboard))

    def test_a_deleted_listing_forgets_the_poster_name(self) -> None:
        from home.owner_posts import delete_listing

        app = type(self.source).objects.create(username="app_ca", country="CA", default_currency="CAD")
        listing = self.poster_listing()
        HomePost.objects.filter(pk=listing.post_id).update(source=app, author_id=self.user.telegram_user_id)
        delete_listing(self.user, listing.pk)
        self.assertEqual(HomePost.objects.get(pk=listing.post_id).poster_username, "")


class NothingPublicHoldsThePosterTests(PosterCase):
    """The poster's username leaves only through the contact path."""

    def setUp(self) -> None:
        super().setUp()
        HomeSettings.objects.update_or_create(pk=1, defaults={"snapshots_on": True})
        self.home = self.poster_listing()

    def test_it_says_a_telegram_contact_exists_and_never_which(self) -> None:
        body = self.client.get(f"/api/v1/public/listings/{self.home.pk}/")
        self.assertEqual(body.status_code, 200)
        self.assertTrue(body.json()["has_telegram"])
        self.assertNotIn(POSTER, body.content.decode())

    def test_not_in_the_public_list(self) -> None:
        body = self.client.get("/api/v1/public/feed/", {"country": "CA", "city": "Toronto"})
        self.assertEqual(body.status_code, 200, body.content)
        self.assertNotIn(POSTER, body.content.decode())
        self.assertNotIn(POSTER, readable_text(body.json()))

    def test_not_in_a_snapshot(self) -> None:
        storage = FakeStorage()
        with patch("home.snapshots.get_storage", return_value=storage):
            self.assertTrue(snapshots.write_snapshots()["ok"])
        self.assertTrue(storage.files)
        for key in storage.files:
            self.assertNotIn(POSTER, str(storage.json(key)), key)

    def test_not_in_a_channel_post_or_an_alert_button(self) -> None:
        from home.alerts import contact_button

        set_gate(False)
        self.assertNotIn(POSTER, publish.format_listing_text(self.home))
        self.assertNotIn(POSTER, publish.contact_line(self.home, phone=True))
        self.assertNotIn(POSTER, str(contact_button(self.home)))

    def test_not_in_search_results_in_the_bot(self) -> None:
        set_gate(False)
        self.assertNotIn(POSTER, botchat.result_lines(self.home))

    def test_the_old_page_shows_it_only_when_asked_even_with_the_gate_off(self) -> None:
        from miniapp.home_views import _contact_for

        set_gate(False)
        asked = SimpleNamespace(GET={"contact": "1"}, chamedoon_user=self.user)
        unasked = SimpleNamespace(GET={}, chamedoon_user=self.user)
        contact, state = _contact_for(unasked, self.home)
        self.assertEqual((contact["telegram_handle"], state), ("", {"state": "ask"}))
        contact, state = _contact_for(asked, self.home)
        self.assertEqual((contact["telegram_handle"], state), (POSTER, None))

    def test_a_name_it_wrote_in_the_summary_is_cut_from_public_text(self) -> None:
        HomeListing.objects.filter(pk=self.home.pk).update(summary_fa=f"اتاق {POSTER} در تورنتو")
        body = self.client.get(f"/api/v1/public/listings/{self.home.pk}/")
        self.assertNotIn(POSTER, body.content.decode())


class BackfillStoredTests(ApiCase):
    def stored(self, number: int, **fields) -> HomePost:
        return make_post(self.source, number, text=f"{CA_TEXT_NO_PHONE} {number}", posted_at=timezone.now(), **fields)

    def test_from_the_columns_kept_at_crawl_time(self) -> None:
        cases = {
            "person": self.stored(1, author_id=11, author_username="sara_home", author_name="Sara"),
            "person_no_name": self.stored(2, author_id=12, author_username=""),
            "bot": self.stored(3, author_id=13, author_username="rent_bot"),
            "channel": self.stored(4, author_id=-10077, author_username="a_page"),
            "stand_in": self.stored(5, author_id=777000),
            "none": self.stored(6),
        }
        fill_from_stored()
        for post in cases.values():
            post.refresh_from_db()
        self.assertEqual((cases["person"].poster_kind, cases["person"].poster_username, cases["person"].poster_user_id, cases["person"].poster_via), ("person", "sara_home", 11, "sender"))
        self.assertEqual(cases["person_no_name"].poster_kind, "person")
        self.assertEqual(cases["bot"].poster_kind, "bot")
        self.assertEqual(cases["channel"].poster_kind, "channel")
        self.assertEqual(cases["stand_in"].poster_kind, "anonymous")
        self.assertEqual(cases["none"].poster_kind, "")
        self.assertIsNone(cases["person"].poster_read_at)

    def test_running_it_again_changes_nothing_it_already_knows(self) -> None:
        post = self.stored(1, author_id=11, author_username="sara_home")
        fill_from_stored()
        HomePost.objects.filter(pk=post.pk).update(poster_username="the_author", poster_via="forward_author")
        fill_from_stored()
        post.refresh_from_db()
        self.assertEqual((post.poster_username, post.poster_via), ("the_author", "forward_author"))

    def test_a_listing_posted_in_the_app_is_left_alone(self) -> None:
        app = type(self.source).objects.create(username="app_ca", country="CA", default_currency="CAD")
        post = make_post(app, 1, text=CA_TEXT_NO_PHONE, posted_at=timezone.now(), author_id=11, author_username="owner_one")
        fill_from_stored()
        post.refresh_from_db()
        self.assertEqual(post.poster_kind, "")


class BackfillContactsTests(ApiCase):
    def with_contact(self, number: int, value: str, *, text: str = CA_TEXT_NO_PHONE) -> HomeListing:
        post = make_post(self.source, number, text=f"{text} {number}", posted_at=timezone.now(), source_url=f"https://t.me/RentToronto/{number}")
        apply_review(post, {**CA_ACCEPT, "contact_phone": "", "contact_telegram": ""})
        listing = HomeListing.objects.get(post=post)
        HomeListing.objects.filter(pk=listing.pk).update(contact_telegram=value)
        return listing

    def contact(self, listing: HomeListing) -> str:
        return HomeListing.objects.get(pk=listing.pk).contact_telegram

    def test_only_what_is_in_the_text_stays(self) -> None:
        kept = self.with_contact(1, "sara_home", text=f"{CA_TEXT_NO_PHONE} @sara_home")
        junk = self.with_contact(2, "unknown")
        name = self.with_contact(3, "Musa Gholame")
        copied = self.with_contact(4, POSTER)
        better = self.with_contact(5, "null", text=f"{CA_TEXT_NO_PHONE} @real_one1")
        counts = clean_contacts()
        self.assertEqual(counts, {"kept": 1, "cleared": 3, "replaced": 1})
        self.assertEqual([self.contact(item) for item in (kept, junk, name, copied, better)], ["sara_home", "", "", "", "real_one1"])
        self.assertEqual(clean_contacts(), {"kept": 2, "cleared": 0, "replaced": 0})

    def test_a_listing_posted_in_the_app_is_left_alone(self) -> None:
        app = type(self.source).objects.create(username="app_ca", country="CA", default_currency="CAD")
        post = make_post(app, 1, text=CA_TEXT_NO_PHONE, posted_at=timezone.now())
        apply_review(post, {**CA_ACCEPT, "contact_phone": "", "contact_telegram": "owner_handle"})
        clean_contacts()
        self.assertEqual(HomeListing.objects.get(post=post).contact_telegram, "owner_handle")


class FakeReader:
    def __init__(self, found: dict, *, flood_first: bool = False) -> None:
        self.found = found
        self.calls: list[list[int]] = []
        self.flood_first = flood_first

    def messages(self, _username: str, ids: list[int]) -> list:
        if self.flood_first:
            self.flood_first = False
            error = FloodWaitError(request=None, capture=30)
            raise error
        self.calls.append(list(ids))
        return [self.found.get(number) for number in ids]

    def chat_id(self, _username: str) -> int:
        return GROUP_ID


class BackfillRereadTests(ApiCase):
    def stored(self, number: int, **fields) -> HomePost:
        return make_post(self.source, number, text=f"{CA_TEXT_NO_PHONE} {number}", posted_at=timezone.now(), **fields)

    def run_reread(self, reader) -> dict:
        rows = HomePost.objects.filter(poster_read_at__isnull=True)
        return reread(HomePost, rows, "RentToronto", {"reader": reader}, delay=0, sleep=lambda _s: None)

    def test_the_forward_author_wins_and_a_gone_message_keeps_what_was_stored(self) -> None:
        forwarded = self.stored(1, author_id=66, author_username="the_forwarder", poster_kind="person", poster_username="the_forwarder", poster_user_id=66, poster_via="sender")
        gone = self.stored(2, author_id=67, author_username="stored_one", poster_kind="person", poster_username="stored_one", poster_user_id=67, poster_via="sender")
        unknown = self.stored(3, author_id=68, poster_kind="person", poster_username="stored_two", poster_user_id=68, poster_via="sender")
        missing = self.stored(4)
        reader = FakeReader(
            {
                1: message(person("the_forwarder", 66), 66, forward=SimpleNamespace(sender=person("the_author", 55), sender_id=55)),
                3: message(None, 68),
                4: message(person("was_missing", 69), 69),
            }
        )
        self.run_reread(reader)
        for post in (forwarded, gone, unknown, missing):
            post.refresh_from_db()
            self.assertIsNotNone(post.poster_read_at)
        self.assertEqual((forwarded.poster_username, forwarded.poster_via), ("the_author", "forward_author"))
        self.assertEqual(gone.poster_username, "stored_one")
        self.assertEqual(unknown.poster_username, "stored_two")
        self.assertEqual(missing.poster_username, "was_missing")

    def test_a_second_run_asks_telegram_for_nothing(self) -> None:
        self.stored(1)
        reader = FakeReader({1: message(person(), 4242)})
        self.run_reread(reader)
        self.run_reread(reader)
        self.assertEqual(len(reader.calls), 1)

    def test_batches_are_small_and_throttled(self) -> None:
        for number in range(1, 251):
            self.stored(number)
        reader = FakeReader({})
        pauses: list[float] = []
        rows = HomePost.objects.filter(poster_read_at__isnull=True)
        reread(HomePost, rows, "RentToronto", {"reader": reader}, delay=1.5, sleep=pauses.append)
        self.assertEqual([len(call) for call in reader.calls], [100, 100, 50])
        self.assertEqual(pauses, [1.5, 1.5, 1.5])

    def test_flood_wait_is_waited_out_in_full(self) -> None:
        reader = FakeReader({1: message(person(), 4242)}, flood_first=True)
        pauses: list[float] = []
        found = read_batch({"reader": reader}, "RentToronto", [1], sleep=pauses.append)
        self.assertEqual(pauses, [35])
        self.assertEqual(len(found), 1)

    def test_a_channel_post_is_no_contact_after_the_read(self) -> None:
        post = self.stored(1, author_id=MARKED_GROUP)
        apply_message(post, message(channel("my_board", GROUP_ID), MARKED_GROUP, post=True), chat_id=GROUP_ID)
        self.assertEqual(poster_contact(post), "")

    def test_it_makes_no_model_call(self) -> None:
        self.stored(1, author_id=11, author_username="sara_home")
        with patch("home.review.ask_model") as model:
            call_command("poster_backfill", "--stored", "--contacts", "--report", stdout=StringIO())
        model.assert_not_called()

    def test_the_report_says_why_a_listing_has_no_contact(self) -> None:
        for number, fields in enumerate(
            (
                {"poster_kind": "person", "poster_username": POSTER},
                {"poster_kind": "person"},
                {"poster_kind": "channel", "poster_username": "a_page"},
                {"poster_kind": "anonymous"},
                {"poster_kind": "bot", "poster_username": "rent_bot"},
                {},
                {"poster_kind": "person", "poster_username": "the_author", "poster_via": "forward_author"},
            ),
            start=1,
        ):
            post = self.stored(number, **fields)
            apply_review(post, {**CA_ACCEPT, "contact_phone": "", "contact_telegram": ""})
        HomeListing.objects.update(status="active", needs_review=False)
        rows = report()[self.source.kind]
        for name in ("from_poster", "none_no_username", "none_channel", "none_anonymous", "none_bot", "none_not_read", "from_poster_forward_author"):
            self.assertEqual(rows.get(name), 1, (name, rows))
