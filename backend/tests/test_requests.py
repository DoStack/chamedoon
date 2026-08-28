from __future__ import annotations

from django.test import override_settings
from rest_framework.test import APITestCase

from item_requests.models import City, ItemRequest, RequestStatus, RequestType
from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from tests.helpers import TEST_SECRET, bearer_auth, make_user

DEMAND_PAYLOAD = {
    "type": "DEMAND",
    "origin_country": "IR",
    "origin_city": "tehran",
    "destination_country": "CA",
    "destination_city": "toronto",
    "desired_date": "2027-09-07",
    "weight_kg": "2.00",
    "item_category_codes": ["CLOTHES"],
    "description": "Personal clothes",
}

SUPPLY_PAYLOAD = {
    "type": "SUPPLY",
    "origin_country": "IR",
    "origin_city": "tehran",
    "destination_country": "CA",
    "destination_city": "toronto",
    "flight_date": "2027-09-10",
    "date_from": "2027-09-01",
    "date_to": "2027-09-15",
    "capacity_kg": "5.00",
    "item_category_codes": ["CLOTHES", "DOCUMENTS", "PERSONAL_ITEMS"],
    "excluded_category_codes": ["CIGARETTES", "MEDICINE"],
    "description": "Can carry personal items",
}


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False)
class RequestApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.user = make_user(telegram_user_id=91001, first_name="Sara")
        self.other = make_user(telegram_user_id=91002, first_name="Ali")

    def test_create_demand_request(self) -> None:
        response = self.client.post(
            "/api/requests/",
            DEMAND_PAYLOAD,
            format="json",
            **bearer_auth(self.user),
        )

        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(payload["type"], RequestType.DEMAND)
        self.assertEqual(payload["origin_city"], "tehran")
        self.assertEqual(payload["destination_city"], "toronto")
        self.assertEqual(payload["weight_kg"], "2.00")
        self.assertIsNone(payload["capacity_kg"])
        self.assertEqual(payload["item_category_codes"], ["CLOTHES"])
        self.assertEqual(payload["status"], RequestStatus.ACTIVE)
        self.assertEqual(payload["desired_date"], "2027-09-07")
        self.assertIsNone(payload["flight_date"])
        self.assertEqual(payload["date_from"], "2027-09-07")
        self.assertEqual(payload["date_to"], "2027-09-07")
        self.assertEqual(ItemRequest.objects.filter(user=self.user, type=RequestType.DEMAND).count(), 1)

    def test_create_supply_request_with_exclusions(self) -> None:
        response = self.client.post(
            "/api/requests/",
            SUPPLY_PAYLOAD,
            format="json",
            **bearer_auth(self.user),
        )

        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(payload["type"], RequestType.SUPPLY)
        self.assertEqual(payload["capacity_kg"], "5.00")
        self.assertIsNone(payload["weight_kg"])
        self.assertCountEqual(
            payload["item_category_codes"],
            ["CLOTHES", "DOCUMENTS", "PERSONAL_ITEMS"],
        )
        self.assertCountEqual(payload["excluded_category_codes"], ["CIGARETTES", "MEDICINE"])
        self.assertEqual(payload["flight_date"], "2027-09-10")
        self.assertIsNone(payload["desired_date"])
        self.assertEqual(payload["date_from"], "2027-09-01")
        self.assertEqual(payload["date_to"], "2027-09-15")

    def test_demand_requires_weight_and_category(self) -> None:
        missing_weight = {**DEMAND_PAYLOAD}
        missing_weight.pop("weight_kg")
        response = self.client.post(
            "/api/requests/",
            missing_weight,
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("weight_kg", response.json())

        missing_category = {**DEMAND_PAYLOAD, "item_category_codes": []}
        response = self.client.post(
            "/api/requests/",
            missing_category,
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("item_category_codes", response.json())

    def test_supply_requires_capacity(self) -> None:
        payload = {**SUPPLY_PAYLOAD}
        payload.pop("capacity_kg")
        response = self.client.post(
            "/api/requests/",
            payload,
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("capacity_kg", response.json())

    def test_demand_cannot_include_exclusions_or_capacity(self) -> None:
        payload = {**DEMAND_PAYLOAD, "excluded_category_codes": ["MEDICINE"], "capacity_kg": "5"}
        response = self.client.post(
            "/api/requests/",
            payload,
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ItemRequest.objects.count(), 0)

    def test_supply_cannot_carry_and_exclude_the_same_category(self) -> None:
        payload = {
            **SUPPLY_PAYLOAD,
            "item_category_codes": ["CLOTHES", "DOCUMENTS"],
            "excluded_category_codes": ["CLOTHES", "MEDICINE"],
        }
        response = self.client.post(
            "/api/requests/",
            payload,
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("excluded_category_codes", response.json())
        self.assertEqual(ItemRequest.objects.count(), 0)

    def test_supply_needs_at_least_one_carry_category(self) -> None:
        payload = {**SUPPLY_PAYLOAD, "item_category_codes": []}
        response = self.client.post(
            "/api/requests/",
            payload,
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("item_category_codes", response.json())
        self.assertEqual(ItemRequest.objects.count(), 0)

    def test_custom_city_is_created_and_reused(self) -> None:
        payload = {**DEMAND_PAYLOAD, "origin_city": "Rasht"}
        response = self.client.post(
            "/api/requests/",
            payload,
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["origin_city"], "rasht")
        self.assertTrue(City.objects.filter(slug="rasht", country__code="IR").exists())
        again = self.client.post(
            "/api/requests/",
            {**SUPPLY_PAYLOAD, "destination_city": "rasht", "destination_country": "IR", "origin_city": "toronto", "origin_country": "CA"},
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(again.status_code, 201, again.content)
        self.assertEqual(City.objects.filter(slug="rasht", country__code="IR").count(), 1)

    def test_blank_city_is_rejected(self) -> None:
        payload = {**DEMAND_PAYLOAD, "origin_city": " "}
        response = self.client.post(
            "/api/requests/",
            payload,
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("origin_city", response.json())
        self.assertEqual(ItemRequest.objects.count(), 0)

    def test_demand_accepts_legacy_date_from(self) -> None:
        payload = {**DEMAND_PAYLOAD}
        payload.pop("desired_date")
        payload["date_from"] = "2027-09-08"
        response = self.client.post(
            "/api/requests/",
            payload,
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["desired_date"], "2027-09-08")

    def test_demand_cannot_include_flight_date(self) -> None:
        payload = {**DEMAND_PAYLOAD, "flight_date": "2027-09-10"}
        response = self.client.post(
            "/api/requests/",
            payload,
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("flight_date", response.json())

    def test_supply_requires_flight_date_and_window(self) -> None:
        payload = {**SUPPLY_PAYLOAD}
        payload.pop("flight_date")
        response = self.client.post(
            "/api/requests/",
            payload,
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("flight_date", response.json())

    def test_supply_cannot_include_desired_date(self) -> None:
        payload = {**SUPPLY_PAYLOAD, "desired_date": "2027-09-07"}
        response = self.client.post(
            "/api/requests/",
            payload,
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("desired_date", response.json())

    def test_date_range_must_be_ordered(self) -> None:
        payload = {**SUPPLY_PAYLOAD, "date_from": "2027-09-15", "date_to": "2027-09-01"}
        response = self.client.post(
            "/api/requests/",
            payload,
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("date_to", response.json())

    def test_same_origin_and_destination_are_rejected(self) -> None:
        payload = {
            **DEMAND_PAYLOAD,
            "destination_country": "IR",
            "destination_city": "tehran",
        }
        response = self.client.post(
            "/api/requests/",
            payload,
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(response.status_code, 400)

    def test_list_returns_only_own_requests(self) -> None:
        self.client.post("/api/requests/", DEMAND_PAYLOAD, format="json", **bearer_auth(self.user))
        self.client.post("/api/requests/", SUPPLY_PAYLOAD, format="json", **bearer_auth(self.other))

        response = self.client.get("/api/requests/", **bearer_auth(self.user))
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], RequestType.DEMAND)

    def test_cannot_view_or_edit_another_users_request(self) -> None:
        created = self.client.post(
            "/api/requests/",
            DEMAND_PAYLOAD,
            format="json",
            **bearer_auth(self.other),
        )
        request_id = created.json()["id"]

        detail = self.client.get(f"/api/requests/{request_id}/", **bearer_auth(self.user))
        self.assertEqual(detail.status_code, 404)

        patch = self.client.patch(
            f"/api/requests/{request_id}/",
            {"description": "hacked"},
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(patch.status_code, 404)

        cancel = self.client.post(f"/api/requests/{request_id}/cancel/", **bearer_auth(self.user))
        self.assertEqual(cancel.status_code, 404)

    def test_owner_can_edit_and_cancel_active_request(self) -> None:
        created = self.client.post(
            "/api/requests/",
            DEMAND_PAYLOAD,
            format="json",
            **bearer_auth(self.user),
        )
        request_id = created.json()["id"]

        patched = self.client.patch(
            f"/api/requests/{request_id}/",
            {"weight_kg": "3.50", "description": "Winter clothes"},
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual(patched.json()["weight_kg"], "3.50")
        self.assertEqual(patched.json()["description"], "Winter clothes")

        cancelled = self.client.post(
            f"/api/requests/{request_id}/cancel/",
            **bearer_auth(self.user),
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], RequestStatus.CANCELLED)

        edit_again = self.client.patch(
            f"/api/requests/{request_id}/",
            {"description": "nope"},
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(edit_again.status_code, 400)

    def test_unauthenticated_cannot_create_requests(self) -> None:
        response = self.client.post("/api/requests/", DEMAND_PAYLOAD, format="json")
        self.assertEqual(response.status_code, 401)

    def test_categories_and_locations_are_public(self) -> None:
        categories = self.client.get("/api/categories/")
        self.assertEqual(categories.status_code, 200)
        codes = {item["code"] for item in categories.json()}
        self.assertIn("CLOTHES", codes)
        self.assertIn("MEDICINE", codes)

        locations = self.client.get("/api/locations/")
        self.assertEqual(locations.status_code, 200)
        countries = locations.json()["countries"]
        codes = [item["code"] for item in countries]
        self.assertEqual(codes[:2], ["IR", "CA"])
        names = {item["code"]: item["name_en"] for item in countries}
        rest = codes[2:]
        self.assertEqual(rest, sorted(rest, key=lambda code: names[code].casefold()))
        iran = next(item for item in countries if item["code"] == "IR")
        city_slugs = {city["slug"] for city in iran["cities"]}
        self.assertIn("tehran", city_slugs)
        self.assertEqual(
            [city["slug"] for city in iran["cities"]],
            ["isfahan", "mashhad", "shiraz", "tabriz", "tehran"],
        )
        create_item_request(self.user, {**DEMAND_PAYLOAD, "origin_city": "mashhad"})
        create_item_request(self.user, {**SUPPLY_PAYLOAD, "origin_city": "mashhad"})
        ranked = self.client.get("/api/locations/").json()["countries"]
        iran_ranked = next(item for item in ranked if item["code"] == "IR")
        self.assertEqual(iran_ranked["cities"][0]["slug"], "mashhad")
        canada = next(item for item in locations.json()["countries"] if item["code"] == "CA")
        canada_slugs = {city["slug"] for city in canada["cities"]}
        self.assertIn("ottawa", canada_slugs)
        self.assertIn("calgary", canada_slugs)
        austria = next(item for item in locations.json()["countries"] if item["code"] == "AT")
        self.assertIn("vienna", {city["slug"] for city in austria["cities"]})
        italy = next(item for item in locations.json()["countries"] if item["code"] == "IT")
        italy_slugs = {city["slug"] for city in italy["cities"]}
        self.assertIn("milan", italy_slugs)
        self.assertIn("rome", italy_slugs)
