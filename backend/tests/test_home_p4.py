"""P4: posting (F18, F19), my listings (F20) and direct photo upload. Storage and Telegram are fake here; R2's CORS rule is not exercised."""

from __future__ import annotations

import io
import re
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.utils import timezone
from PIL import Image

from home.admin import HomeListingEditAdmin
from home.freshness import run_freshness
from home.models import (
    HomeAlert,
    HomeChannel,
    HomeListing,
    HomeListingEdit,
    HomeMedia,
    HomeNotification,
    HomePostKey,
    HomeSaved,
    HomeSettings,
    HomeUpload,
    ListingStatus,
    NotificationKind,
    StatusPost,
    UploadStatus,
)
from home.storage import R2Storage, StorageError, sigv4_authorization
from home.submit import MAX_PHOTO_BYTES, MAX_PHOTOS
from home.uploads import purge_abandoned
from tests.contract_shape import named
from tests.helpers import bearer_auth, make_user
from tests.test_home_api import ApiCase

TEXT = "آپارتمان یک خوابه مبله نزدیک مترو، قبوض داخل اجاره و از اول دسامبر آزاد است."
LISTINGS = "/api/v1/me/listings/"
LISTING = "/api/v1/me/listings/{id}/"
PHOTOS = "/api/v1/me/photos/"
PHOTO = "/api/v1/me/photos/{photo_id}/"


def jpeg(color: str = "red") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(out, "JPEG")
    return out.getvalue()


class FakeStorage:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.broken = False

    def _check(self) -> None:
        if self.broken:
            raise StorageError("down")

    def presign_put(self, key, content_type, size, *, expires=600):
        self._check()
        return f"https://storage.test/{key}?signed", {"Content-Type": content_type}

    def get(self, key, *, limit):
        self._check()
        data = self.files.get(key)
        return None if data is None else data[:limit]

    def put(self, key, data, content_type="image/jpeg", **_kwargs):
        self._check()
        self.files[key] = data

    def delete(self, key):
        self._check()
        self.files.pop(key, None)

    def url(self, key):
        return f"https://media.test/{key}"


