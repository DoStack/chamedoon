"""`qa_user_ids`: one person gets every switched-off feature on production, and nobody else sees any change."""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from home import conf, freshness, inbox
from home.models import HomeListing, HomeNotification, HomePost, HomeSettings, ListingStatus, NotificationKind
from tests.helpers import TEST_SECRET, bearer_auth, make_user
from tests.test_home_api import ApiCase
from tests.test_telegram_auth import TEST_BOT_TOKEN, build_init_data

QA_ID = 7700100
OTHER_ID = 7700200

# Every route behind a switch, with a method that reaches the gate before anything else.
GATED = (
    ("get", "/api/v1/me/account/"),
    ("get", "/api/v1/me/notifications/"),
    ("get", "/api/v1/me/alerts/"),
    ("get", "/api/v1/me/alerts/1/"),
    ("get", "/api/v1/me/alerts/1/listings/"),
    ("get", "/api/v1/me/listings/"),
    ("get", "/api/v1/me/listings/1/form/"),
    ("post", "/api/v1/me/photos/"),
    ("post", "/api/v1/me/posts/parse/"),
    ("post", "/api/v1/me/search/parse/"),
    ("get", "/api/v1/me/search/recent/"),
)


def switches_off() -> None:
    HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(
        alerts_api_on=False, account_api_on=False, posting_api_on=False, smart_posting_on=False, ai_search_enabled=False, alerts_enabled=False
    )


def set_ids(text: str) -> None:
    HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(qa_user_ids=text)
    cache.clear()


