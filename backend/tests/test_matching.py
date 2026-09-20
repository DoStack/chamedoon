from __future__ import annotations

from decimal import Decimal
from urllib.parse import parse_qs, unquote, urlparse
from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APITestCase

from item_requests.seed import CATEGORIES, seed_catalog
from item_requests.services import create_item_request
from matching.contact import CATEGORY_EMOJI
from matching.models import Match, MatchRating, MatchStatus
from matching.scoring import calculate_score
from matching.services import find_matches
from tests.helpers import TEST_SECRET, bearer_auth, make_user
from tests.test_requests import DEMAND_PAYLOAD, SUPPLY_PAYLOAD


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False)
class MatchingEngineTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.demand_user = make_user(telegram_user_id=92001, first_name="Sara")
        self.supply_user = make_user(
            telegram_user_id=92002,
            first_name="Ali",
            telegram_username="ali_carry",
        )

    def _demand(self, **overrides):
        return create_item_request(self.demand_user, {**DEMAND_PAYLOAD, **overrides})

    def _supply(self, **overrides):
        return create_item_request(self.supply_user, {**SUPPLY_PAYLOAD, **overrides})

    def test_compatible_requests_create_a_strong_match(self) -> None:
        with patch("notifications.services.notify_connected") as notify:
            with self.captureOnCommitCallbacks(execute=True):
                demand = self._demand()
                supply = self._supply()
        notify.assert_called()

        match = Match.objects.get()
        self.assertEqual(match.demand_request_id, demand.id)
        self.assertEqual(match.supply_request_id, supply.id)
        self.assertEqual(match.status, MatchStatus.CONNECTED)
        self.assertEqual(match.initiated_by_id, self.supply_user.id)
        self.assertTrue(match.is_owner(self.demand_user))
        self.assertTrue(match.is_requester(self.supply_user))
        self.assertEqual(match.score, Decimal("94.00"))
        self.assertEqual(match.score_label, "STRONG")
        self.assertEqual(calculate_score(demand, supply), Decimal("94.00"))

    def test_origin_mismatch_does_not_match(self) -> None:
        self._demand()
        self._supply(origin_city="mashhad")
        self.assertEqual(Match.objects.count(), 0)

    def test_destination_mismatch_does_not_match(self) -> None:
        self._demand()
        self._supply(destination_city="vancouver")
        self.assertEqual(Match.objects.count(), 0)

    def test_supply_stops_match_either_destination(self) -> None:
        self._demand(
            origin_country="IT",
            origin_city="milan",
            destination_country="IR",
            destination_city="tehran",
        )
        self._demand(
            origin_country="IT",
            origin_city="milan",
            destination_country="IR",
            destination_city="mashhad",
        )
        supply = self._supply(
            origin_country="IT",
            origin_city="milan",
            destination_country="IR",
            destination_city="mashhad",
            destination_cities=[
                {"country": "IR", "city": "tehran"},
                {"country": "IR", "city": "mashhad"},
            ],
        )
        dests = {match.demand_request.destination_city for match in Match.objects.filter(supply_request=supply)}
        self.assertEqual(dests, {"tehran", "mashhad"})

    def test_desired_date_after_flight_does_not_match(self) -> None:
        self._demand(desired_date="2027-10-01")
        self._supply(flight_date="2027-09-10")
        self.assertEqual(Match.objects.count(), 0)

    def test_desired_date_before_flight_matches(self) -> None:
        demand = self._demand(desired_date="2027-09-07")
        supply = self._supply(
            flight_date="2027-10-01",
            date_from="2027-09-20",
            date_to="2027-10-05",
        )
        match = Match.objects.get()
        self.assertEqual(match.demand_request_id, demand.id)
        self.assertEqual(match.supply_request_id, supply.id)

    def test_category_without_overlap_does_not_match(self) -> None:
        self._demand(item_category_codes=["FOOD"])
        self._supply()
        self.assertEqual(Match.objects.count(), 0)

    def test_insufficient_capacity_does_not_match(self) -> None:
        self._demand(weight_kg="6.00")
        self._supply(capacity_kg="5.00")
        self.assertEqual(Match.objects.count(), 0)

    def test_excluded_category_does_not_match(self) -> None:
        self._demand(item_category_codes=["MEDICINE"])
        self._supply(excluded_category_codes=["MEDICINE", "CIGARETTES"])
        self.assertEqual(Match.objects.count(), 0)

    @patch("matching.services.calculate_score", return_value=Decimal("50.00"))
    def test_weak_scores_are_not_persisted(self, _mocked_score) -> None:
        demand = self._demand()
        self._supply()
        self.assertEqual(Match.objects.count(), 0)
        self.assertEqual(find_matches(demand), [])

    def test_every_catalog_category_has_a_telegram_emoji(self) -> None:
        self.assertEqual(
            {item["code"] for item in CATEGORIES},
            set(CATEGORY_EMOJI),
        )

    def test_edit_invalidates_suggested_match(self) -> None:
        demand = self._demand()
        self._supply()
        self.assertEqual(Match.objects.filter(status=MatchStatus.CONNECTED).count(), 1)

        from item_requests.services import update_item_request

        update_item_request(demand, {"destination_city": "vancouver", "destination_country": "CA"})
        self.assertEqual(Match.objects.filter(status=MatchStatus.CONNECTED).count(), 0)
        self.assertEqual(Match.objects.filter(status=MatchStatus.EXPIRED).count(), 1)

    def test_cancel_expires_open_matches(self) -> None:
        demand = self._demand()
        self._supply()
        from item_requests.services import cancel_item_request

        cancel_item_request(demand)
        self.assertEqual(Match.objects.get().status, MatchStatus.EXPIRED)


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False)
class MatchApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.demand_user = make_user(telegram_user_id=93001, first_name="Leila")
        self.supply_user = make_user(
            telegram_user_id=93002,
            first_name="Omar",
            telegram_username="omar_travel",
        )
        self.outsider = make_user(telegram_user_id=93003, first_name="Nima")
        create_item_request(self.demand_user, DEMAND_PAYLOAD)
        create_item_request(self.supply_user, SUPPLY_PAYLOAD)
        self.match = Match.objects.get()

    def test_participants_see_telegram_as_soon_as_match_exists(self) -> None:
        response = self.client.get("/api/matches/", **bearer_auth(self.demand_user))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        row = response.json()[0]
        self.assertEqual(row["status"], MatchStatus.CONNECTED)
        self.assertTrue(row["is_owner"])
        self.assertFalse(row["is_requester"])
        counterpart = row["counterpart"]
        self.assertEqual(counterpart["telegram_username"], "omar_travel")
        self.assertTrue(counterpart["telegram_url"].startswith("https://t.me/omar_travel?text="))
        self.assertTrue(counterpart["tg_url"].startswith("tg://resolve?domain=omar_travel&text="))
        self.assertNotIn("score", row)
        self.assertNotIn("score_label", row)
        self.assertNotIn("my_rating", row)
        self.assertNotIn("their_rating", row)

        outsider = self.client.get(f"/api/matches/{self.match.id}/", **bearer_auth(self.outsider))
        self.assertEqual(outsider.status_code, 404)

    def test_match_detail_shows_telegram_draft_for_both_sides(self) -> None:
        demand_view = self.client.get(
            f"/api/matches/{self.match.id}/",
            **bearer_auth(self.demand_user),
        )
        demand_contact = demand_view.json()["counterpart"]
        self.assertEqual(demand_contact["telegram_username"], "omar_travel")
        self.assertTrue(demand_contact["telegram_url"].startswith("https://t.me/omar_travel?text="))
        self.assertTrue(demand_contact["tg_url"].startswith("tg://resolve?domain=omar_travel&text="))
        demand_draft = _draft_from_url(demand_contact["telegram_url"])
        self.assertEqual(demand_draft, demand_contact["draft"])
        self.assertIn("من یه بار دارم", demand_draft)
        self.assertIn("Omar", demand_draft)
        self.assertIn("🇮🇷 تهران", demand_draft)
        self.assertIn("🇨🇦 تورنتو", demand_draft)
        self.assertIn("👕 لباس", demand_draft)
        self.assertNotIn("من ظرفیت دارم", demand_draft)

        self.demand_user.telegram_username = "leila_send"
        self.demand_user.save(update_fields=["telegram_username"])
        supply_view = self.client.get(
            f"/api/matches/{self.match.id}/",
            **bearer_auth(self.supply_user),
        )
        supply_contact = supply_view.json()["counterpart"]
        supply_draft = supply_contact["draft"]
        self.assertTrue(supply_contact["telegram_url"].startswith("https://t.me/leila_send?text="))
        self.assertTrue(supply_contact["tg_url"].startswith("tg://resolve?domain=leila_send&text="))
        self.assertIn("من ظرفیت دارم", supply_draft)
        self.assertIn("می‌تونم ببرم", supply_draft)
        self.assertIn("Leila", supply_draft)
        self.assertIn("👕 لباس", supply_draft)
        self.assertNotIn("من یه بار دارم", supply_draft)

    def test_dm_contact_strips_at_and_skips_synthetic_user_ids(self) -> None:
        from matching.contact import clean_telegram_username, telegram_dm_contact

        self.assertEqual(clean_telegram_username("@Sarra_mtd@"), "Sarra_mtd")
        self.assertEqual(clean_telegram_username("https://t.me/Sarra_mtd"), "Sarra_mtd")
        self.supply_user.telegram_username = "@Omar_travel@"
        self.supply_user.save(update_fields=["telegram_username"])
        contact = telegram_dm_contact(self.supply_user, "سلام")
        self.assertEqual(contact["telegram_username"], "Omar_travel")
        self.assertEqual(contact["chat_url"], "https://t.me/Omar_travel")
        self.assertTrue(contact["https_url"].startswith("https://t.me/Omar_travel?text="))
        self.assertTrue(contact["tg_url"].startswith("tg://resolve?domain=Omar_travel&text="))
        self.assertEqual(_draft_from_url(contact["https_url"]), "سلام")

        imported = make_user(telegram_user_id=9_002_886_473_171, first_name="ارسال بار به سراسر دنیا")
        empty = telegram_dm_contact(imported, "سلام")
        self.assertEqual(empty["telegram_url"], "")
        self.assertIsNone(empty["https_url"])
        self.assertIsNone(empty["tg_url"])

    def test_complete_then_both_sides_rate(self) -> None:
        self.client.post(f"/api/matches/{self.match.id}/accept/", **bearer_auth(self.demand_user))
        too_soon = self.client.post(
            f"/api/matches/{self.match.id}/rate/",
            {"score": 5},
            format="json",
            **bearer_auth(self.demand_user),
        )
        self.assertEqual(too_soon.status_code, 400)

        finished = self.client.post(
            f"/api/matches/{self.match.id}/complete/",
            **bearer_auth(self.demand_user),
        )
        self.assertEqual(finished.status_code, 200, finished.content)
        self.assertEqual(finished.json()["status"], MatchStatus.COMPLETED)
        listing = self.client.get("/api/matches/", **bearer_auth(self.demand_user))
        self.assertEqual(listing.json(), [])
        self.assertTrue(finished.json()["can_rate"])
        self.assertFalse(finished.json()["can_complete"])
        self.assertIsNotNone(finished.json()["counterpart"])
        self.assertIn("ممنونم", finished.json()["counterpart"]["draft"])
        self.match.demand_request.refresh_from_db()
        self.match.supply_request.refresh_from_db()
        self.assertTrue(self.match.demand_request.package_sent)
        self.assertTrue(self.match.supply_request.package_sent)

        rated = self.client.post(
            f"/api/matches/{self.match.id}/rate/",
            {"score": 5, "comment": "Smooth handover at the airport."},
            format="json",
            **bearer_auth(self.demand_user),
        )
        self.assertEqual(rated.status_code, 200, rated.content)
        self.assertNotIn("my_rating", rated.json())
        self.assertNotIn("their_rating", rated.json())
        self.assertNotIn("score", rated.json())
        self.assertFalse(rated.json()["can_rate"])
        self.assertEqual(
            MatchRating.objects.get(match=self.match, rater=self.demand_user).score,
            5,
        )
        self.assertEqual(
            MatchRating.objects.get(match=self.match, rater=self.demand_user).comment,
            "Smooth handover at the airport.",
        )

        other = self.client.post(
            f"/api/matches/{self.match.id}/rate/",
            {"score": 4, "comment": "Package arrived as described."},
            format="json",
            **bearer_auth(self.supply_user),
        )
        self.assertEqual(other.status_code, 200)
        self.assertNotIn("my_rating", other.json())
        self.assertNotIn("their_rating", other.json())
        self.assertEqual(
            MatchRating.objects.get(match=self.match, rater=self.supply_user).score,
            4,
        )

        again = self.client.post(
            f"/api/matches/{self.match.id}/rate/",
            {"score": 1},
            format="json",
            **bearer_auth(self.demand_user),
        )
        self.assertEqual(again.status_code, 400)

    def test_connected_match_cannot_be_cancelled(self) -> None:
        cancel = self.client.post(
            f"/api/matches/{self.match.id}/cancel/",
            **bearer_auth(self.supply_user),
        )
        self.assertEqual(cancel.status_code, 400)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, MatchStatus.CONNECTED)

    def test_owner_can_expire_connected_match(self) -> None:
        reject = self.client.post(
            f"/api/matches/{self.match.id}/reject/",
            **bearer_auth(self.demand_user),
        )
        self.assertEqual(reject.status_code, 200)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, MatchStatus.EXPIRED)

    def test_cancel_after_connect_hides_contact(self) -> None:
        from item_requests.services import cancel_item_request
        from matching.contact import contact_for_match

        self.client.post(f"/api/matches/{self.match.id}/accept/", **bearer_auth(self.demand_user))
        cancel_item_request(self.match.demand_request)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, MatchStatus.EXPIRED)
        self.assertIsNone(contact_for_match(self.match, self.demand_user))
        self.assertIsNone(contact_for_match(self.match, self.supply_user))
        listing = self.client.get("/api/matches/", **bearer_auth(self.demand_user))
        self.assertEqual(listing.json(), [])
        archived = self.client.get(f"/api/matches/{self.match.id}/", **bearer_auth(self.supply_user))
        self.assertEqual(archived.status_code, 200)
        self.assertIsNone(archived.json()["counterpart"])

    def test_expire_after_connect_hides_contact(self) -> None:
        from datetime import timedelta

        from django.utils import timezone

        from item_requests.services import expire_if_needed
        from matching.contact import contact_for_match

        self.client.post(f"/api/matches/{self.match.id}/accept/", **bearer_auth(self.demand_user))
        supply = self.match.supply_request
        supply.expires_at = timezone.now() - timedelta(minutes=1)
        supply.save(update_fields=["expires_at"])
        expire_if_needed(supply)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, MatchStatus.EXPIRED)
        self.assertIsNone(contact_for_match(self.match, self.demand_user))
        archived = self.client.get(f"/api/matches/{self.match.id}/", **bearer_auth(self.demand_user))
        self.assertEqual(archived.status_code, 200)
        self.assertIsNone(archived.json()["counterpart"])

    def test_supply_close_without_package_stops_messages(self) -> None:
        from matching.contact import contact_for_match

        self.client.post(f"/api/matches/{self.match.id}/accept/", **bearer_auth(self.demand_user))
        closed = self.client.post(
            f"/api/requests/{self.match.supply_request_id}/close/",
            {"package_sent": False},
            format="json",
            **bearer_auth(self.supply_user),
        )
        self.assertEqual(closed.status_code, 200, closed.content)
        self.assertEqual(closed.json()["status"], "CANCELLED")
        self.assertFalse(closed.json()["package_sent"])
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, MatchStatus.EXPIRED)
        self.assertIsNone(contact_for_match(self.match, self.demand_user))
        listing = self.client.get("/api/matches/", **bearer_auth(self.demand_user))
        self.assertEqual(listing.json(), [])

    def test_supply_close_with_package_counts_success_trip(self) -> None:
        from matching.contact import contact_for_match

        self.client.post(f"/api/matches/{self.match.id}/accept/", **bearer_auth(self.demand_user))
        closed = self.client.post(
            f"/api/requests/{self.match.supply_request_id}/close/",
            {"package_sent": True},
            format="json",
            **bearer_auth(self.supply_user),
        )
        self.assertEqual(closed.status_code, 200, closed.content)
        self.assertEqual(closed.json()["status"], "COMPLETED")
        self.assertTrue(closed.json()["package_sent"])
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, MatchStatus.COMPLETED)
        contact = contact_for_match(self.match, self.demand_user)
        self.assertIsNotNone(contact)
        self.assertIn("ممنونم", contact["draft"])
        finished = self.client.get(f"/api/matches/{self.match.id}/", **bearer_auth(self.demand_user))
        self.assertEqual(finished.status_code, 200)
        self.assertIsNotNone(finished.json()["counterpart"])

    def test_demand_cannot_close_with_trip_outcome(self) -> None:
        closed = self.client.post(
            f"/api/requests/{self.match.demand_request_id}/close/",
            {"package_sent": True},
            format="json",
            **bearer_auth(self.demand_user),
        )
        self.assertEqual(closed.status_code, 400)

    def test_unauthenticated_cannot_list_matches(self) -> None:
        response = self.client.get("/api/matches/")
        self.assertEqual(response.status_code, 401)

    def test_request_list_includes_match_count(self) -> None:
        response = self.client.get("/api/requests/", **bearer_auth(self.demand_user))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["match_count"], 1)