class P4Case(ApiCase):
    def setUp(self) -> None:
        super().setUp()
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(posting_api_on=True, account_api_on=True)
        self.user.telegram_username = "sara_home"
        self.user.save()
        self.auth = bearer_auth(self.user)
        self.storage = FakeStorage()
        for target in ("home.uploads.get_storage", "home_api.views.get_storage", "home.public.get_storage"):
            patcher = patch(target, side_effect=lambda: self.storage, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("home.status_posts.apply")
        self.status_apply = patcher.start()
        self.addCleanup(patcher.stop)
        self.keys = iter(range(1, 1000))

    def form(self, **values) -> dict:
        return {"kind": "offer", "property_type": "apartment", "country": "CA", "city": "Toronto", "area": "North York", "price": 1850, "description": TEXT, **values}

    def send(self, body: dict, key: str | None = None):
        key = key if key is not None else f"key-{next(self.keys)}"
        headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
        return self.client.post(LISTINGS, body, content_type="application/json", **headers, **self.auth)

    def posted(self, **values) -> HomeListing:
        response = self.send(self.form(**values))
        self.assertIn(response.status_code, (200, 201), response.content)
        return HomeListing.objects.get(pk=response.json()["listing_id"])

    def row(self, listing: HomeListing) -> dict:
        return next(row for row in self.client.get(LISTINGS, **self.auth).json()["results"] if row["id"] == listing.pk)

    def upload(self, data: bytes | None = None) -> str:
        ticket = self.client.post(PHOTOS, {"content_type": "image/jpeg", "size": 1000}, content_type="application/json", **self.auth)
        self.assertEqual(ticket.status_code, 201, ticket.content)
        pk = ticket.json()["photo_id"]
        self.storage.files[HomeUpload.objects.get(pk=pk).raw_key] = jpeg() if data is None else data
        return pk

    def confirmed(self) -> str:
        pk = self.upload()
        self.assertEqual(self.client.post(f"{PHOTOS}{pk}/", content_type="application/json", **self.auth).status_code, 200)
        return pk

    def edit(self, listing: HomeListing, **values):
        listing.refresh_from_db()
        body = {**self.form(), "base_updated_at": listing.updated_at.isoformat(), **values}
        return self.client.patch(f"{LISTINGS}{listing.pk}/", body, content_type="application/json", **self.auth)

    def status(self, listing: HomeListing, action: str):
        return self.client.post(f"{LISTINGS}{listing.pk}/status/", {"action": action}, content_type="application/json", **self.auth)


class FlagTests(P4Case):
    def test_every_route_is_closed_while_the_flag_is_off(self) -> None:
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(posting_api_on=False)
        self.assert_error(self.client.get(LISTINGS, **self.auth), "feature_off", LISTINGS)
        self.assert_error(self.send(self.form()), "feature_off", LISTINGS)
        self.assert_error(self.client.patch(f"{LISTINGS}1/", {}, content_type="application/json", **self.auth), "feature_off", LISTING)
        self.assert_error(self.client.delete(f"{LISTINGS}1/", **self.auth), "feature_off", LISTING)
        self.assert_error(self.client.get(f"{LISTINGS}1/form/", **self.auth), "feature_off", f"{LISTING}form/")
        self.assert_error(self.status(HomeListing(pk=1), "close"), "feature_off", f"{LISTING}status/")
        self.assert_error(self.client.post(PHOTOS, {}, content_type="application/json", **self.auth), "feature_off", PHOTOS)

    def test_the_viewer_detail_stays_open(self) -> None:
        listing = self.listing(1)
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(posting_api_on=False)
        self.assertEqual(self.client.get(f"{LISTINGS}{listing.pk}/", **self.auth).status_code, 200)


class PostTests(P4Case):
    def test_goes_live_at_once(self) -> None:
        response = self.send(self.form())
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(named(body, "PostResult"), [])
        self.assertEqual((body["post_outcome"], body["photos_stored"]), ("live", 0))
        listing = HomeListing.objects.get(pk=body["listing_id"])
        self.assertEqual((listing.contact_telegram, listing.post.author_id), ("sara_home", self.user.telegram_user_id))
        self.assertEqual(HomeNotification.objects.get().kind, NotificationKind.LISTING_LIVE)

    def test_held_by_review(self) -> None:
        body = self.send(self.form(price=9_000_000)).json()
        self.assertEqual(body["post_outcome"], "held")
        self.assertEqual(HomeNotification.objects.get().kind, NotificationKind.LISTING_HELD)
        self.assertEqual(self.row(HomeListing.objects.get(pk=body["listing_id"]))["owner_state"], "in_review")

    def test_the_same_key_posts_once(self) -> None:
        first = self.send(self.form(), key="draft-1")
        again = self.send(self.form(), key="draft-1")
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(first.json(), again.json())
        self.assertEqual(HomeListing.objects.count(), 1)

    def test_a_key_is_one_persons(self) -> None:
        self.send(self.form(), key="draft-1")
        self.auth = bearer_auth(make_user(telegram_user_id=880020, telegram_username="other"))
        self.assertEqual(self.send(self.form(description=TEXT + " دیگر"), key="draft-1").status_code, 201)

    def test_no_key_is_refused(self) -> None:
        self.assert_error(self.send(self.form(), key=""), "bad_request", LISTINGS)

    def test_a_key_too_long_is_refused(self) -> None:
        self.assert_error(self.send(self.form(), key="x" * 65), "bad_request", LISTINGS)

    def test_each_field_problem_has_its_code(self) -> None:
        for values, field, code in (
            ({"description": "کوتاه"}, "description", "too_short"),
            ({"description": TEXT + " با شماره 416-555-0142"}, "description", "contact_in_text"),
            ({"description": TEXT + " آیدی @someone_here"}, "description", "contact_in_text"),
            ({"price": None}, "price", "required"),
            ({"country": "ZZ"}, "country", "invalid_choice"),
            ({"city": None}, "city", "required"),
            ({"property_type": None}, "property_type", "required"),
        ):
            with self.subTest(field=field, code=code):
                form = self.form(**values)
                form = {key: value for key, value in form.items() if value is not None}
                body = self.assert_error(self.send(form), "validation_failed", LISTINGS)
                self.assertEqual(body["fields"].get(field), [code], body)
        self.assertFalse(HomeListing.objects.exists())

    def test_no_username_needs_a_phone(self) -> None:
        self.auth = bearer_auth(make_user(telegram_user_id=880021))
        body = self.assert_error(self.send(self.form()), "validation_failed", LISTINGS)
        self.assertEqual(body["fields"], {"phone": ["no_contact"]})
        self.assertEqual(self.send(self.form(phone="416-555-0142")).status_code, 201)

    def test_a_price_past_the_limit_is_a_field_error(self) -> None:
        body = self.assert_error(self.send(self.form(price=99_000_000)), "validation_failed", LISTINGS)
        self.assertEqual(body["fields"], {"price": ["max_value"]})

    def test_german_and_sublet_fields_arrive(self) -> None:
        HomeChannel.objects.create(country="DE", city="Berlin", username="chamedoon_berlin", title="Berlin")
        with patch("home_api.views.countries", return_value=["CA", "DE"]):
            listing = self.posted(country="DE", city="Berlin", price=900, price_basis="warm", registration="yes", rental_mode="sublet", price_period="week", available_from="2026-12-01", available_to="2027-01-15", deposit=1800)
        self.assertEqual((listing.price_basis, listing.registration, listing.rental_mode, listing.price_period), ("warm", "yes", "sublet", "week"))
        self.assertEqual(listing.available_to.isoformat(), "2027-01-15")

    def test_post_cap(self) -> None:
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(post_daily_cap=1)
        self.posted()
        self.assert_error(self.send(self.form(description=TEXT + " دوم")), "post_cap", LISTINGS)

    def test_the_cap_does_not_block_a_retry(self) -> None:
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(post_daily_cap=1)
        self.send(self.form(), key="draft-1")
        self.assertEqual(self.send(self.form(), key="draft-1").status_code, 200)

    def test_own_duplicate(self) -> None:
        first = self.posted()
        body = self.assert_error(self.send(self.form()), "duplicate_listing", LISTINGS)
        self.assertEqual((body["duplicate_of"], body["duplicate_owner"]), (first.pk, "self"))

    def test_someone_elses_duplicate_carries_no_contact(self) -> None:
        first = self.posted(phone="416-555-0142")
        self.auth = bearer_auth(make_user(telegram_user_id=880022, telegram_username="other"))
        body = self.assert_error(self.send(self.form()), "duplicate_listing", LISTINGS)
        self.assertEqual((body["duplicate_of"], body["duplicate_owner"]), (first.pk, "other"))
        self.assertEqual(set(body), {"code", "detail", "duplicate_of", "duplicate_owner"})

    def test_force_new_posts_it_anyway_and_stays_apart(self) -> None:
        first = self.posted()
        second = self.posted(force_new=True)
        second.refresh_from_db()
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.status, ListingStatus.ACTIVE)

    def test_wanted_needs_a_budget_and_gets_its_alert(self) -> None:
        form = self.form(kind="wanted", property_type="room", wanted_types=["room", "studio"], household="couple", price=None)
        form.pop("price")
        body = self.assert_error(self.send(form), "validation_failed", LISTINGS)
        self.assertEqual(body["fields"], {"price": ["required"]})
        listing = self.posted(kind="wanted", property_type="room", wanted_types=["room", "studio"], household="couple", price=1600)
        self.assertEqual(listing.price_max, 1600)
        self.assertEqual(listing.flags["household"], "couple")
        alert = HomeAlert.objects.get(wanted_listing=listing)
        self.assertEqual(alert.property_types, ["room", "studio"])

    def test_a_wanted_post_keeps_no_photos(self) -> None:
        pk = self.confirmed()
        response = self.send(self.form(kind="wanted", property_type="room", price=1600, photo_ids=[pk]))
        self.assertEqual(response.json()["photos_stored"], 0)
        self.assertFalse(HomeMedia.objects.exists())

    def test_photos_join_in_order(self) -> None:
        first, second = self.confirmed(), self.confirmed()
        response = self.send(self.form(photo_ids=[second, first]))
        self.assertEqual(response.json()["photos_stored"], 2)
        listing = HomeListing.objects.get(pk=response.json()["listing_id"])
        keys = list(HomeMedia.objects.filter(post=listing.post).order_by("position").values_list("storage_key", flat=True))
        self.assertIn(second, keys[0])
        self.assertIn(first, keys[1])
        self.assertEqual(listing.post.photo_count, 2)
        self.assertEqual(self.row(listing)["card"]["photo_count"], 2)

    def test_an_unconfirmed_or_foreign_photo_stops_the_post(self) -> None:
        self.assert_error(self.send(self.form(photo_ids=[self.upload()])), "photo_gone", LISTINGS)
        self.assert_error(self.send(self.form(photo_ids=[str(uuid.uuid4())])), "photo_gone", LISTINGS)
        self.assertFalse(HomeListing.objects.exists())

    def test_a_lost_race_answers_the_first_result(self) -> None:
        first = self.posted()
        HomePostKey.objects.create(user=self.user, key="race", listing=first)
        with patch("home.owner_posts.HomePostKey.objects.filter") as found:
            found.return_value.select_related.return_value.first.return_value = None
            response = self.send(self.form(force_new=True), key="race")
        self.assertEqual((response.status_code, response.json()["listing_id"]), (200, first.pk))