@override_settings(JWT_SECRET=TEST_SECRET, HOME_BOT_TOKEN=TEST_BOT_TOKEN, TELEGRAM_BOT_TOKEN=TEST_BOT_TOKEN)
class QaAllowlistTests(ApiCase):
    def setUp(self) -> None:
        super().setUp()
        switches_off()
        self.qa = make_user(telegram_user_id=QA_ID)
        self.other = make_user(telegram_user_id=OTHER_ID)

    def meta(self) -> dict:
        cache.clear()
        return self.client.get("/api/v1/public/meta/").json()

    def exchange(self, telegram_id: int) -> dict:
        init_data = build_init_data(telegram_user_id=telegram_id, first_name="T")
        response = self.client.post("/api/v1/auth/exchange/", json.dumps({"init_data": init_data}), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def call(self, user, method: str, url: str):
        return getattr(self.client, method)(url, *([{}] if method == "get" else ["{}"]), **({} if method == "get" else {"content_type": "application/json"}), **bearer_auth(user))

    def test_the_list_reads_like_the_other_allowlists(self) -> None:
        for text, listed in ((f"{QA_ID}", True), (f"1, {QA_ID}", True), (f"1\n{QA_ID}\n2", True), (f"{QA_ID}0", False), (f"{str(QA_ID)[:-1]}", False), ("", False), (" , ", False)):
            with self.subTest(text=text):
                set_ids(text)
                self.assertEqual(conf.qa_user(QA_ID), listed)
        set_ids(f"{QA_ID}")
        self.assertFalse(conf.qa_user(None))
        self.assertFalse(conf.qa_user(0))

    def test_someone_on_it_gets_every_gate_open(self) -> None:
        set_ids(f"{QA_ID}")
        for method, url in GATED:
            with self.subTest(url=url):
                response = self.call(self.qa, method, url)
                self.assertIn(response.status_code, {200, 400, 404}, response.content)
                self.assertNotEqual(response.json().get("code"), "feature_off")

    def test_their_exchange_says_so(self) -> None:
        set_ids(f"{QA_ID}")
        body = self.exchange(QA_ID)
        self.assertEqual(body["qa"], {"features": {"alerts": True, "account": True, "posting": True, "smart_posting": True, "map": True}, "text_search_on": True, "alerts_on": True})

    def test_nobody_else_sees_any_change(self) -> None:
        set_ids("")
        meta_before, exchange_before = self.meta(), self.exchange(OTHER_ID)
        closed_before = {url: self.call(self.other, method, url).json()["code"] for method, url in GATED}
        self.assertEqual(set(closed_before.values()), {"feature_off"})

        set_ids(f"{QA_ID}")
        meta_after, exchange_after = self.meta(), self.exchange(OTHER_ID)
        closed_after = {url: self.call(self.other, method, url).json()["code"] for method, url in GATED}

        self.assertEqual(meta_after, meta_before)
        self.assertNotIn("qa", exchange_after)
        for body in (exchange_before, exchange_after):
            body.pop("access_token")
        self.assertEqual(exchange_after, exchange_before)
        self.assertEqual(closed_after, closed_before)

    def test_public_meta_is_the_same_for_the_person_on_it(self) -> None:
        set_ids(f"{QA_ID}")
        cache.clear()
        anonymous = self.client.get("/api/v1/public/meta/")
        signed_in = self.client.get("/api/v1/public/meta/", **bearer_auth(self.qa))
        self.assertEqual(signed_in.json(), anonymous.json())
        self.assertEqual(anonymous.json()["features"], {"alerts": False, "account": False, "posting": False, "smart_posting": False})
        self.assertFalse(anonymous.json()["text_search_on"])
        self.assertFalse(anonymous.json()["alerts_on"])

    def test_turning_a_switch_on_for_everyone_still_works(self) -> None:
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(account_api_on=True)
        self.assertEqual(self.call(self.other, "get", "/api/v1/me/account/").status_code, 200)

    def test_the_inbox_writes_only_for_someone_on_it(self) -> None:
        set_ids(f"{QA_ID}")
        inbox.write(self.qa.pk, NotificationKind.LISTING_LIVE, title="a")
        inbox.write(self.other.pk, NotificationKind.LISTING_LIVE, title="b")
        self.assertEqual(list(HomeNotification.objects.values_list("user_id", flat=True)), [self.qa.pk])

    def owned_listing(self, number: int, owner) -> HomeListing:
        listing = self.listing(number, price_amount=1000 + number * 100, area=f"Area {number}", contact_telegram=f"qa_owner{number}")
        HomePost.objects.filter(pk=listing.post_id).update(author_id=owner.telegram_user_id)
        listing.refresh_from_db()
        return listing

    def test_expiry_tells_only_someone_on_it(self) -> None:
        set_ids(f"{QA_ID}")
        old = timezone.now() - timedelta(days=400)
        mine, theirs = self.owned_listing(1, self.qa), self.owned_listing(2, self.other)
        HomeListing.objects.filter(pk__in=[mine.pk, theirs.pk]).update(last_seen=old)
        freshness.expire_stale()
        self.assertEqual(HomeListing.objects.get(pk=theirs.pk).status, ListingStatus.EXPIRED)
        self.assertEqual(list(HomeNotification.objects.values_list("user_id", "kind", "listing_id")), [(self.qa.pk, NotificationKind.LISTING_EXPIRED, mine.pk)])

    def test_a_list_of_junk_is_nobody_for_the_inbox_too(self) -> None:
        set_ids(" , abc ")
        self.assertFalse(conf.inbox_may_write())
        set_ids(f"{QA_ID}")
        self.assertTrue(conf.inbox_may_write())

    def test_a_failed_lookup_does_not_stop_rented_for_someone_off_the_list(self) -> None:
        set_ids(f"{QA_ID}")
        listing = self.owned_listing(1, self.other)
        with patch("home.inbox.owner_id", side_effect=RuntimeError("boom")), self.assertLogs("home.inbox", level="ERROR"):
            self.assertTrue(freshness.set_rented(listing))
        self.assertEqual(HomeListing.objects.get(pk=listing.pk).status, ListingStatus.RENTED)

    def test_a_failed_user_lookup_does_not_reach_the_writer(self) -> None:
        set_ids(f"{QA_ID}")
        with patch("home.inbox.User.objects.filter", side_effect=RuntimeError("boom")), self.assertLogs("home.inbox", level="ERROR"):
            inbox.write(self.other.pk, NotificationKind.LISTING_LIVE, title="a")
        self.assertFalse(HomeNotification.objects.exists())

    def test_the_new_app_opens_for_them_with_its_flag_off(self) -> None:
        HomeSettings.objects.filter(pk=HomeSettings.load().pk).update(miniapp_v2=False, miniapp_v2_allowlist="")
        set_ids(f"{QA_ID}")
        self.assertTrue(conf.miniapp_v2_for(QA_ID))
        self.assertFalse(conf.miniapp_v2_for(OTHER_ID))
        self.assertEqual(self.client.post("/api/v1/auth/exchange/", json.dumps({"init_data": build_init_data(telegram_user_id=OTHER_ID, first_name="T")}), content_type="application/json").json()["code"], "miniapp_off")