def _draft_from_url(url: str) -> str:
    parsed = urlparse(url)
    return unquote(parse_qs(parsed.query)["text"][0])


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False, TELEGRAM_WEBHOOK_SECRET="hook-secret")
class TelegramWebhookTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.demand_user = make_user(telegram_user_id=93101, first_name="Leila")
        self.supply_user = make_user(telegram_user_id=93102, first_name="Omar")
        self.outsider = make_user(telegram_user_id=93103, first_name="Nima")
        create_item_request(self.demand_user, DEMAND_PAYLOAD)
        create_item_request(self.supply_user, SUPPLY_PAYLOAD)
        self.match = Match.objects.get()

    def _callback(self, data: str, telegram_user_id: int, secret: str = "hook-secret"):
        return self.client.post(
            "/api/telegram/webhook/",
            {
                "callback_query": {
                    "id": "cbq-1",
                    "from": {"id": telegram_user_id},
                    "data": data,
                    "message": {"message_id": 44, "chat": {"id": telegram_user_id}},
                }
            },
            format="json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret,
        )

    def test_secret_mismatch_is_forbidden(self) -> None:
        response = self._callback(f"match:accept:{self.match.id}", 93101, secret="wrong")
        self.assertEqual(response.status_code, 403)

    @patch("api.telegram_webhook.edit_telegram_message", return_value=True)
    @patch("api.telegram_webhook.answer_callback_query", return_value=True)
    def test_owner_accept_via_callback(self, _answer, _edit) -> None:
        response = self._callback(f"match:accept:{self.match.id}", 93101)
        self.assertEqual(response.status_code, 200)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, MatchStatus.CONNECTED)

    @patch("api.telegram_webhook.edit_telegram_message", return_value=True)
    @patch("api.telegram_webhook.answer_callback_query", return_value=True)
    def test_owner_reject_then_close_listing(self, _answer, _edit) -> None:
        rejected = self._callback(f"match:reject:{self.match.id}", 93101)
        self.assertEqual(rejected.status_code, 200)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, MatchStatus.EXPIRED)
        closed = self._callback(f"listing:close:{self.match.id}", 93101)
        self.assertEqual(closed.status_code, 200)
        listing = self.match.owner_request()
        listing.refresh_from_db()
        self.assertEqual(listing.status, "CLOSED")

    @patch("api.telegram_webhook.edit_telegram_message", return_value=True)
    @patch("api.telegram_webhook.answer_callback_query", return_value=True)
    def test_outsider_callback_is_rejected(self, mocked_answer, _edit) -> None:
        response = self._callback(f"match:accept:{self.match.id}", 93103)
        self.assertEqual(response.status_code, 200)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, MatchStatus.CONNECTED)
        self.assertIn("not part", mocked_answer.call_args.args[1].lower())