class MineTests(P4Case):
    def test_none(self) -> None:
        body = self.client.get(LISTINGS, **self.auth).json()
        self.assertEqual(body, {"results": [], "total": 0, "next_cursor": None})

    def test_rows_in_the_contract_shape_and_only_own(self) -> None:
        listing = self.posted()
        self.listing(1)
        body = self.client.get(LISTINGS, **self.auth).json()
        self.assertEqual(named(body, "MineList"), [])
        self.assertEqual([row["id"] for row in body["results"]], [listing.pk])
        row = body["results"][0]
        self.assertEqual((row["owner_state"], row["can_edit"], row["can_renew"], row["edit_pending"], row["reject_code"]), ("live", True, False, False, None))

    def test_rejected_is_in_the_contract_and_never_answered(self) -> None:
        self.posted()
        self.assertNotEqual(self.client.get(LISTINGS, **self.auth).json()["results"][0]["owner_state"], "rejected")

    def test_owner_check_after_a_week(self) -> None:
        listing = self.posted()
        self.assertFalse(self.row(listing)["owner_check"])
        HomeListing.objects.filter(pk=listing.pk).update(first_seen=timezone.now() - timedelta(days=30))
        self.assertTrue(self.row(listing)["owner_check"])

    def test_someone_elses_listing_is_gone(self) -> None:
        listing = self.posted()
        self.auth = bearer_auth(make_user(telegram_user_id=880023, telegram_username="other"))
        self.assert_error(self.client.get(f"{LISTINGS}{listing.pk}/form/", **self.auth), "listing_gone", f"{LISTING}form/")
        self.assert_error(self.status(listing, "close"), "listing_gone", f"{LISTING}status/")
        self.assert_error(self.edit(listing), "listing_gone", LISTING)
        self.assertEqual(self.client.delete(f"{LISTINGS}{listing.pk}/", **self.auth).status_code, 204)
        listing.refresh_from_db()
        self.assertEqual(listing.status, ListingStatus.ACTIVE)

    def test_a_bad_cursor(self) -> None:
        self.assert_error(self.client.get(LISTINGS, {"cursor": "x"}, **self.auth), "validation_failed", LISTINGS)

    def test_form_holds_the_phone_and_photos(self) -> None:
        pk = self.confirmed()
        listing = self.posted(phone="416-555-0142", photo_ids=[pk])
        body = self.client.get(f"{LISTINGS}{listing.pk}/form/", **self.auth).json()
        self.assertEqual(named(body, "MineForm"), [])
        self.assertEqual((body["description"], body["photos"][0]["photo_id"]), (TEXT, pk))
        self.assertTrue(body["phone"])

    def test_photos_from_the_old_form_get_an_id(self) -> None:
        listing = self.posted()
        HomeMedia.objects.create(source=listing.post.source, post=listing.post, telegram_message_id=-5, position=0, status="stored", storage_key="home/ca/old-f.jpg", thumb_key="home/ca/old-t.jpg")
        first = self.client.get(f"{LISTINGS}{listing.pk}/form/", **self.auth).json()["photos"]
        second = self.client.get(f"{LISTINGS}{listing.pk}/form/", **self.auth).json()["photos"]
        self.assertEqual(first, second)
        self.assertEqual(HomeUpload.objects.count(), 1)


