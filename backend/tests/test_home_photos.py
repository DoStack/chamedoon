from __future__ import annotations

import io
import json
import random
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from ai.openrouter import ChatResult
from home.images import ImageError, hamming, process_image, same_photo, to_signed, to_unsigned
from home.ingest import ingest_source, link_media
from home.job import review_pending, run_home_job
from home.media import process_pending_media
from home.models import (
    HomeListing,
    HomeMedia,
    HomePost,
    HomeSource,
    ListingStatus,
    MediaStatus,
    PostStatus,
)
from home.review import COUNTRY_NOTES, apply_review, build_prompt, review_post, text_key
from home.storage import LocalStorage, R2Storage, get_storage, media_key, sigv4_authorization
from home.telegram import home_invite_sources, home_message_to_raw, pick_thumb
from home.vision import enrich_listing, merge_vision, vision_enabled
from tests.test_home import ACCEPT, FakeReader, make_post, make_source, raw

CA_TEXT = (
    "🏠 اتاق در بیسمنت دوخوابه، نورث یورک، یانگ و فینچ\n"
    "اجاره ۹۵۰ دلار، همه چیز شامل. فقط خانم. از اول نوامبر\n"
    "تماس: (647) 555-0142"
)
CA_ACCEPT = {
    "accept": True,
    "kind": "offer",
    "property_type": "room",
    "country": "CA",
    "region": "ON",
    "city": "North York",
    "area": "Yonge & Finch",
    "price_amount": 950,
    "price_currency": "",
    "price_period": "month",
    "bills_included": "yes",
    "available_from": "2026-11-01",
    "roommate_gender": "female",
    "contact_phone": "647-555-0142",
    "lister_type": "owner",
    "summary_fa": "اتاق در بیسمنت نورث یورک، ۹۵۰ دلار با قبوض. تماس 6475550142",
    "flags": {"first_last": True, "damage_deposit": True},
    "confidence": {"overall": 0.9},
}


CA_TEXT_NO_PHONE = CA_TEXT.rsplit(chr(10), 1)[0]


def ca_source(**overrides) -> HomeSource:
    values = {
        "username": "RentToronto",
        "country": "CA",
        "default_city": "Toronto",
        "default_region": "ON",
        "default_currency": "CAD",
        "kind": "group",
    }
    values.update(overrides)
    return HomeSource.objects.create(**values)


def pattern_image(seed: int, size=(400, 300)) -> bytes:
    from PIL import Image

    rng = random.Random(seed)
    small = Image.new("RGB", (16, 12))
    small.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(16 * 12)])
    big = small.resize(size, Image.BICUBIC)
    buffer = io.BytesIO()
    big.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def recompressed(data: bytes) -> bytes:
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    image = image.resize((int(image.width * 0.7), int(image.height * 0.7)))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=45)
    return buffer.getvalue()


class CanadaReviewTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = ca_source()

    def test_canadian_post_is_normalised(self) -> None:
        post = make_post(self.source, text=CA_TEXT)
        apply_review(post, dict(CA_ACCEPT))
        listing = HomeListing.objects.get(post=post)
        self.assertEqual((listing.city, listing.region, listing.price_currency), ("North York", "ON", "CAD"))
        self.assertEqual(listing.price_amount, Decimal("950.00"))
        self.assertEqual(listing.contact_phone, "+16475550142")
        self.assertEqual(listing.roommate_gender, "female")
        self.assertEqual(listing.bills_included, "yes")
        self.assertNotIn("6475550142", listing.summary_fa)
        self.assertFalse(listing.needs_review)

    def test_ontario_damage_deposit_is_labelled_but_not_hidden(self) -> None:
        post = make_post(self.source, text=CA_TEXT)
        apply_review(post, dict(CA_ACCEPT))
        listing = HomeListing.objects.get(post=post)
        self.assertIn("Ontario", listing.flags["deposit_warning"])
        self.assertEqual(listing.status, ListingStatus.ACTIVE)
        self.assertFalse(listing.needs_review)

    def test_quebec_deposit_is_labelled(self) -> None:
        quebec = ca_source(username="Iranianmontrealhome", default_city="Montreal", default_region="QC")
        post = make_post(quebec, text=CA_TEXT.replace("نورث یورک", "مونترال"))
        apply_review(post, {**CA_ACCEPT, "region": "QC", "city": "Montreal", "flags": {}, "deposit_amount": 500})
        self.assertIn("Quebec", HomeListing.objects.get(post=post).flags["deposit_warning"])

    def test_square_feet_become_square_metres(self) -> None:
        post = make_post(self.source, text=CA_TEXT)
        apply_review(post, {**CA_ACCEPT, "size_m2": None, "size_sqft": 1000})
        self.assertEqual(HomeListing.objects.get(post=post).size_m2, Decimal("92.9"))

    def test_phone_is_read_from_the_text_when_the_model_misses_it(self) -> None:
        post = make_post(self.source, text=CA_TEXT)
        apply_review(post, {**CA_ACCEPT, "contact_phone": ""})
        self.assertEqual(HomeListing.objects.get(post=post).contact_phone, "+16475550142")

    def test_a_price_is_not_mistaken_for_a_phone(self) -> None:
        post = make_post(self.source, text="اتاق در تورنتو، اجاره 1450 دلار، از 2026-11-01 تا 2027-04-30، عالی " * 2)
        apply_review(post, {**CA_ACCEPT, "contact_phone": ""})
        self.assertEqual(HomeListing.objects.get(post=post).contact_phone, "")

    def test_bare_amount_defaults_to_cad_and_region_to_the_source(self) -> None:
        post = make_post(self.source, text=CA_TEXT)
        apply_review(post, {**CA_ACCEPT, "region": "", "price_currency": ""})
        listing = HomeListing.objects.get(post=post)
        self.assertEqual((listing.price_currency, listing.region), ("CAD", "ON"))

    def test_prompt_carries_the_country_notes_and_defaults(self) -> None:
        post = make_post(self.source, text=CA_TEXT)
        prompt = build_prompt(post)
        self.assertIn("Source default currency: CAD", prompt)
        self.assertIn("Source default region: ON", prompt)
        self.assertIn(COUNTRY_NOTES["CA"], prompt)
        self.assertIn("بیسمنت", prompt)

    def test_a_sender_who_keeps_posting_is_an_agent(self) -> None:
        when = timezone.now() - timedelta(days=1)
        for number in range(1, 17):
            make_post(
                self.source,
                number,
                text=f"آگهی شماره {number} برای اجاره اتاق در تورنتو با تمام امکانات و قیمت مناسب " * 2,
                author_id=777,
                posted_at=when,
            )
        post = HomePost.objects.get(telegram_message_id=1)
        apply_review(post, dict(CA_ACCEPT))
        listing = HomeListing.objects.get(post=post)
        self.assertEqual(listing.lister_type, "agent")
        self.assertTrue(listing.flags["repeat_poster"])

    def test_one_off_sender_stays_an_owner(self) -> None:
        post = make_post(self.source, text=CA_TEXT, author_id=555)
        apply_review(post, dict(CA_ACCEPT))
        self.assertEqual(HomeListing.objects.get(post=post).lister_type, "owner")


class RepostShortcutTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = ca_source()
        self.other = ca_source(username="Toronto_Rental")

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_an_exact_repost_costs_no_model_call_and_extends_the_original(self, call, _enabled) -> None:
        call.return_value = ChatResult(ok=True, text=json.dumps(CA_ACCEPT), model="m")
        first = make_post(self.source, 1, text=CA_TEXT, posted_at=timezone.now() - timedelta(days=3))
        review_post(first)
        again = make_post(self.other, 9, text=CA_TEXT, posted_at=timezone.now() - timedelta(days=1))
        HomeMedia.objects.create(source=self.other, post=again, telegram_message_id=9)
        outcome = review_post(again)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(outcome.reject_reason, "repost")
        again.refresh_from_db()
        self.assertEqual((again.status, again.reject_reason), (PostStatus.REJECTED, "repost"))
        self.assertEqual(again.media.count(), 0)
        listing = HomeListing.objects.get(post=first)
        self.assertEqual(listing.last_seen, again.posted_at)

    @patch("home.review.llm_enabled", return_value=True)
    @patch("home.review.ask_model")
    def test_the_newer_repost_never_becomes_the_original(self, call, _enabled) -> None:
        call.return_value = ChatResult(ok=True, text=json.dumps(CA_ACCEPT), model="m")
        old = make_post(self.source, 1, text=CA_TEXT, posted_at=timezone.now() - timedelta(days=5))
        new = make_post(self.source, 2, text=CA_TEXT, posted_at=timezone.now() - timedelta(days=1))
        result = review_pending(limit=5)
        self.assertEqual((result["accepted"], result["reposts"]), (1, 1))
        self.assertEqual(call.call_count, 1)
        self.assertEqual(HomePost.objects.get(pk=old.pk).status, PostStatus.ACCEPTED)
        self.assertEqual(HomePost.objects.get(pk=new.pk).reject_reason, "repost")
        listing = HomeListing.objects.get(post=old)
        self.assertEqual((listing.first_seen, listing.last_seen), (old.posted_at, new.posted_at))

    def test_other_countries_do_not_share_reposts(self) -> None:
        de = make_source(username="berlin_khane")
        first = make_post(self.source, 1, text=CA_TEXT, posted_at=timezone.now() - timedelta(days=3))
        german = make_post(de, 1, text=CA_TEXT, posted_at=timezone.now())
        from home.review import repost_original

        self.assertIsNone(repost_original(german))
        self.assertIsNone(repost_original(first))


class AlbumIngestTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = ca_source()

    def photo(self, message_id: int, album: int | None, text: str = "") -> dict:
        row = raw(message_id, 0.5, text=text or "x")
        row.update({"text": text, "has_photo": True, "grouped_id": album, "author_id": 42})
        return row

    def test_album_parts_attach_to_the_captioned_message(self) -> None:
        rows = [
            self.photo(13, 900),
            self.photo(12, 900),
            self.photo(11, 900),
            self.photo(10, 900, text="اتاق در بیسمنت، ۹۵۰ دلار، تماس 6475550142 " * 2),
        ]
        ingest_source(FakeReader(rows, page_size=10), self.source, days=30, stop_at=None)
        post = HomePost.objects.get()
        self.assertEqual(post.telegram_message_id, 10)
        self.assertEqual((post.photo_count, post.has_photo, post.author_id, post.grouped_id), (4, True, 42, 900))
        self.assertEqual(list(post.media.values_list("telegram_message_id", flat=True)), [10, 11, 12, 13])
        self.assertEqual(list(post.media.values_list("position", flat=True)), [0, 1, 2, 3])

    def test_caption_on_a_later_page_still_links(self) -> None:
        rows = [self.photo(13, 900), self.photo(12, 900), self.photo(11, 900), self.photo(10, 900, text="اتاق ۹۵۰ دلار " * 5)]
        ingest_source(FakeReader(rows, page_size=2), self.source, days=30, stop_at=None)
        self.assertEqual(HomePost.objects.get().media.count(), 4)

    def test_single_photo_with_its_caption(self) -> None:
        ingest_source(FakeReader([self.photo(5, None, text="اتاق ۹۵۰ دلار " * 5)]), self.source, days=30, stop_at=None)
        post = HomePost.objects.get()
        self.assertEqual(post.media.count(), 1)

    def test_photos_without_any_text_stay_unlinked(self) -> None:
        ingest_source(FakeReader([self.photo(5, 77), self.photo(4, 77)]), self.source, days=30, stop_at=None)
        self.assertFalse(HomePost.objects.exists())
        self.assertEqual(HomeMedia.objects.filter(post__isnull=True).count(), 2)
        self.assertEqual(link_media(self.source), 0)

    def test_a_source_can_opt_out_of_photos(self) -> None:
        self.source.fetch_photos = False
        self.source.save()
        ingest_source(FakeReader([self.photo(5, None, text="اتاق ۹۵۰ دلار " * 5)]), self.source, days=30, stop_at=None)
        self.assertEqual(HomeMedia.objects.count(), 0)
        self.assertEqual(HomePost.objects.count(), 1)

    def test_rerunning_adds_nothing(self) -> None:
        rows = [self.photo(11, 900), self.photo(10, 900, text="اتاق ۹۵۰ دلار " * 5)]
        ingest_source(FakeReader(rows), self.source, days=30, stop_at=None)
        ingest_source(FakeReader(rows), self.source, days=30, stop_at=None)
        self.assertEqual((HomePost.objects.count(), HomeMedia.objects.count()), (1, 2))


