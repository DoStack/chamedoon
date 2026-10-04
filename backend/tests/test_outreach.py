from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from item_requests.models import RequestStatus
from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from market.models import MarketPost, MarketRole
from matching.contact import SYNTHETIC_TELEGRAM_USER_ID
from matching.models import Match
from miniapp.auth import SESSION_USER_KEY, startapp_path
from notifications.messages import connected_text
from outreach.messages import outreach_text
from outreach.models import OutreachMessage, OutreachOptOut, OutreachState, OutreachStatus
from outreach.persian import TEHRAN_TZ, fa_date, fa_date_range, fa_kg
from outreach.services import (
    PEER_FLOOD,
    OutreachConflict,
    build_outreach_queue,
    claim_next_message,
    open_outreach,
    record_opt_out,
    record_reply,
    record_result,
)
from users.services import upsert_telegram_user
from users.telegram import TelegramIdentity
from tests.helpers import TEST_SECRET, make_user
from tests.test_requests import DEMAND_PAYLOAD, SUPPLY_PAYLOAD

OUTREACH_SECRET = "outreach-secret-for-tests"
SHADOW_ID = SYNTHETIC_TELEGRAM_USER_ID + 4242


def _tehran_noon():
    return timezone.now().astimezone(TEHRAN_TZ).replace(hour=12, minute=0, second=0, microsecond=0)


class PersianFormatTests(SimpleTestCase):
    def test_jalali_dates_and_digits(self) -> None:
        self.assertEqual(fa_date(date(2026, 9, 23)), "۱ مهر")
        self.assertEqual(fa_date(date(2027, 9, 10)), "۱۹ شهریور")
        self.assertEqual(fa_date_range(date(2027, 9, 1), date(2027, 9, 15)), "۱۰ تا ۲۴ شهریور")
        self.assertEqual(fa_date_range(date(2027, 9, 20), date(2027, 9, 25)), "۲۹ شهریور تا ۳ مهر")
        self.assertEqual(fa_kg(Decimal("5.00")), "۵")
        self.assertEqual(fa_kg(Decimal("2.50")), "۲٫۵")