class EditTests(P4Case):
    def test_clean_edit_goes_live_at_once(self) -> None:
        listing = self.posted()
        body = self.edit(listing, price=1900, description=TEXT + " تازه").json()
        self.assertEqual(named(body, "PostResult"), [])
        self.assertEqual(body["post_outcome"], "live")
        listing.refresh_from_db()
        self.assertEqual((listing.price_amount, listing.post.text, listing.needs_review), (1900, TEXT + " تازه", False))

    def test_a_held_edit_keeps_the_old_version_up(self) -> None:
        listing = self.posted()
        body = self.edit(listing, price=9_000_000).json()
        self.assertEqual(body["post_outcome"], "held")
        listing.refresh_from_db()
        self.assertEqual((listing.price_amount, listing.needs_review), (1850, False))
        self.assertEqual(self.client.get(f"/api/v1/public/listings/{listing.pk}/").status_code, 200)
        self.assertTrue(self.row(listing)["edit_pending"])
        self.assertTrue(HomeListingEdit.objects.get(listing=listing).review_note)

    def test_an_admin_approval_makes_the_held_edit_live(self) -> None:
        listing = self.posted()
        self.edit(listing, price=9_000_000)
        HomeListingEditAdmin(HomeListingEdit, AdminSite()).approve_edits(None, HomeListingEdit.objects.all())
        listing.refresh_from_db()
        self.assertEqual(listing.price_amount, 9_000_000)
        self.assertFalse(HomeListingEdit.objects.exists())

    def test_a_retry_after_a_lost_response(self) -> None:
        listing = self.posted()
        old = listing.updated_at
        changed = {"price": 1900, "base_updated_at": old.isoformat()}
        self.assertEqual(self.edit(listing, **changed).status_code, 200)
        again = self.edit(listing, **changed)
        self.assertEqual((again.status_code, again.json()["post_outcome"]), (200, "live"))

    def test_a_retry_of_a_held_edit(self) -> None:
        listing = self.posted()
        changed = {"price": 9_000_000, "base_updated_at": listing.updated_at.isoformat()}
        self.assertEqual(self.edit(listing, **changed).json()["post_outcome"], "held")
        self.assertEqual(self.edit(listing, **changed).json()["post_outcome"], "held")
        self.assertEqual(HomeListingEdit.objects.count(), 1)

    def test_conflict_when_it_changed_since_the_form_opened(self) -> None:
        listing = self.posted()
        old = listing.updated_at - timedelta(minutes=5)
        self.assert_error(self.edit(listing, price=1900, base_updated_at=old.isoformat()), "edit_conflict", LISTING)

    def test_closed_while_editing(self) -> None:
        listing = self.posted()
        HomeListing.objects.filter(pk=listing.pk).update(status=ListingStatus.EXPIRED)
        self.assert_error(self.edit(listing, price=1900), "listing_closed", LISTING)

    def test_same_checks_as_a_post(self) -> None:
        listing = self.posted()
        body = self.assert_error(self.edit(listing, description="کوتاه"), "validation_failed", LISTING)
        self.assertEqual(body["fields"], {"description": ["too_short"]})

    def test_base_time_is_needed(self) -> None:
        listing = self.posted()
        response = self.client.patch(f"{LISTINGS}{listing.pk}/", self.form(price=1900), content_type="application/json", **self.auth)
        body = self.assert_error(response, "validation_failed", LISTING)
        self.assertIn("base_updated_at", body["fields"])

    def test_a_body_that_cannot_be_read(self) -> None:
        listing = self.posted()
        self.assert_error(self.client.patch(f"{LISTINGS}{listing.pk}/", "{", content_type="application/json", **self.auth), "bad_request", LISTING)

    def test_duplicate_of_another_listing(self) -> None:
        other = self.posted(description=TEXT + " دیگر", price=2222, area="Downtown")
        listing = self.posted()
        body = self.assert_error(self.edit(listing, description=TEXT + " دیگر"), "duplicate_listing", LISTING)
        self.assertEqual(body["duplicate_of"], other.pk)

    def test_a_missing_photo_stops_the_edit(self) -> None:
        listing = self.posted()
        self.assert_error(self.edit(listing, price=1900, photo_ids=[str(uuid.uuid4())]), "photo_gone", LISTING)

    def test_photos_are_replaced_and_old_files_leave(self) -> None:
        first = self.confirmed()
        listing = self.posted(photo_ids=[first])
        old_key = HomeUpload.objects.get(pk=first).full_key
        second = self.confirmed()
        self.assertEqual(self.edit(listing, price=1900, photo_ids=[second]).json()["photos_stored"], 1)
        keys = list(HomeMedia.objects.filter(post=listing.post).values_list("storage_key", flat=True))
        self.assertEqual(keys, [HomeUpload.objects.get(pk=second).full_key])
        self.assertNotIn(old_key, self.storage.files)

    def test_a_published_listing_edits_its_channel_post(self) -> None:
        listing = self.posted()
        HomeListing.objects.filter(pk=listing.pk).update(published_at=timezone.now())
        self.edit(listing, price=1900)
        self.status_apply.assert_called()
        self.assertEqual(self.status_apply.call_args.args[1], StatusPost.RESTORED)