class TelegramShapeTests(TestCase):
    def message(self, **overrides):
        values = {
            "id": 7,
            "date": datetime(2026, 10, 10, 8, tzinfo=dt_timezone.utc),
            "message": "",
            "action": None,
            "views": 3,
            "photo": object(),
            "grouped_id": 555,
            "sender_id": 99,
            "sender": SimpleNamespace(username="seller", first_name="Sara", last_name="B"),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_a_captionless_album_part_is_kept(self) -> None:
        row = home_message_to_raw("RentToronto", self.message(), public_username="RentToronto")
        self.assertIsNotNone(row)
        self.assertEqual((row["text"], row["has_photo"], row["grouped_id"], row["author_id"]), ("", True, 555, 99))
        self.assertEqual(row["source_url"], "https://t.me/RentToronto/7")

    def test_nothing_to_keep_returns_none(self) -> None:
        self.assertIsNone(home_message_to_raw("x", self.message(photo=None)))
        self.assertIsNone(home_message_to_raw("x", self.message(action=object())))

    def test_private_link_and_channel_sender_are_handled(self) -> None:
        row = home_message_to_raw(
            "khane_montreal",
            self.message(sender=SimpleNamespace(username="khane_montreal", first_name="", last_name="")),
            chat_id=123,
        )
        self.assertEqual(row["source_url"], "https://t.me/c/123/7")
        self.assertEqual(row["author_username"], "")

    def test_pick_thumb_takes_the_largest_size_up_to_1280(self) -> None:
        sizes = [
            SimpleNamespace(type="m", w=320, h=240),
            SimpleNamespace(type="y", w=1280, h=960),
            SimpleNamespace(type="w", w=2560, h=1920),
            SimpleNamespace(type="i"),
        ]
        self.assertEqual(pick_thumb(SimpleNamespace(sizes=sizes)), "y")
        self.assertEqual(pick_thumb(SimpleNamespace(sizes=[SimpleNamespace(type="w", w=2560, h=1920)])), "w")
        self.assertIsNone(pick_thumb(SimpleNamespace(sizes=[])))

    @override_settings(HOME_SOURCE_INVITES="khane_montreal:abc123, bad, other:https://t.me/+xyz789")
    def test_invites_are_read_from_the_environment_not_the_repo(self) -> None:
        self.assertEqual(home_invite_sources(), {"khane_montreal": "abc123", "other": "xyz789"})


class ImageTests(TestCase):
    def test_process_makes_a_small_full_image_a_thumbnail_and_a_hash(self) -> None:
        image = process_image(pattern_image(1, size=(3000, 2000)))
        self.assertEqual(max(image.width, image.height), 1280)
        self.assertLess(len(image.thumb), len(image.full))
        self.assertEqual(len(image.digest), 40)

    def test_a_recompressed_resized_copy_has_a_near_identical_hash(self) -> None:
        original = process_image(pattern_image(2))
        copy = process_image(recompressed(pattern_image(2)))
        self.assertLessEqual(hamming(original.phash, copy.phash), 6)
        self.assertTrue(same_photo(original.phash, copy.phash))

    def test_different_photos_are_far_apart(self) -> None:
        self.assertGreater(hamming(process_image(pattern_image(3)).phash, process_image(pattern_image(4)).phash), 12)

    def test_unreadable_and_tiny_images_are_refused(self) -> None:
        with self.assertRaises(ImageError):
            process_image(b"not an image")
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buffer, format="JPEG")
        with self.assertRaises(ImageError):
            process_image(buffer.getvalue())

    def test_signed_hash_round_trips(self) -> None:
        for value in (0, 1, (1 << 63) - 1, 1 << 63, (1 << 64) - 1):
            self.assertEqual(to_unsigned(to_signed(value)), value)
        self.assertFalse(same_photo(None, 5))


