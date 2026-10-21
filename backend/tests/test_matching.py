from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, unquote, urlparse
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from item_requests.models import Category, City, Country, ItemRequest
from item_requests.seed import CATEGORIES, seed_catalog
from item_requests.services import create_item_request, update_item_request
from matching import rules
from matching.contact import CATEGORY_EMOJI, is_person_user
from matching.models import MIN_VISIBLE_SCORE, Match, MatchRating, MatchStatus
from matching.scoring import calculate_score, capacity_score, category_score, date_proximity_score
from matching.services import find_match_candidates, find_matches, persist_candidates
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
        self.assertEqual(match.score, Decimal("100.00"))
        self.assertEqual(match.score_label, "STRONG")
        self.assertEqual(calculate_score(demand, supply), Decimal("100.00"))

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


DOCS_DEMAND = {**DEMAND_PAYLOAD, "weight_kg": "0.10", "item_category_codes": ["DOCUMENTS"]}
DOCS_SUPPLY = {
    **SUPPLY_PAYLOAD,
    "capacity_kg": "0.10",
    "item_category_codes": ["DOCUMENTS"],
    "excluded_category_codes": [],
}


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False)
class MatchingPrecisionTests(APITestCase):
    """Cases from the 2026-10-21 production dry run (precision 5/8, all wrong = not a person)."""

    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.sender = make_user(telegram_user_id=93501, first_name="Kourosh")
        self.traveler = make_user(telegram_user_id=93502, first_name="Ali", telegram_username="ali_carry")

    def _make(self, user, base, kw):
        data = {**base, **kw}
        cats = data.pop("item_category_codes", [])
        excl = data.pop("excluded_category_codes", [])
        data.pop("description", None)
        if data["type"] == "DEMAND":
            data.setdefault("date_from", data["desired_date"])
            data.setdefault("date_to", data["desired_date"])
        data.setdefault("destination_cities", [])
        item = ItemRequest.objects.create(
            user=user, imported=True, expires_at=timezone.now() + timedelta(days=30), **data
        )
        item.item_categories.set(Category.objects.filter(code__in=cats))
        item.excluded_categories.set(Category.objects.filter(code__in=excl))
        item.refresh_from_db()
        return item

    def _demand(self, user=None, **kw):
        return self._make(user or self.sender, DOCS_DEMAND, kw)

    def _supply(self, user=None, **kw):
        return self._make(user or self.traveler, DOCS_SUPPLY, kw)

    def _matched(self, item):
        return [candidate.supply.pk for candidate in find_match_candidates(item)]

    # ---------------------------------------------------------------- dates

    def test_flight_date_missing_falls_back_to_date_to(self) -> None:
        demand = self._demand(desired_date="2027-09-07")
        supply = self._supply(flight_date=None, date_from="2027-09-01", date_to="2027-09-10")
        self.assertTrue(rules.dates_compatible(demand, supply))

    def test_flight_on_desired_day_matches_at_full_score(self) -> None:
        demand = self._demand(desired_date="2027-09-10")
        supply = self._supply(flight_date="2027-09-10")
        self.assertEqual(self._matched(demand), [supply.pk])
        self.assertEqual(calculate_score(demand, supply), Decimal("100.00"))

    def test_date_points_fall_from_three_to_sixty_days(self) -> None:
        demand = self._demand(desired_date="2027-09-01")
        near = self._supply(flight_date="2027-09-04")
        month = self._supply(flight_date="2027-10-01")
        far = self._supply(flight_date="2027-11-15")
        self.assertEqual(date_proximity_score(demand, near), Decimal("30"))
        self.assertEqual(date_proximity_score(demand, month), Decimal("15.79"))
        self.assertEqual(date_proximity_score(demand, far), Decimal("0.00"))

    def test_flight_months_after_desired_date_is_review_not_outreach(self) -> None:
        demand = self._demand(desired_date="2027-09-07")
        supply = self._supply(flight_date="2028-03-01", date_from="2027-09-01", date_to="2028-02-25")
        score = calculate_score(demand, supply)
        self.assertTrue(rules.passes_hard_rules(demand, supply))
        self.assertGreaterEqual(score, MIN_VISIBLE_SCORE)
        self.assertLess(score, Decimal("80"))

    def test_flight_one_to_three_days_early_matches_with_fewer_points(self) -> None:
        demand = self._demand(desired_date="2027-09-11")
        one_early = self._supply(flight_date="2027-09-10")
        three_early = self._supply(
            user=make_user(telegram_user_id=93511, first_name="Sam"), flight_date="2027-09-08"
        )
        four_early = self._supply(
            user=make_user(telegram_user_id=93512, first_name="Nima"), flight_date="2027-09-07"
        )
        self.assertEqual(sorted(self._matched(demand)), sorted([one_early.pk, three_early.pk]))
        self.assertEqual(date_proximity_score(demand, one_early), Decimal("18.00"))
        self.assertEqual(date_proximity_score(demand, three_early), Decimal("6.00"))
        self.assertFalse(rules.dates_compatible(demand, four_early))

    def test_open_date_sender_takes_any_upcoming_flight(self) -> None:
        demand = create_item_request(
            self.sender,
            {**DOCS_DEMAND, "desired_date_open": True, "date_to": "2027-09-20"},
            imported=True,
        )
        self.assertIsNone(demand.desired_date)
        self.assertEqual(demand.date_to.isoformat(), "2027-09-20")
        supply = self._supply(flight_date="2027-09-10")  # before the old import default
        self.assertEqual(self._matched(demand), [supply.pk])

    def test_partial_edit_keeps_an_open_date_open(self) -> None:
        demand = create_item_request(
            self.sender,
            {**DOCS_DEMAND, "desired_date_open": True, "date_to": "2027-09-20"},
            imported=True,
        )
        update_item_request(demand, {"description": "Two envelopes"})
        demand.refresh_from_db()
        self.assertIsNone(demand.desired_date)
        update_item_request(demand, {"desired_date": "2027-09-12"})
        demand.refresh_from_db()
        self.assertEqual(demand.desired_date.isoformat(), "2027-09-12")

    def test_rules_import_stores_no_default_desired_date(self) -> None:
        from market.migrate import _clean_with_rules
        from market.models import MarketPost, MarketRole

        post = MarketPost.objects.create(
            channel_username="koolbarcanada",
            telegram_message_id=7001,
            posted_at=timezone.now(),
            text="مدارک دارم از تورنتو به تهران، کسی میبره؟",
            role=MarketRole.DEMAND,
        )
        payload, reason = _clean_with_rules(post)
        self.assertIsNone(reason)
        self.assertTrue(payload["desired_date_open"])
        self.assertNotIn("desired_date", payload)
        demand = create_item_request(self.sender, payload, imported=True)
        self.assertIsNone(demand.desired_date)

    # ---------------------------------------------------------------- import flags

    def _imported_supply(self, **overrides):
        today = timezone.localdate()
        payload = {
            **DOCS_SUPPLY,
            "flight_date": (today + timedelta(days=10)).isoformat(),
            "date_from": today.isoformat(),
            "date_to": (today + timedelta(days=7)).isoformat(),
            **overrides,
        }
        return create_item_request(self.traveler, payload, imported=True)

    def test_imported_flight_far_out_is_kept_but_not_matchable(self) -> None:
        far = (timezone.localdate() + timedelta(days=200)).isoformat()
        supply = self._imported_supply(flight_date=far, date_to=far)
        self.assertEqual(supply.unmatchable_reason, "flight_too_far")
        self.assertEqual(supply.status, "ACTIVE")
        self.assertFalse(rules.is_matchable(supply))

    def test_imported_inverted_window_is_not_matchable(self) -> None:
        today = timezone.localdate()
        supply = self._imported_supply(
            flight_date=(today + timedelta(days=2)).isoformat(),
            date_from=(today + timedelta(days=5)).isoformat(),
            date_to=(today + timedelta(days=6)).isoformat(),
        )
        self.assertEqual(supply.unmatchable_reason, "dates_inverted")

    def test_imported_normal_flight_is_matchable(self) -> None:
        supply = self._imported_supply()
        self.assertEqual(supply.unmatchable_reason, "")
        self.assertTrue(rules.is_matchable(supply))

    def test_past_flight_is_never_matched(self) -> None:
        today = timezone.localdate()
        demand = self._demand(desired_date=(today + timedelta(days=1)).isoformat())
        supply = self._supply(
            flight_date=(today - timedelta(days=1)).isoformat(),
            date_from=(today - timedelta(days=9)).isoformat(),
            date_to=(today + timedelta(days=5)).isoformat(),
        )
        self.assertFalse(rules.is_matchable(supply))
        self.assertEqual(self._matched(demand), [])

    # ---------------------------------------------------------------- places

    def test_multi_stop_supply_matches_middle_stop(self) -> None:
        demand = self._demand(destination_country="TR", destination_city="istanbul")
        supply = self._supply(
            destination_country="IR",
            destination_city="tehran",
            destination_cities=[
                {"country": "TR", "city": "istanbul"},
                {"country": "IR", "city": "tehran"},
            ],
        )
        self.assertEqual(self._matched(demand), [supply.pk])

    def test_multi_city_demand_keeps_every_destination(self) -> None:
        demand = create_item_request(
            self.sender,
            {
                **DOCS_DEMAND,
                "origin_country": "CA",
                "origin_city": "toronto",
                "destination_country": "IR",
                "destination_city": "mashhad",
                "destination_cities": [
                    {"country": "IR", "city": "mashhad"},
                    {"country": "IR", "city": "tehran"},
                ],
            },
        )
        self.assertEqual(demand.destination_stop_pairs(), [("IR", "mashhad"), ("IR", "tehran")])
        supply = self._supply(
            origin_country="CA", origin_city="toronto", destination_country="IR", destination_city="tehran"
        )
        self.assertEqual(self._matched(demand), [supply.pk])

    def test_hambrg_slug_merges_into_hamburg(self) -> None:
        from importlib import import_module

        from django.apps import apps as global_apps

        migration = import_module("item_requests.migrations.0017_unmatchable_reason_merge_hambrg")
        country = Country.objects.get(code="DE")
        City.objects.get_or_create(
            country=country, slug="hambrg", defaults={"name_en": "Hambrg", "name_fa": "Hambrg"}
        )
        supply = self._supply(
            origin_country="IR",
            origin_city="isfahan",
            destination_country="DE",
            destination_city="hambrg",
            destination_cities=[{"country": "DE", "city": "hamburg"}, {"country": "DE", "city": "hambrg"}],
        )
        migration.merge_city_slugs(global_apps, None)
        migration.merge_city_slugs(global_apps, None)  # a second run is a no-op
        supply.refresh_from_db()
        self.assertFalse(City.objects.filter(country=country, slug="hambrg").exists())
        self.assertEqual(supply.destination_city, "hamburg")
        self.assertEqual(supply.destination_cities, [{"country": "DE", "city": "hamburg"}])
        demand = self._demand(
            origin_country="IR", origin_city="isfahan", destination_country="DE", destination_city="hamburg"
        )
        self.assertEqual(self._matched(demand), [supply.pk])

    # ---------------------------------------------------------------- categories

    def test_exclusion_beats_overlap(self) -> None:
        demand = self._demand(item_category_codes=["DOCUMENTS", "MEDICINE"])
        self._supply(item_category_codes=["DOCUMENTS", "MEDICINE"], excluded_category_codes=["MEDICINE"])
        self.assertEqual(self._matched(demand), [])

    def test_supply_without_categories_never_matches(self) -> None:
        demand = self._demand()
        supply = self._supply(item_category_codes=[])
        self.assertFalse(rules.categories_compatible(demand, supply))

    def test_documents_fit_a_traveler_who_only_listed_medicine(self) -> None:
        demand = self._demand(item_category_codes=["DOCUMENTS"])
        medicine = self._supply(item_category_codes=["MEDICINE"])
        self.assertEqual(self._matched(demand), [medicine.pk])
        self.assertEqual(category_score(demand, medicine), Decimal("5.00"))

    def test_documents_do_not_go_to_a_traveler_who_excludes_them(self) -> None:
        demand = self._demand(item_category_codes=["DOCUMENTS"])
        self._supply(item_category_codes=["MEDICINE"], excluded_category_codes=["DOCUMENTS"])
        self.assertEqual(self._matched(demand), [])

    def test_clothes_still_need_overlap(self) -> None:
        demand = self._demand(item_category_codes=["CLOTHES"], weight_kg="1.00")
        self._supply(item_category_codes=["PET"], capacity_kg="5.00")
        self.assertEqual(self._matched(demand), [])

    # ---------------------------------------------------------------- score

    def test_hard_rule_pass_does_not_guarantee_outreach_score(self) -> None:
        demand = self._demand(
            desired_date="2027-09-01", item_category_codes=["DOCUMENTS", "CLOTHES"], weight_kg="0.10"
        )
        supply = self._supply(
            flight_date="2027-10-20",
            date_to="2027-10-17",
            capacity_kg="40.00",
            item_category_codes=["DOCUMENTS"],
        )
        self.assertTrue(rules.passes_hard_rules(demand, supply))
        score = calculate_score(demand, supply)
        self.assertGreaterEqual(score, MIN_VISIBLE_SCORE)
        self.assertLess(score, Decimal("80"))

    def test_big_suitcase_is_not_penalised(self) -> None:
        demand = self._demand()
        big = self._supply(capacity_kg="40.00")
        other = make_user(telegram_user_id=93503, first_name="Sam")
        small = self._supply(user=other, capacity_kg="0.10")
        self.assertEqual(capacity_score(demand, big), capacity_score(demand, small))

    # ---------------------------------------------------------------- who is the traveler

    def test_channel_account_is_not_offered_as_traveler(self) -> None:
        from outreach.services import valid_matches

        channel = make_user(
            telegram_user_id=9_000_000_000_777, first_name="chamed0on", telegram_username="chamed0on"
        )
        demand = self._demand()
        self._supply(user=channel)
        self.assertEqual(self._matched(demand), [])
        persist_candidates(find_match_candidates(demand))
        self.assertEqual(valid_matches(demand, timezone.now() + timedelta(seconds=1)), [])

    def test_stored_match_with_business_traveler_is_hidden_from_outreach(self) -> None:
        from outreach.services import valid_matches

        agency = make_user(telegram_user_id=93504, first_name="Hirbod", telegram_username="HirbodRealEstateTeam")
        demand = self._demand()
        supply = self._supply(user=agency)
        Match.objects.create(demand_request=demand, supply_request=supply, score=Decimal("100"))
        self.assertEqual(valid_matches(demand, timezone.now()), [])

    def test_business_names_are_not_people(self) -> None:
        for index, name in enumerate(
            ("HirbodRealEstateTeam", "Toronto Cargo", "Parsian Agency", "باربری پارس", "Sky Travels")
        ):
            user = make_user(telegram_user_id=93600 + index, first_name=name)
            self.assertFalse(is_person_user(user), name)
        for index, (name, username) in enumerate((("Omar", "omar_travel"), ("Sara", "sara_send"), ("Negar", None))):
            user = make_user(telegram_user_id=93700 + index, first_name=name, telegram_username=username)
            self.assertTrue(is_person_user(user), name)

    def test_bot_sender_is_not_matched(self) -> None:
        bot = make_user(telegram_user_id=93505, first_name="Helper", telegram_username="helper_carry_bot")
        demand = self._demand(user=bot)
        self._supply()
        self.assertEqual(self._matched(demand), [])

    def test_five_place_route_is_an_agency_but_whole_country_counts_once(self) -> None:
        demand = self._demand(destination_country="IR", destination_city="tehran")
        whole_iran = self._supply(
            destination_country="IR",
            destination_city="tehran",
            destination_cities=[
                {"country": "IR", "city": city} for city in ("mashhad", "isfahan", "shiraz", "tabriz", "tehran")
            ],
        )
        other = make_user(telegram_user_id=93506, first_name="Reza", telegram_username="reza_go")
        agency_route = self._supply(
            user=other,
            destination_country="IR",
            destination_city="tehran",
            destination_cities=[
                {"country": "TR", "city": "ankara"},
                {"country": "AE", "city": "dubai"},
                {"country": "AM", "city": "yerevan"},
                {"country": "IQ", "city": "baghdad"},
                {"country": "IR", "city": "tehran"},
            ],
        )
        self.assertEqual(rules.destination_place_count(whole_iran), 1)
        self.assertEqual(rules.destination_place_count(agency_route), 5)
        self.assertEqual(self._matched(demand), [whole_iran.pk])

    # ---------------------------------------------------------------- duplicates

    def test_same_sender_posting_twice_gets_one_set_of_matches(self) -> None:
        first = self._demand(desired_date="2027-09-07")
        second = self._demand(desired_date="2027-09-08")
        supply = self._supply(flight_date="2027-09-10")
        self.assertEqual(self._matched(first), [])
        self.assertEqual(self._matched(second), [supply.pk])
        demands = [candidate.demand.pk for candidate in find_match_candidates(supply)]
        self.assertEqual(demands, [second.pk])

    def test_same_sender_different_route_is_not_a_duplicate(self) -> None:
        first = self._demand()
        self._demand(destination_city="vancouver")
        supply = self._supply()
        self.assertEqual(self._matched(first), [supply.pk])

    def test_repost_expires_matches_of_the_older_copy(self) -> None:
        supply = create_item_request(self.traveler, DOCS_SUPPLY)
        first = create_item_request(self.sender, DOCS_DEMAND)
        self.assertEqual(Match.objects.get(demand_request=first).status, MatchStatus.CONNECTED)
        second = create_item_request(self.sender, DOCS_DEMAND)
        self.assertEqual(Match.objects.get(demand_request=first).status, MatchStatus.EXPIRED)
        self.assertEqual(Match.objects.get(demand_request=second).supply_request_id, supply.pk)