class StatusTests(P4Case):
    def test_close_undo_and_retry(self) -> None:
        listing = self.posted()
        body = self.status(listing, "close").json()
        self.assertEqual(named(body, "MineRow"), [])
        self.assertEqual((body["owner_state"], body["closed_reason"], body["can_restore"]), ("closed", "owner", True))
        self.assertEqual(self.status(listing, "close").status_code, 200)
        self.assertEqual(self.status_apply.call_args_list[0].args[1], StatusPost.RENTED)
        body = self.status(listing, "restore").json()
        self.assertEqual((body["owner_state"], body["closed_reason"]), ("live", None))
        self.assertEqual(self.status(listing, "restore").status_code, 200)
        listing.refresh_from_db()
        self.assertIsNone(listing.rented_at)

    def test_closing_tells_who_saved_it(self) -> None:
        listing = self.posted()
        saver = make_user(telegram_user_id=880024)
        HomeSaved.objects.create(user=saver, listing=listing)
        self.status(listing, "close")
        self.assertTrue(HomeNotification.objects.filter(user=saver, kind=NotificationKind.SAVED_CLOSED).exists())

    def test_renew_only_when_expired(self) -> None:
        listing = self.posted()
        HomeListing.objects.filter(pk=listing.pk).update(status=ListingStatus.EXPIRED, last_seen=timezone.now() - timedelta(days=30))
        self.assertTrue(self.row(listing)["can_renew"])
        body = self.status(listing, "renew").json()
        self.assertEqual((body["owner_state"], body["can_renew"]), ("live", False))
        self.assertEqual(self.status(listing, "renew").status_code, 200)

    def test_what_does_not_apply(self) -> None:
        listing = self.posted()
        HomeListing.objects.filter(pk=listing.pk).update(status=ListingStatus.EXPIRED)
        self.assert_error(self.status(listing, "restore"), "status_not_allowed", f"{LISTING}status/")
        HomeListing.objects.filter(pk=listing.pk).update(status=ListingStatus.RENTED)
        self.assert_error(self.status(listing, "renew"), "status_not_allowed", f"{LISTING}status/")
        HomeListing.objects.filter(pk=listing.pk).update(status=ListingStatus.REPORTED)
        self.assert_error(self.status(listing, "close"), "status_not_allowed", f"{LISTING}status/")

    def test_a_bad_action(self) -> None:
        listing = self.posted()
        self.assert_error(self.status(listing, "explode"), "validation_failed", f"{LISTING}status/")