class StorageTests(TestCase):
    def test_signature_matches_the_botocore_reference(self) -> None:
        payload = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        header = sigv4_authorization(
            method="PUT",
            host="acct123.r2.cloudflarestorage.com",
            path="/chamedoon-media/home/ca/00001/1234-abcdef0123-f.jpg",
            query="",
            headers={"content-type": "image/jpeg", "x-amz-content-sha256": payload, "x-amz-date": "20261013T093000Z"},
            payload_hash=payload,
            access_key="AKIDEXAMPLE",
            secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
            region="auto",
            service="s3",
            amz_date="20261013T093000Z",
        )
        self.assertIn("Credential=AKIDEXAMPLE/20261013/auto/s3/aws4_request", header)
        self.assertIn("SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date", header)
        self.assertTrue(header.endswith("Signature=" + header.rsplit("Signature=", 1)[1]))
        self.assertEqual(len(header.rsplit("Signature=", 1)[1]), 64)

    def test_local_storage_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = LocalStorage(root, "https://cdn.test/")
            store.put("home/ca/1.jpg", b"abc")
            self.assertEqual((store.root / "home/ca/1.jpg").read_bytes(), b"abc")
            self.assertEqual(store.url("home/ca/1.jpg"), "https://cdn.test/home/ca/1.jpg")

    def test_r2_put_signs_and_sends_the_bytes(self) -> None:
        store = R2Storage(account_id="acct", access_key="AK", secret_key="SK", bucket="b", public_base="https://m.test/")
        captured = {}

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["headers"] = {k.lower(): v for k, v in request.header_items()}
            captured["data"] = request.data
            return Response()

        with patch("home.storage.urllib.request.urlopen", fake_urlopen):
            store.put("home/ca/00000/1-abc-f.jpg", b"jpegbytes")
        self.assertEqual(captured["url"], "https://acct.r2.cloudflarestorage.com/b/home/ca/00000/1-abc-f.jpg")
        self.assertEqual(captured["data"], b"jpegbytes")
        self.assertTrue(captured["headers"]["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AK/"))
        self.assertEqual(store.url("home/ca/x.jpg"), "https://m.test/home/ca/x.jpg")

    def test_r2_errors_become_storage_errors(self) -> None:
        import urllib.error

        from home.storage import StorageError

        store = R2Storage(account_id="a", access_key="AK", secret_key="SK", bucket="b", public_base="https://m.test")
        error = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b"denied"))
        with patch("home.storage.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(StorageError):
                store.put("k.jpg", b"x")

    def test_backend_choice(self) -> None:
        self.assertIsNone(get_storage())
        with override_settings(HOME_MEDIA_BACKEND="r2", R2_ACCOUNT_ID="a"):
            self.assertIsNone(get_storage())
        with override_settings(
            HOME_MEDIA_BACKEND="r2",
            R2_ACCOUNT_ID="a",
            R2_ACCESS_KEY_ID="k",
            R2_SECRET_ACCESS_KEY="s",
            HOME_MEDIA_BUCKET="b",
            HOME_MEDIA_PUBLIC_BASE="https://m.test",
        ):
            self.assertIsInstance(get_storage(), R2Storage)
        with override_settings(HOME_MEDIA_BACKEND="local", HOME_MEDIA_LOCAL_DIR="/tmp/x"):
            self.assertIsInstance(get_storage(), LocalStorage)

    def test_keys_are_stable_and_split_by_country(self) -> None:
        self.assertEqual(media_key("CA", 1234, "abcdef0123456789"), "home/ca/00001/1234-abcdef0123-f.jpg")
        self.assertTrue(media_key("DE", 5, "ab", thumb=True).endswith("-t.jpg"))


class PhotoReader:
    """Stand-in for HomeHistoryReader: bytes by (username, message id)."""

    def __init__(self, photos: dict[int, bytes | None]) -> None:
        self.photos = photos
        self.calls: list[int] = []

    def download_photo(self, username: str, message_id: int):
        self.calls.append(message_id)
        return self.photos.get(message_id)


def accepted_listing(source: HomeSource, message_id: int, text: str, **data) -> HomeListing:
    post = make_post(source, message_id, text=text, posted_at=timezone.now() - timedelta(days=1, minutes=-message_id))
    apply_review(post, {**CA_ACCEPT, **data})
    return HomeListing.objects.get(post=post)


@override_settings(HOME_MEDIA_BACKEND="local")
class MediaPipelineTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = ca_source()
        self.dir = tempfile.TemporaryDirectory()
        self.override = override_settings(HOME_MEDIA_BACKEND="local", HOME_MEDIA_LOCAL_DIR=self.dir.name, HOME_MEDIA_LOCAL_BASE="https://m.test")
        self.override.enable()

    def tearDown(self) -> None:
        self.override.disable()
        self.dir.cleanup()

    def add_media(self, listing: HomeListing, ids: list[int]) -> None:
        for position, message_id in enumerate(ids):
            HomeMedia.objects.create(
                source=self.source, post=listing.post, telegram_message_id=message_id, position=position
            )

    def test_stores_full_and_thumb_and_the_hash(self) -> None:
        listing = accepted_listing(self.source, 1, CA_TEXT)
        self.add_media(listing, [101, 102])
        reader = PhotoReader({101: pattern_image(1), 102: pattern_image(2)})
        result = process_pending_media(reader, limit=10)
        self.assertEqual((result["stored"], result["failed"], result["remaining"]), (2, 0, 0))
        media = list(listing.post.media.all())
        self.assertTrue(all(m.status == MediaStatus.STORED and m.phash is not None for m in media))
        stored = LocalStorage(self.dir.name)
        for item in media:
            self.assertTrue((stored.root / item.storage_key).exists())
            self.assertTrue((stored.root / item.thumb_key).exists())

    def test_nothing_is_fetched_when_storage_is_missing(self) -> None:
        with override_settings(HOME_MEDIA_BACKEND=""):
            listing = accepted_listing(self.source, 1, CA_TEXT)
            self.add_media(listing, [101])
            reader = PhotoReader({101: pattern_image(1)})
            result = process_pending_media(reader, limit=10)
            self.assertIn("not configured", result["skipped"])
            self.assertEqual(reader.calls, [])

    def test_only_accepted_active_listings_are_fetched(self) -> None:
        rejected = make_post(self.source, 5, text=CA_TEXT + " x")
        apply_review(rejected, {"accept": False, "reject_reason": "ad"})
        HomeMedia.objects.create(source=self.source, post=rejected, telegram_message_id=500)
        waiting = make_post(self.source, 6, text=CA_TEXT + " y")
        HomeMedia.objects.create(source=self.source, post=waiting, telegram_message_id=600)
        reader = PhotoReader({500: pattern_image(1), 600: pattern_image(2)})
        process_pending_media(reader, limit=10)
        self.assertEqual(reader.calls, [])

    def test_a_missing_or_broken_photo_fails_without_stopping_the_batch(self) -> None:
        listing = accepted_listing(self.source, 1, CA_TEXT)
        self.add_media(listing, [101, 102, 103])
        reader = PhotoReader({101: None, 102: b"junk", 103: pattern_image(3)})
        result = process_pending_media(reader, limit=10)
        self.assertEqual((result["stored"], result["failed"]), (1, 2))
        statuses = sorted(listing.post.media.values_list("status", flat=True))
        self.assertEqual(statuses, ["failed", "failed", "stored"])

    def test_an_album_is_capped_at_ten_photos(self) -> None:
        listing = accepted_listing(self.source, 1, CA_TEXT)
        self.add_media(listing, list(range(200, 212)))
        reader = PhotoReader({n: pattern_image(n) for n in range(200, 212)})
        process_pending_media(reader, limit=50)
        self.assertEqual(listing.post.media.filter(status=MediaStatus.STORED).count(), 10)
        self.assertEqual(listing.post.media.filter(status=MediaStatus.SKIPPED).count(), 2)

    def test_the_same_photo_at_a_close_price_merges_the_listings(self) -> None:
        first = accepted_listing(self.source, 1, CA_TEXT_NO_PHONE, contact_phone="", price_amount=950)
        second = accepted_listing(self.source, 2, CA_TEXT_NO_PHONE.replace("۹۵۰", "۹۸۰") + " دوباره", contact_phone="", price_amount=980)
        self.add_media(first, [101])
        self.add_media(second, [201])
        shared = pattern_image(9)
        reader = PhotoReader({101: shared, 201: recompressed(shared)})
        result = process_pending_media(reader, limit=10)
        self.assertEqual(result["merged_by_photo"], 1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.status, second.status), (ListingStatus.ACTIVE, ListingStatus.DUPLICATE))
        self.assertEqual(second.canonical, first)

    def test_the_same_photo_at_a_very_different_price_is_only_flagged(self) -> None:
        first = accepted_listing(self.source, 1, CA_TEXT_NO_PHONE, contact_phone="", price_amount=950, contact_telegram="")
        second = accepted_listing(
            self.source, 2, CA_TEXT_NO_PHONE.replace("۹۵۰", "۱۸۰۰") + " دوباره", contact_phone="", price_amount=1800, contact_telegram=""
        )
        self.add_media(first, [101])
        self.add_media(second, [201])
        shared = pattern_image(9)
        process_pending_media(PhotoReader({101: shared, 201: recompressed(shared)}), limit=10)
        second.refresh_from_db()
        self.assertEqual(second.status, ListingStatus.ACTIVE)
        self.assertEqual(second.flags.get("shared_photo_with"), first.pk)

    def test_the_same_photo_with_the_same_phone_merges_despite_the_price(self) -> None:
        first = accepted_listing(self.source, 1, CA_TEXT, price_amount=950, contact_phone="647-555-0142")
        second = accepted_listing(
            self.source, 2, CA_TEXT + " تازه", price_amount=1800, contact_phone="647-555-0142", property_type="apartment"
        )
        self.add_media(first, [101])
        self.add_media(second, [201])
        shared = pattern_image(11)
        process_pending_media(PhotoReader({101: shared, 201: recompressed(shared)}), limit=10)
        second.refresh_from_db()
        self.assertEqual(second.status, ListingStatus.DUPLICATE)

    def test_a_listing_in_another_city_is_never_merged_by_photo(self) -> None:
        first = accepted_listing(self.source, 1, CA_TEXT, city="Toronto")
        second = accepted_listing(self.source, 2, CA_TEXT + " montreal", city="Montreal", region="QC")
        self.add_media(first, [101])
        self.add_media(second, [201])
        shared = pattern_image(12)
        process_pending_media(PhotoReader({101: shared, 201: shared}), limit=10)
        second.refresh_from_db()
        self.assertEqual(second.status, ListingStatus.ACTIVE)

    def test_job_skips_the_media_step_without_opening_telegram(self) -> None:
        with override_settings(HOME_MEDIA_BACKEND=""):
            with patch("home.job.HomeHistoryReader") as reader_class:
                result = run_home_job(step="media")
        reader_class.assert_not_called()
        self.assertIn("not configured", result["media"]["skipped"])


