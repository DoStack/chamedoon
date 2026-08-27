from __future__ import annotations

from decimal import Decimal
from urllib.parse import parse_qs, unquote, urlparse
from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APITestCase

from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from matching.models import Match, MatchStatus
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
        demand = self._demand()
        supply = self._supply()

        match = Match.objects.get()
        self.assertEqual(match.demand_request_id, demand.id)
        self.assertEqual(match.supply_request_id, supply.id)
        self.assertEqual(match.status, MatchStatus.SUGGESTED)
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

    def test_non_overlapping_dates_do_not_match(self) -> None:
        self._demand()
        self._supply(date_from="2027-10-01", date_to="2027-10-01")
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

    def test_edit_invalidates_suggested_match(self) -> None:
        demand = self._demand()
        self._supply()
        self.assertEqual(Match.objects.filter(status=MatchStatus.SUGGESTED).count(), 1)

        from item_requests.services import update_item_request

        update_item_request(demand, {"destination_city": "vancouver", "destination_country": "CA"})
        self.assertEqual(Match.objects.filter(status=MatchStatus.SUGGESTED).count(), 0)
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

    def test_participants_see_match_without_contact_until_connected(self) -> None:
        response = self.client.get("/api/matches/", **bearer_auth(self.demand_user))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        row = response.json()[0]
        self.assertEqual(row["status"], MatchStatus.SUGGESTED)
        self.assertIsNone(row["counterpart"])
        self.assertNotIn("omar_travel", str(row))

        outsider = self.client.get(f"/api/matches/{self.match.id}/", **bearer_auth(self.outsider))
        self.assertEqual(outsider.status_code, 404)

    def test_two_sided_accept_connects_and_reveals_telegram(self) -> None:
        first = self.client.post(
            f"/api/matches/{self.match.id}/accept/",
            **bearer_auth(self.demand_user),
        )
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json()["status"], MatchStatus.ACCEPTED_BY_DEMAND)
        self.assertIsNone(first.json()["counterpart"])

        second = self.client.post(
            f"/api/matches/{self.match.id}/accept/",
            **bearer_auth(self.supply_user),
        )
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.json()["status"], MatchStatus.CONNECTED)
        counterpart = second.json()["counterpart"]
        self.assertEqual(counterpart["first_name"], "Leila")
        self.assertIsNone(counterpart["telegram_username"])
        self.assertEqual(counterpart["telegram_user_id"], 93001)
        self.assertTrue(counterpart["telegram_url"].startswith("tg://user?id="))
        self.assertIn("مسافر این مسیرم", counterpart["draft"])
        self.assertIn("Leila", counterpart["draft"])
        self.assertNotIn("فرستنده‌ام", counterpart["draft"])

        demand_view = self.client.get(
            f"/api/matches/{self.match.id}/",
            **bearer_auth(self.demand_user),
        )
        demand_contact = demand_view.json()["counterpart"]
        self.assertEqual(demand_contact["telegram_username"], "omar_travel")
        self.assertTrue(demand_contact["telegram_url"].startswith("https://t.me/omar_travel?text="))
        demand_draft = _draft_from_url(demand_contact["telegram_url"])
        self.assertEqual(demand_draft, demand_contact["draft"])
        self.assertIn("فرستنده‌ام", demand_draft)
        self.assertIn("Omar", demand_draft)
        self.assertIn("تهران", demand_draft)
        self.assertIn("تورنتو", demand_draft)
        self.assertNotIn("مسافر این مسیرم", demand_draft)

        supply_view = self.client.get(
            f"/api/matches/{self.match.id}/",
            **bearer_auth(self.supply_user),
        )
        supply_draft = supply_view.json()["counterpart"]["draft"]
        self.assertIn("مسافر این مسیرم", supply_draft)
        self.assertIn("Leila", supply_draft)
        self.assertNotIn("فرستنده‌ام", supply_draft)

    def test_complete_then_both_sides_rate(self) -> None:
        self.client.post(f"/api/matches/{self.match.id}/accept/", **bearer_auth(self.demand_user))
        self.client.post(f"/api/matches/{self.match.id}/accept/", **bearer_auth(self.supply_user))
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
        self.assertTrue(finished.json()["can_rate"])
        self.assertFalse(finished.json()["can_complete"])

        rated = self.client.post(
            f"/api/matches/{self.match.id}/rate/",
            {"score": 5, "comment": "Smooth handover at the airport."},
            format="json",
            **bearer_auth(self.demand_user),
        )
        self.assertEqual(rated.status_code, 200, rated.content)
        self.assertEqual(rated.json()["my_rating"], 5)
        self.assertEqual(rated.json()["my_comment"], "Smooth handover at the airport.")
        self.assertEqual(rated.json()["their_comment"], "")
        self.assertIsNone(rated.json()["their_rating"])
        self.assertFalse(rated.json()["can_rate"])

        other = self.client.post(
            f"/api/matches/{self.match.id}/rate/",
            {"score": 4, "comment": "Package arrived as described."},
            format="json",
            **bearer_auth(self.supply_user),
        )
        self.assertEqual(other.status_code, 200)
        self.assertEqual(other.json()["my_rating"], 4)
        self.assertEqual(other.json()["my_comment"], "Package arrived as described.")
        self.assertEqual(other.json()["their_rating"], 5)
        self.assertEqual(other.json()["their_comment"], "Smooth handover at the airport.")

        again = self.client.post(
            f"/api/matches/{self.match.id}/rate/",
            {"score": 1},
            format="json",
            **bearer_auth(self.demand_user),
        )
        self.assertEqual(again.status_code, 400)

    def test_reject_hides_match_from_lists(self) -> None:
        response = self.client.post(
            f"/api/matches/{self.match.id}/reject/",
            **bearer_auth(self.supply_user),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], MatchStatus.REJECTED)
        listing = self.client.get("/api/matches/", **bearer_auth(self.demand_user))
        self.assertEqual(listing.json(), [])
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, MatchStatus.REJECTED)

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