class DeleteTests(P4Case):
    def test_gone_for_everyone_and_safe_to_retry(self) -> None:
        listing = self.posted()
        HomeListing.objects.filter(pk=listing.pk).update(published_at=timezone.now())
        url = f"{LISTINGS}{listing.pk}/"
        self.assertEqual(self.client.delete(url, **self.auth).status_code, 204)
        self.assertEqual(self.client.delete(url, **self.auth).status_code, 204)
        self.assertEqual(self.status_apply.call_args.args[1], StatusPost.RENTED)
        self.assertEqual(self.status_apply.call_count, 1)
        self.assert_error(self.client.get(f"/api/v1/public/listings/{listing.pk}/"), "listing_gone", "/api/v1/public/listings/{id}/")
        self.assert_error(self.client.get(url, **self.auth), "listing_gone", LISTING)
        self.assertEqual(self.client.get(LISTINGS, **self.auth).json()["total"], 0)

    def test_contact_and_photos_leave_and_a_new_post_stays_apart(self) -> None:
        pk = self.confirmed()
        listing = self.posted(phone="416-555-0142", photo_ids=[pk])
        key = HomeUpload.objects.get(pk=pk).full_key
        self.client.delete(f"{LISTINGS}{listing.pk}/", **self.auth)
        listing.refresh_from_db()
        self.assertEqual((listing.contact_telegram, listing.contact_phone, listing.dedup_key), ("", "", ""))
        self.assertFalse(HomeMedia.objects.filter(post=listing.post).exists())
        self.assertNotIn(key, self.storage.files)
        fresh = self.posted(phone="416-555-0142")
        fresh.refresh_from_db()
        self.assertEqual((fresh.status, fresh.canonical_id), (ListingStatus.ACTIVE, None))

    def test_old_bot_buttons_no_longer_open_it(self) -> None:
        from home.botactions import _listing

        listing = self.posted()
        self.assertIsNotNone(_listing(str(listing.pk)))
        self.client.delete(f"{LISTINGS}{listing.pk}/", **self.auth)
        self.assertIsNone(_listing(str(listing.pk)))

    def test_a_wanted_alert_stops_with_its_post(self) -> None:
        listing = self.posted(kind="wanted", property_type="room", price=1600)
        self.assertTrue(HomeAlert.objects.get(wanted_listing=listing).active)
        self.client.delete(f"{LISTINGS}{listing.pk}/", **self.auth)
        self.assertFalse(HomeAlert.objects.get(wanted_listing=listing).active)