@override_settings(HOME_VISION_ENABLED=True, HOME_VISION_MODEL="google/gemini-test", HOME_MEDIA_BACKEND="local")
class VisionTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = ca_source()
        self.dir = tempfile.TemporaryDirectory()
        self.override = override_settings(HOME_MEDIA_LOCAL_DIR=self.dir.name, HOME_MEDIA_LOCAL_BASE="https://m.test")
        self.override.enable()
        self.listing = accepted_listing(
            self.source, 1, "اتاق برای اجاره در تورنتو، عکس را ببینید " * 2, price_amount=None, summary_fa="اتاق در تورنتو."
        )
        HomeMedia.objects.create(
            source=self.source,
            post=self.listing.post,
            telegram_message_id=11,
            status=MediaStatus.STORED,
            storage_key="home/ca/00000/11-aa-f.jpg",
        )

    def tearDown(self) -> None:
        self.override.disable()
        self.dir.cleanup()

    def test_merge_fills_gaps_and_lets_the_text_win(self) -> None:
        merged = merge_vision(
            {"price_amount": None, "city": "Toronto", "flags": {"wg": True}},
            {"price_amount": 1200, "city": "Montreal", "size_m2": 18, "flags": {"parking": True}},
        )
        self.assertEqual((merged["price_amount"], merged["city"], merged["size_m2"]), (1200, "Toronto", 18))
        self.assertEqual(merged["flags"], {"parking": True, "wg": True})

    @patch("home.vision.llm_enabled", return_value=True)
    @patch("home.vision.chat")
    def test_photos_fill_the_missing_price(self, chat, _enabled) -> None:
        chat.return_value = ChatResult(ok=True, text=json.dumps({**CA_ACCEPT, "price_amount": 1200}), model="v")
        self.assertTrue(vision_enabled())
        self.assertEqual(enrich_listing(self.listing), "ok")
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.price_amount, Decimal("1200.00"))
        self.assertEqual(self.listing.flags["vision"], "ok")
        self.assertIsNotNone(self.listing.vision_at)
        messages = chat.call_args.args[0]
        images = [part for part in messages[1]["content"] if part["type"] == "image_url"]
        self.assertEqual(images[0]["image_url"]["url"], "https://m.test/home/ca/00000/11-aa-f.jpg")
        self.assertEqual(chat.call_args.kwargs["model"], "google/gemini-test")

    @patch("home.vision.chat")
    def test_a_failed_call_is_recorded_and_not_retried_forever(self, chat) -> None:
        chat.return_value = ChatResult(ok=False, error="no vision", model="v")
        self.assertEqual(enrich_listing(self.listing), "no vision")
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.flags["vision"], "failed")
        self.assertIsNotNone(self.listing.vision_at)

    @patch("home.vision.chat")
    def test_non_housing_photos_flag_the_listing_for_review(self, chat) -> None:
        chat.return_value = ChatResult(ok=True, text='{"accept": false, "reject_reason": "ad"}', model="v")
        self.assertEqual(enrich_listing(self.listing), "rejected")
        self.listing.refresh_from_db()
        self.assertTrue(self.listing.needs_review)

    def test_only_offers_with_gaps_are_candidates(self) -> None:
        from home.vision import vision_candidates

        self.assertEqual([item.pk for item in vision_candidates()], [self.listing.pk])
        self.listing.price_amount = Decimal("900")
        self.listing.save()
        self.assertEqual(list(vision_candidates()), [])
        wanted = accepted_listing(self.source, 2, CA_TEXT_NO_PHONE + " wanted", kind="wanted", price_amount=None)
        HomeMedia.objects.create(source=self.source, post=wanted.post, telegram_message_id=22, status=MediaStatus.STORED, storage_key="k")
        self.assertEqual(list(vision_candidates()), [])

    def test_vision_stays_off_without_a_model(self) -> None:
        with override_settings(HOME_VISION_MODEL=""):
            self.assertFalse(vision_enabled())
            from home.vision import enrich_pending

            self.assertIn("not configured", enrich_pending()["skipped"])


class CanadaSeedTests(TestCase):
    def test_canada_sources_are_seeded_with_the_right_defaults(self) -> None:
        sources = {s.username: s for s in HomeSource.objects.filter(country="CA")}
        for name in ("RentToronto", "Toronto_Rental", "Toronto_Apartment_House"):
            self.assertEqual((sources[name].default_city, sources[name].default_region, sources[name].enabled), ("Toronto", "ON", True))
        for name in ("Iranianmontrealhome", "buyhouseinMT", "khane_montreal"):
            self.assertEqual((sources[name].default_region, sources[name].enabled), ("QC", True))
        self.assertTrue(sources["CanadaHome"].is_agency)
        self.assertTrue(sources["EjareToronto"].enabled)
        self.assertFalse(sources["CANADARENT"].enabled)
        self.assertEqual(sources["RentToronto"].default_currency, "CAD")

    def test_the_private_invite_hash_is_not_in_the_repository(self) -> None:
        from pathlib import Path

        import home

        root = Path(home.__file__).parent
        text = "".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
        self.assertNotIn("Mg6H3q4878U2OTAx", text)
