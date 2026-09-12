from __future__ import annotations

from django.test import override_settings
from rest_framework.test import APITestCase

from item_requests.explore import open_request_facets
from item_requests.models import RequestStatus
from item_requests.seed import seed_catalog
from item_requests.services import cancel_item_request, create_item_request
from matching.models import Match, MatchStatus
from tests.helpers import TEST_SECRET, bearer_auth, make_user
from tests.test_requests import DEMAND_PAYLOAD, SUPPLY_PAYLOAD


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False)
class ExploreApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.viewer = make_user(telegram_user_id=98001, first_name="Leila")
        self.sender = make_user(telegram_user_id=98002, first_name="Sara")
        self.traveler = make_user(
            telegram_user_id=98003,
            first_name="Ali",
            telegram_username="ali_carry",
        )
        self.demand = create_item_request(self.sender, DEMAND_PAYLOAD)
        self.supply = create_item_request(
            self.traveler,
            {**SUPPLY_PAYLOAD, "origin_city": "mashhad"},
        )

    def test_open_request_facets_come_from_live_data(self) -> None:
        facets = open_request_facets(self.viewer)
        self.assertEqual(set(facets["origins"]), {("IR", "tehran"), ("IR", "mashhad")})
        self.assertEqual(set(facets["destinations"]), {("CA", "toronto")})
        self.assertEqual(facets["category_codes"], {"CLOTHES", "DOCUMENTS", "PERSONAL_ITEMS"})

        supply_only = open_request_facets(self.viewer, "SUPPLY")
        self.assertEqual(set(supply_only["origins"]), {("IR", "mashhad")})
        self.assertEqual(supply_only["category_codes"], {"CLOTHES", "DOCUMENTS", "PERSONAL_ITEMS"})

        demand_only = open_request_facets(self.viewer, "DEMAND")
        self.assertEqual(set(demand_only["origins"]), {("IR", "tehran")})
        self.assertEqual(demand_only["category_codes"], {"CLOTHES"})

    def test_open_request_facets_include_viewer_custom_city(self) -> None:
        create_item_request(self.viewer, {**DEMAND_PAYLOAD, "origin_city": "Bandar"})
        facets = open_request_facets(self.viewer)
        self.assertIn(("IR", "bandar"), set(facets["origins"]))
        self.assertIn(("CA", "toronto"), set(facets["destinations"]))

    def test_unauthenticated_cannot_browse(self) -> None:
        response = self.client.get("/api/explore/")
        self.assertEqual(response.status_code, 401)

    def test_list_excludes_self_and_hides_telegram(self) -> None:
        own = create_item_request(
            self.viewer,
            {**DEMAND_PAYLOAD, "destination_city": "vancouver"},
        )
        response = self.client.get("/api/explore/", **bearer_auth(self.viewer))
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        ids = {row["id"] for row in rows}
        self.assertIn(self.demand.id, ids)
        self.assertIn(self.supply.id, ids)
        self.assertNotIn(own.id, ids)
        payload = str(rows)
        self.assertNotIn("ali_carry", payload)
        for row in rows:
            self.assertNotIn("telegram_user_id", row)
            self.assertNotIn("telegram_username", row)
        demand_row = next(row for row in rows if row["id"] == self.demand.id)
        self.assertEqual(demand_row["owner_first_name"], "Sara")
        self.assertEqual(demand_row["type"], "DEMAND")
        self.assertEqual(demand_row["description"], "Personal clothes")

    def test_filters_type_route_category_and_dates(self) -> None:
        other_demand = create_item_request(
            self.sender,
            {
                **DEMAND_PAYLOAD,
                "origin_city": "mashhad",
                "destination_city": "vancouver",
                "desired_date": "2027-11-01",
                "item_category_codes": ["MEDICINE"],
            },
        )
        auth = bearer_auth(self.viewer)

        by_type = self.client.get("/api/explore/?type=SUPPLY", **auth)
        self.assertEqual({row["id"] for row in by_type.json()}, {self.supply.id})

        by_origin = self.client.get("/api/explore/?origin_country=IR&origin_city=tehran", **auth)
        self.assertEqual({row["id"] for row in by_origin.json()}, {self.demand.id})

        by_dest = self.client.get(
            "/api/explore/?destination_country=CA&destination_city=vancouver",
            **auth,
        )
        self.assertEqual({row["id"] for row in by_dest.json()}, {other_demand.id})

        by_category = self.client.get("/api/explore/?category=MEDICINE", **auth)
        self.assertEqual({row["id"] for row in by_category.json()}, {other_demand.id})

        by_categories = self.client.get(
            "/api/explore/?category=CLOTHES&category=MEDICINE",
            **auth,
        )
        self.assertEqual(
            {row["id"] for row in by_categories.json()},
            {self.demand.id, other_demand.id, self.supply.id},
        )

        by_dates = self.client.get("/api/explore/?date_from=2027-11-01&date_to=2027-11-15", **auth)
        self.assertEqual({row["id"] for row in by_dates.json()}, {other_demand.id})

        by_weight = self.client.get("/api/explore/?type=SUPPLY&weight_kg=4", **auth)
        self.assertEqual({row["id"] for row in by_weight.json()}, {self.supply.id})
        too_heavy = self.client.get("/api/explore/?type=SUPPLY&weight_kg=20", **auth)
        self.assertEqual(too_heavy.json(), [])

        by_flight = self.client.get("/api/explore/?type=SUPPLY&flight_after=2027-09-10", **auth)
        self.assertEqual({row["id"] for row in by_flight.json()}, {self.supply.id})
        after_flight = self.client.get("/api/explore/?type=SUPPLY&flight_after=2027-09-11", **auth)
        self.assertEqual(after_flight.json(), [])

        extra_stop = create_item_request(
            self.traveler,
            {
                **SUPPLY_PAYLOAD,
                "origin_city": "mashhad",
                "destination_city": "toronto",
                "destination_cities": [
                    {"country": "CA", "city": "toronto"},
                    {"country": "CA", "city": "vancouver"},
                ],
            },
        )
        by_extra_dest = self.client.get(
            "/api/explore/?type=SUPPLY&destination_country=CA&destination_city=vancouver",
            **auth,
        )
        self.assertEqual({row["id"] for row in by_extra_dest.json()}, {extra_stop.id})

    def test_cancelled_requests_are_hidden(self) -> None:
        cancel_item_request(self.demand)
        response = self.client.get("/api/explore/", **bearer_auth(self.viewer))
        ids = {row["id"] for row in response.json()}
        self.assertNotIn(self.demand.id, ids)
        self.assertIn(self.supply.id, ids)
        self.assertEqual(self.demand.status, RequestStatus.CANCELLED)

    def test_connect_requires_opposite_request(self) -> None:
        response = self.client.post(
            f"/api/explore/{self.supply.id}/connect/",
            {},
            format="json",
            **bearer_auth(self.viewer),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("my_request_id", response.json())

    def test_connect_creates_override_match_and_shows_contact(self) -> None:
        mine = create_item_request(self.viewer, DEMAND_PAYLOAD)
        response = self.client.post(
            f"/api/explore/{self.supply.id}/connect/",
            {"my_request_id": mine.id},
            format="json",
            **bearer_auth(self.viewer),
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["status"], MatchStatus.ACCEPTED)
        counterpart = payload["counterpart"]
        self.assertEqual(counterpart["telegram_username"], "ali_carry")
        self.assertTrue(counterpart["telegram_url"].startswith("https://t.me/ali_carry"))
        self.assertEqual(Match.objects.filter(demand_request=mine, supply_request=self.supply).count(), 1)

    def test_connect_returns_existing_visible_match(self) -> None:
        matching_supply = create_item_request(
            make_user(telegram_user_id=98004, first_name="Omar"),
            SUPPLY_PAYLOAD,
        )
        mine = create_item_request(self.viewer, DEMAND_PAYLOAD)
        existing = Match.objects.get(demand_request=mine, supply_request=matching_supply)

        response = self.client.post(
            f"/api/explore/{matching_supply.id}/connect/",
            {},
            format="json",
            **bearer_auth(self.viewer),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["id"], existing.id)
        self.assertEqual(Match.objects.filter(demand_request=mine).count(), 1)

    def test_connect_requires_choice_when_multiple_opposite_requests(self) -> None:
        create_item_request(self.viewer, DEMAND_PAYLOAD)
        create_item_request(
            self.viewer,
            {**DEMAND_PAYLOAD, "destination_city": "vancouver"},
        )
        response = self.client.post(
            f"/api/explore/{self.supply.id}/connect/",
            {},
            format="json",
            **bearer_auth(self.viewer),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("my_request_id", response.json())

    def test_cannot_connect_to_own_request(self) -> None:
        mine = create_item_request(self.viewer, DEMAND_PAYLOAD)
        response = self.client.post(
            f"/api/explore/{mine.id}/connect/",
            {},
            format="json",
            **bearer_auth(self.viewer),
        )
        self.assertEqual(response.status_code, 404)