class UploadTests(P4Case):
    def test_ticket_in_the_contract_shape(self) -> None:
        response = self.client.post(PHOTOS, {"content_type": "image/jpeg", "size": 1000}, content_type="application/json", **self.auth)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(named(response.json(), "PhotoUploadTicket"), [])
        self.assertEqual(response.json()["headers"], {"Content-Type": "image/jpeg"})

    def test_not_an_image_type(self) -> None:
        response = self.client.post(PHOTOS, {"content_type": "application/pdf", "size": 10}, content_type="application/json", **self.auth)
        self.assert_error(response, "not_image", PHOTOS)

    def test_too_big_before_sending(self) -> None:
        response = self.client.post(PHOTOS, {"content_type": "image/jpeg", "size": MAX_PHOTO_BYTES + 1}, content_type="application/json", **self.auth)
        self.assert_error(response, "too_big", PHOTOS)

    def test_a_missing_size(self) -> None:
        self.assert_error(self.client.post(PHOTOS, {"content_type": "image/jpeg"}, content_type="application/json", **self.auth), "validation_failed", PHOTOS)

    def test_too_many(self) -> None:
        for _ in range(MAX_PHOTOS):
            self.upload()
        response = self.client.post(PHOTOS, {"content_type": "image/jpeg", "size": 10}, content_type="application/json", **self.auth)
        self.assert_error(response, "too_many", PHOTOS)

    def test_no_storage(self) -> None:
        with patch("home.uploads.get_storage", return_value=None):
            response = self.client.post(PHOTOS, {"content_type": "image/jpeg", "size": 10}, content_type="application/json", **self.auth)
        self.assert_error(response, "upload_unavailable", PHOTOS)

    def test_storage_cannot_sign(self) -> None:
        self.storage.broken = True
        response = self.client.post(PHOTOS, {"content_type": "image/jpeg", "size": 10}, content_type="application/json", **self.auth)
        self.assert_error(response, "upload_unavailable", PHOTOS)
        self.assertFalse(HomeUpload.objects.exists())

    def test_confirm_resizes_cleans_up_and_is_safe_to_retry(self) -> None:
        pk = self.upload()
        raw_key = HomeUpload.objects.get(pk=pk).raw_key
        url = f"{PHOTOS}{pk}/"
        first = self.client.post(url, content_type="application/json", **self.auth)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(named(first.json(), "UploadedPhoto"), [])
        self.assertNotIn(raw_key, self.storage.files)
        row = HomeUpload.objects.get(pk=pk)
        self.assertEqual(row.status, UploadStatus.READY)
        self.assertIn(row.full_key, self.storage.files)
        self.assertEqual(self.client.post(url, content_type="application/json", **self.auth).json(), first.json())

    def test_the_file_never_arrived(self) -> None:
        pk = self.upload()
        self.storage.files.clear()
        self.assert_error(self.client.post(f"{PHOTOS}{pk}/", content_type="application/json", **self.auth), "upload_failed", PHOTO)
        self.assertEqual(HomeUpload.objects.get(pk=pk).status, UploadStatus.PENDING)

    def test_the_bytes_are_not_an_image(self) -> None:
        pk = self.upload(b"this is not a picture")
        self.assert_error(self.client.post(f"{PHOTOS}{pk}/", content_type="application/json", **self.auth), "not_image", PHOTO)
        self.assertFalse(self.storage.files)

    def test_the_bytes_are_too_big(self) -> None:
        pk = self.upload(b"x" * (MAX_PHOTO_BYTES + 5))
        self.assert_error(self.client.post(f"{PHOTOS}{pk}/", content_type="application/json", **self.auth), "too_big", PHOTO)

    def test_storage_fails_at_confirm(self) -> None:
        pk = self.upload()
        self.storage.broken = True
        self.assert_error(self.client.post(f"{PHOTOS}{pk}/", content_type="application/json", **self.auth), "upload_unavailable", PHOTO)
        self.assertEqual(HomeUpload.objects.get(pk=pk).status, UploadStatus.PENDING)

    def test_someone_elses_or_expired_upload(self) -> None:
        pk = self.upload()
        other = bearer_auth(make_user(telegram_user_id=880025))
        self.assert_error(self.client.post(f"{PHOTOS}{pk}/", content_type="application/json", **other), "photo_gone", PHOTO)
        HomeUpload.objects.filter(pk=pk).update(created_at=timezone.now() - timedelta(hours=25))
        self.assert_error(self.client.post(f"{PHOTOS}{pk}/", content_type="application/json", **self.auth), "photo_gone", PHOTO)

    def test_delete_is_safe_to_retry_and_leaves_posted_photos(self) -> None:
        pk = self.confirmed()
        key = HomeUpload.objects.get(pk=pk).full_key
        self.assertEqual(self.client.delete(f"{PHOTOS}{pk}/", **self.auth).status_code, 204)
        self.assertNotIn(key, self.storage.files)
        self.assertEqual(self.client.delete(f"{PHOTOS}{pk}/", **self.auth).status_code, 204)
        posted = self.confirmed()
        self.posted(photo_ids=[posted])
        self.assertEqual(self.client.delete(f"{PHOTOS}{posted}/", **self.auth).status_code, 204)
        self.assertTrue(HomeUpload.objects.filter(pk=posted).exists())

    def test_abandoned_uploads_leave_after_a_day(self) -> None:
        fresh, old, posted = self.confirmed(), self.confirmed(), self.confirmed()
        self.posted(photo_ids=[posted])
        old_key = HomeUpload.objects.get(pk=old).full_key
        HomeUpload.objects.filter(pk__in=[old, posted]).update(created_at=timezone.now() - timedelta(hours=25))
        self.assertEqual(purge_abandoned(), 1)
        self.assertEqual(set(str(pk) for pk in HomeUpload.objects.values_list("pk", flat=True)), {fresh, posted})
        self.assertNotIn(old_key, self.storage.files)

    def test_the_job_step_purges(self) -> None:
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(freshness_enabled=True)
        old = self.confirmed()
        HomeUpload.objects.filter(pk=old).update(created_at=timezone.now() - timedelta(hours=25))
        self.assertEqual(run_freshness()["uploads_purged"], 1)