@override_settings(
    SECRET_KEY=TEST_SECRET,
    TELEGRAM_BOT_TOKEN="",
    TELEGRAM_BOT_USERNAME="CB_koolbarbot",
    TELEGRAM_MINI_APP_URL="",
    TELEGRAM_CHANNEL_USERNAME="",
    MARKET_CHANNEL_USERNAMES="koolbar_international,koolbarcanada",
    OUTREACH_ENABLED=True,
    OUTREACH_SENDING_ENABLED=True,
    OUTREACH_SERVICE_SECRET=OUTREACH_SECRET,
    OUTREACH_DAILY_LIMIT=10,
    OUTREACH_MIN_SCORE=80,
    OUTREACH_MAX_POST_AGE_DAYS=5,
    OUTREACH_COOLDOWN_DAYS=14,
    OUTREACH_SEND_START_HOUR=10,
    OUTREACH_SEND_END_HOUR=21,
)
class OutreachTestCase(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.shadow = make_user(
            telegram_user_id=SHADOW_ID,
            first_name="مریم",
            telegram_username="maryam_send",
        )
        self.traveler = make_user(telegram_user_id=95002, first_name="Ali", telegram_username="ali_carry")
        self.demand = self._imported_demand(self.shadow, message_id=55)
        self.supply = create_item_request(self.traveler, SUPPLY_PAYLOAD)
        self.match = Match.objects.get(demand_request=self.demand)

    def _imported_demand(self, owner, *, message_id: int, posted_days_ago: int = 1, **overrides):
        demand = create_item_request(
            owner,
            {**DEMAND_PAYLOAD, **overrides},
            imported=True,
            source_url=f"https://t.me/koolbarcanada/{message_id}",
            sync_channel=False,
        )
        MarketPost.objects.create(
            channel_username="koolbarcanada",
            telegram_message_id=message_id,
            posted_at=timezone.now() - timedelta(days=posted_days_ago),
            text="بار دارم از تهران به تورنتو",
            role=MarketRole.DEMAND,
            author_username=owner.telegram_username or "",
            item_request=demand,
        )
        return demand

    def _queued(self) -> OutreachMessage:
        build_outreach_queue()
        return OutreachMessage.objects.get(demand_request=self.demand)

    def _auth(self) -> dict:
        return {"HTTP_X_OUTREACH_SECRET": OUTREACH_SECRET}


class OutreachBuildTests(OutreachTestCase):
    def test_queues_persian_message_for_imported_demander(self) -> None:
        result = build_outreach_queue()

        self.assertEqual(result.as_dict()["created"], 1)
        message = OutreachMessage.objects.get()
        self.assertEqual(message.status, OutreachStatus.QUEUED)
        self.assertEqual(message.recipient_username, "maryam_send")
        self.assertEqual(list(message.matches.all()), [self.match])
        self.assertTrue(message.text.startswith("سلام مریم"))
        self.assertIn("دیروز", message.text)
        self.assertIn("از تهران ببره تورنتو", message.text)
        self.assertIn("یه مسافر", message.text)
        self.assertIn("پیدا کردیم", message.text)
        self.assertNotIn("داریم", message.text)
        self.assertIn("۱۹ شهریور", message.text)
        link = f"https://t.me/CB_koolbarbot?startapp=o_{message.token}"
        self.assertIn(f"👈 [دیدن مشخصات مسافر]({link})", message.text)
        self.assertRegex(message.token, r"^[0-9a-f]{16}$")
        self.assertTrue(message.text.endswith(("بپرسید 🙏", "در خدمتم 🙂")))
        self.assertNotIn("لغو", message.text)
        for hidden in ("koolbarcanada", "کانال", "کیلو", "ali_carry", "🇮🇷", "•"):
            self.assertNotIn(hidden, message.text)
        self.assertIsNotNone(OutreachState.load().last_built_at)

    def test_wording_varies_but_always_names_chamedoon(self) -> None:
        texts = {outreach_text(self.demand, [self.match], f"token-{n:04d}") for n in range(40)}
        self.assertGreater(len(texts), 4)
        for text in texts:
            self.assertIn("چمدون", text)
            self.assertNotIn("لغو", text)
            self.assertNotIn("koolbarcanada", text)
        self.assertEqual(
            outreach_text(self.demand, [self.match], "same-token"),
            outreach_text(self.demand, [self.match], "same-token"),
        )

    def test_several_travelers_mention_the_earliest(self) -> None:
        other = make_user(telegram_user_id=95005, first_name="Nima", telegram_username="nima_fly")
        create_item_request(other, {**SUPPLY_PAYLOAD, "flight_date": "2027-09-08"})
        matches = list(Match.objects.filter(demand_request=self.demand))
        self.assertEqual(len(matches), 2)
        text = outreach_text(self.demand, matches, "token-many")
        self.assertIn("دو تا مسافر", text)
        self.assertIn("پیدا کردیم", text)
        self.assertIn("👈 [دیدن مسافرها](https://t.me/CB_koolbarbot?startapp=o_token-many)", text)
        self.assertIn("اولیش ۱۷ شهریور", text)

    def test_one_traveler_with_two_trips_counts_once(self) -> None:
        create_item_request(self.traveler, {**SUPPLY_PAYLOAD, "flight_date": "2027-09-08"})
        matches = list(Match.objects.filter(demand_request=self.demand))
        self.assertEqual(len(matches), 2)
        text = outreach_text(self.demand, matches, "token-trips")
        self.assertIn("یه مسافر", text)
        self.assertNotIn("دو تا", text)
        self.assertIn("۱۷ شهریور", text)

    def test_queued_text_is_refreshed_when_claimed(self) -> None:
        queued = self._queued()
        OutreachMessage.objects.filter(pk=queued.pk).update(text="old wording")
        message, _reason = claim_next_message(_tehran_noon())
        self.assertNotEqual(message.text, "old wording")
        self.assertTrue(message.text.startswith("سلام مریم"))

    def test_building_twice_does_not_duplicate(self) -> None:
        build_outreach_queue()
        again = build_outreach_queue()
        self.assertEqual(again.as_dict()["created"], 0)
        self.assertEqual(OutreachMessage.objects.count(), 1)

    def test_dry_run_saves_nothing(self) -> None:
        result = build_outreach_queue(dry_run=True)
        self.assertEqual(len(result.created), 1)
        self.assertFalse(OutreachMessage.objects.exists())

    def test_registered_demanders_are_left_to_the_bot(self) -> None:
        OutreachMessage.objects.all().delete()
        self.demand.delete()
        registered = make_user(telegram_user_id=95003, first_name="Sara", telegram_username="sara_real")
        create_item_request(registered, DEMAND_PAYLOAD)
        self.assertEqual(build_outreach_queue().as_dict()["created"], 0)

    def test_official_handle_and_bots_are_not_people(self) -> None:
        self.shadow.telegram_username = "koolbar"
        self.shadow.save()
        self.assertEqual(build_outreach_queue().skipped, {"not_a_person": 1})
        self.shadow.telegram_username = "cargo_helper_bot"
        self.shadow.save()
        self.assertEqual(build_outreach_queue().skipped, {"not_a_person": 1})

    def test_opted_out_person_is_skipped(self) -> None:
        OutreachOptOut.objects.create(telegram_username="Maryam_Send")
        self.assertEqual(build_outreach_queue().skipped, {"opted_out": 1})

    def test_recently_contacted_person_is_skipped(self) -> None:
        other = self._imported_demand(self.shadow, message_id=56, destination_city="vancouver")
        OutreachMessage.objects.create(
            recipient=self.shadow,
            recipient_username="maryam_send",
            demand_request=other,
            text="x",
            token="previous-token",
            status=OutreachStatus.SENT,
            sent_at=timezone.now() - timedelta(days=3),
        )
        self.assertEqual(build_outreach_queue().skipped, {"cooldown": 1})

    def test_old_posts_are_not_contacted(self) -> None:
        MarketPost.objects.filter(item_request=self.demand).update(posted_at=timezone.now() - timedelta(days=9))
        self.assertEqual(build_outreach_queue().as_dict()["created"], 0)

    @override_settings(OUTREACH_MIN_SCORE=95)
    def test_weak_matches_are_not_announced(self) -> None:
        self.assertEqual(self.match.score, Decimal("94.00"))
        self.assertEqual(build_outreach_queue().as_dict()["created"], 0)


class OutreachSendingTests(OutreachTestCase):
    def test_claim_marks_message_sending(self) -> None:
        queued = self._queued()
        message, reason = claim_next_message(_tehran_noon())
        self.assertEqual(reason, "")
        self.assertEqual(message.pk, queued.pk)
        message.refresh_from_db()
        self.assertEqual(message.status, OutreachStatus.SENDING)
        self.assertEqual(message.attempts, 1)

    def test_gates(self) -> None:
        self._queued()
        noon = _tehran_noon()
        with self.settings(OUTREACH_SENDING_ENABLED=False):
            self.assertEqual(claim_next_message(noon), (None, "disabled"))
        self.assertEqual(claim_next_message(noon.replace(hour=3))[1], "outside_window")
        self.assertEqual(claim_next_message(noon.replace(hour=21))[1], "outside_window")
        state = OutreachState.load()
        state.stopped = True
        state.save()
        self.assertEqual(claim_next_message(noon)[1], "stopped")
        state.stopped = False
        state.paused_until = noon + timedelta(hours=1)
        state.save()
        self.assertEqual(claim_next_message(noon)[1], "paused")

    @override_settings(OUTREACH_DAILY_LIMIT=1)
    def test_daily_limit_counts_attempts(self) -> None:
        self._queued()
        noon = _tehran_noon()
        message, _reason = claim_next_message(noon)
        record_result(message, outcome="failed", error_code="PRIVACY_PREMIUM_REQUIRED", now=noon)
        self.assertEqual(claim_next_message(noon + timedelta(minutes=5))[1], "daily_limit")

    def test_closed_demand_is_skipped_at_claim_time(self) -> None:
        queued = self._queued()
        self.demand.status = RequestStatus.CANCELLED
        self.demand.save()
        self.assertEqual(claim_next_message(_tehran_noon()), (None, "empty"))
        queued.refresh_from_db()
        self.assertEqual(queued.status, OutreachStatus.SKIPPED)
        self.assertEqual(queued.error_code, "demand_closed")

    def test_abandoned_claim_is_never_resent(self) -> None:
        self._queued()
        noon = _tehran_noon()
        message, _reason = claim_next_message(noon)
        self.assertEqual(claim_next_message(noon + timedelta(minutes=20)), (None, "empty"))
        message.refresh_from_db()
        self.assertEqual(message.status, OutreachStatus.FAILED)
        self.assertEqual(message.error_code, "lease_expired")

    def test_results(self) -> None:
        self._queued()
        noon = _tehran_noon()
        message, _reason = claim_next_message(noon)
        record_result(message, outcome="sent", recipient_telegram_id=777, telegram_message_id=12, now=noon)
        message.refresh_from_db()
        self.assertEqual(message.status, OutreachStatus.SENT)
        self.assertEqual(message.recipient_telegram_id, 777)
        self.assertEqual(message.sent_at, noon)
        with self.assertRaises(OutreachConflict):
            record_result(message, outcome="sent")

    def test_peer_flood_requeues_and_pauses_for_two_days(self) -> None:
        self._queued()
        noon = _tehran_noon()
        message, _reason = claim_next_message(noon)
        record_result(message, outcome="retry", error_code=PEER_FLOOD, now=noon)
        message.refresh_from_db()
        self.assertEqual(message.status, OutreachStatus.QUEUED)
        state = OutreachState.load()
        self.assertEqual(state.paused_until, noon + timedelta(hours=48))
        self.assertEqual(state.pause_reason, PEER_FLOOD)

    def test_flood_wait_pauses_for_the_requested_time(self) -> None:
        self._queued()
        noon = _tehran_noon()
        message, _reason = claim_next_message(noon)
        record_result(message, outcome="retry", error_code="FLOOD_WAIT", retry_after_seconds=600, now=noon)
        self.assertEqual(OutreachState.load().paused_until, noon + timedelta(seconds=600))

    def test_opt_out_and_reply(self) -> None:
        queued = self._queued()
        record_opt_out(username="@Maryam_Send")
        queued.refresh_from_db()
        self.assertEqual(queued.status, OutreachStatus.SKIPPED)
        self.assertEqual(queued.error_code, "opted_out")
        self.assertIsNone(record_opt_out())

        queued.status = OutreachStatus.SENT
        queued.recipient_telegram_id = 777
        queued.sent_at = timezone.now()
        queued.save()
        self.assertEqual(record_reply(telegram_user_id=777).pk, queued.pk)
        queued.refresh_from_db()
        self.assertIsNotNone(queued.replied_at)


class OutreachApiTests(OutreachTestCase):
    def test_requires_outreach_secret(self) -> None:
        self.assertEqual(self.client.post("/api/outreach/claim/").status_code, 403)
        wrong = {"HTTP_X_OUTREACH_SECRET": "nope"}
        self.assertEqual(self.client.post("/api/outreach/claim/", **wrong).status_code, 403)

    @override_settings(OUTREACH_SEND_START_HOUR=0, OUTREACH_SEND_END_HOUR=24)
    def test_worker_round_trip(self) -> None:
        queued = self._queued()
        preview = self.client.get("/api/outreach/preview/", **self._auth())
        self.assertEqual(preview.status_code, 200)
        first = preview.json()["messages"][0]
        self.assertEqual(first["username"], "maryam_send")
        self.assertEqual(first["skip"], "")
        self.assertEqual(first["source_url"], "https://t.me/koolbarcanada/55")
        self.assertEqual(first["travelers"][0]["username"], "ali_carry")
        self.assertEqual(first["travelers"][0]["request_id"], self.supply.pk)
        self.assertEqual(first["travelers"][0]["flight_date"], "2027-09-10")
        self.assertEqual(preview.json()["queued"], 1)

        claimed = self.client.post("/api/outreach/claim/", **self._auth()).json()
        self.assertEqual(claimed["message"]["id"], queued.pk)
        self.assertIn("سلام مریم", claimed["message"]["text"])

        url = f"/api/outreach/{queued.pk}/result/"
        done = self.client.post(url, {"outcome": "sent", "recipient_telegram_id": 777}, format="json", **self._auth())
        self.assertEqual(done.json(), {"id": queued.pk, "status": OutreachStatus.SENT})
        again = self.client.post(url, {"outcome": "sent"}, format="json", **self._auth())
        self.assertEqual(again.status_code, 409)

        reply = self.client.post("/api/outreach/reply/", {"telegram_user_id": 777}, format="json", **self._auth())
        self.assertEqual(reply.json()["message_id"], queued.pk)
        self.assertEqual(self.client.post("/api/outreach/heartbeat/", **self._auth()).status_code, 200)
        self.assertIsNotNone(OutreachState.load().last_heartbeat_at)

    def test_opt_out_endpoint(self) -> None:
        self._queued()
        response = self.client.post(
            "/api/outreach/opt-out/",
            {"username": "maryam_send", "telegram_user_id": 777},
            format="json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(OutreachOptOut.objects.filter(telegram_user_id=777).exists())
        empty = self.client.post("/api/outreach/opt-out/", {}, format="json", **self._auth())
        self.assertEqual(empty.status_code, 400)

    def test_secret_hash_fallback_when_no_secret_is_set(self) -> None:
        digest = hashlib.sha256(b"worker-only-secret").hexdigest()
        with self.settings(OUTREACH_SERVICE_SECRET="", OUTREACH_SERVICE_SECRET_SHA256=digest):
            ok = self.client.get("/api/outreach/preview/", HTTP_X_OUTREACH_SECRET="worker-only-secret")
            self.assertEqual(ok.status_code, 200)
            wrong = self.client.get("/api/outreach/preview/", HTTP_X_OUTREACH_SECRET=digest)
            self.assertEqual(wrong.status_code, 403)
        with self.settings(OUTREACH_SERVICE_SECRET="", OUTREACH_SERVICE_SECRET_SHA256=""):
            self.assertEqual(self.client.get("/api/outreach/preview/", **self._auth()).status_code, 403)

    def test_worker_can_build_the_queue(self) -> None:
        response = self.client.post("/api/outreach/build/", **self._auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["created"], 1)
        with self.settings(OUTREACH_ENABLED=False):
            self.assertEqual(self.client.post("/api/outreach/build/", **self._auth()).status_code, 503)

    @override_settings(CRON_SECRET="cron-secret")
    def test_build_cron(self) -> None:
        auth = {"HTTP_AUTHORIZATION": "Bearer cron-secret"}
        with self.settings(OUTREACH_ENABLED=False):
            self.assertEqual(self.client.get("/api/cron/outreach-build/", **auth).status_code, 503)
        response = self.client.get("/api/cron/outreach-build/", **auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["created"], 1)


class ShadowClaimTests(OutreachTestCase):
    def _identity(self, telegram_user_id: int, username: str) -> TelegramIdentity:
        return TelegramIdentity(
            telegram_user_id=telegram_user_id,
            telegram_username=username,
            first_name="Maryam",
            last_name=None,
        )

    def test_signing_in_claims_imported_requests(self) -> None:
        Match.objects.filter(pk=self.match.pk).update(initiated_by=self.shadow)
        real = upsert_telegram_user(self._identity(95010, "Maryam_Send"))
        self.demand.refresh_from_db()
        self.shadow.refresh_from_db()
        self.match.refresh_from_db()
        self.assertEqual(self.demand.user_id, real.pk)
        self.assertEqual(self.match.initiated_by_id, real.pk)
        self.assertFalse(self.shadow.is_active)
        self.assertIsNone(self.shadow.telegram_username)

    def test_reserved_handles_are_never_claimed(self) -> None:
        self.shadow.telegram_username = "koolbar"
        self.shadow.save()
        upsert_telegram_user(self._identity(95011, "koolbar"))
        self.demand.refresh_from_db()
        self.assertEqual(self.demand.user_id, self.shadow.pk)

    def test_outreach_link_opens_the_demand_for_its_owner(self) -> None:
        queued = self._queued()
        queued.recipient_telegram_id = 95012
        queued.save()
        owner = make_user(telegram_user_id=95012, first_name="Maryam")  # username hidden
        self.assertEqual(open_outreach(queued.token, owner), f"/app/matches/{self.match.pk}/")
        queued.refresh_from_db()
        self.assertIsNotNone(queued.opened_at)
        self.assertEqual(queued.opened_by_id, owner.pk)
        self.demand.refresh_from_db()
        self.assertEqual(self.demand.user_id, owner.pk)

    def test_outreach_link_with_several_travelers_opens_the_request(self) -> None:
        queued = self._queued()
        other = make_user(telegram_user_id=95006, first_name="Nima", telegram_username="nima_fly")
        create_item_request(other, {**SUPPLY_PAYLOAD, "flight_date": "2027-09-08"})
        owner = make_user(telegram_user_id=95015, first_name="Maryam", telegram_username="maryam_send")
        self.assertEqual(open_outreach(queued.token, owner), f"/app/requests/{self.demand.pk}/")

    def test_forwarded_link_does_not_hand_over_the_demand(self) -> None:
        queued = self._queued()
        stranger = make_user(telegram_user_id=95013, first_name="Reza", telegram_username="reza_x")
        self.assertEqual(open_outreach(queued.token, stranger), "/app/explore/")
        self.demand.refresh_from_db()
        self.assertEqual(self.demand.user_id, self.shadow.pk)
        self.assertEqual(open_outreach("unknown-token", stranger), "/app/")

    def test_mini_app_route(self) -> None:
        queued = self._queued()
        self.assertEqual(startapp_path(f"o_{queued.token}"), f"/app/o/{queued.token}/")
        self.assertEqual(startapp_path("o_bad token"), "/app/")
        owner = make_user(telegram_user_id=95014, first_name="Maryam", telegram_username="maryam_send")
        session = self.client.session
        session[SESSION_USER_KEY] = owner.pk
        session.save()
        response = self.client.get(f"/app/o/{queued.token}/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/app/matches/{self.match.pk}/")

    def test_synthetic_telegram_id_is_never_shown(self) -> None:
        text = connected_text(self.match, self.traveler)
        self.assertNotIn(str(SHADOW_ID), text)
        self.assertIn("@maryam_send", text)
        session = self.client.session
        session[SESSION_USER_KEY] = self.traveler.pk
        session.save()
        page = self.client.get(f"/app/matches/{self.match.pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "@maryam_send")
        self.assertNotContains(page, str(SHADOW_ID))