class R2SigningTests(P4Case):
    """No request leaves here: this proves the signature against the same signer the server's own calls use."""

    def test_a_signed_put_binds_type_and_length(self) -> None:
        storage = R2Storage(account_id="acc", access_key="AKID", secret_key="SECRET", bucket="media", public_base="https://media.test")
        url, headers = storage.presign_put("home/uploads/x/raw", "image/jpeg", 1234, expires=600)
        self.assertEqual(headers, {"Content-Type": "image/jpeg"})
        path, query = url.removeprefix("https://acc.r2.cloudflarestorage.com").split("?", 1)
        self.assertEqual(path, "/media/home/uploads/x/raw")
        self.assertIn("X-Amz-SignedHeaders=content-length%3Bcontent-type%3Bhost", query)
        self.assertIn("X-Amz-Expires=600", query)
        unsigned, signature = query.rsplit("&X-Amz-Signature=", 1)
        amz_date = re.search(r"X-Amz-Date=(\w+)", query).group(1)
        expected = sigv4_authorization(
            method="PUT",
            host="acc.r2.cloudflarestorage.com",
            path=path,
            query=unsigned,
            headers={"content-length": "1234", "content-type": "image/jpeg"},
            payload_hash="UNSIGNED-PAYLOAD",
            access_key="AKID",
            secret_key="SECRET",
            region="auto",
            service="s3",
            amz_date=amz_date,
        ).rsplit("Signature=", 1)[-1]
        self.assertEqual(signature, expected)
        self.assertNotIn("SECRET", url)

    def test_local_storage_takes_no_direct_uploads(self) -> None:
        from home.storage import LocalStorage

        with self.assertRaises(StorageError):
            LocalStorage("/tmp/none").presign_put("key", "image/jpeg", 1)
