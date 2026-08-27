from __future__ import annotations

from django.test import Client, override_settings
from rest_framework.test import APITestCase

from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from miniapp.auth import SESSION_USER_KEY
from tests.helpers import TEST_SECRET, make_user
from tests.test_requests import DEMAND_PAYLOAD, SUPPLY_PAYLOAD


def _login(client: Client, user) -> None:
    session = client.session
    session[SESSION_USER_KEY] = user.pk
    session.save()


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=True)
class MiniAppTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.user = make_user(telegram_user_id=99001, first_name="Leila")
        self.other = make_user(telegram_user_id=99002, first_name="Ali")

    def test_landing_and_login_redirect(self) -> None:
        landing = self.client.get("/")
        self.assertEqual(landing.status_code, 200)
        home = self.client.get("/app/")
        self.assertEqual(home.status_code, 302)
        self.assertIn("/app/login/", home["Location"])

    @override_settings(DEBUG=False, TELEGRAM_BOT_USERNAME="CB_koolbarbot")
    def test_production_login_asks_to_open_telegram(self) -> None:
        landing = self.client.get("/")
        self.assertContains(landing, "https://t.me/CB_koolbarbot/app")
        page = self.client.get("/app/login/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Open Koolbar in Telegram")
        self.assertContains(page, "https://t.me/CB_koolbarbot/app")

    def test_debug_login_sets_session(self) -> None:
        response = self.client.post(
            "/app/login/",
            {"telegram_user_id": "42", "first_name": "Dev"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/app/")
        self.assertTrue(self.client.session.get(SESSION_USER_KEY))

    def test_create_demand_from_html_form(self) -> None:
        _login(self.client, self.user)
        response = self.client.post(
            "/app/demand/new/",
            {
                "origin_country": "IR",
                "origin_city": "tehran",
                "destination_country": "CA",
                "destination_city": "toronto",
                "date_from": "2027-09-01",
                "date_to": "2027-09-15",
                "weight_kg": "2",
                "item_category_codes": ["CLOTHES"],
                "description": "Bag",
            },
        )
        self.assertEqual(response.status_code, 302, response.content)
        self.assertRegex(response["Location"], r"^/app/requests/\d+/$")
        listing = self.client.get("/app/requests/")
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "Tehran")

    def test_explore_and_propose_match(self) -> None:
        create_item_request(self.user, DEMAND_PAYLOAD)
        supply = create_item_request(self.other, {**SUPPLY_PAYLOAD, "origin_city": "mashhad"})
        _login(self.client, self.user)
        page = self.client.get("/app/explore/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Ali")
        connect = self.client.post("/app/explore/", {"request_id": str(supply.id)})
        self.assertEqual(connect.status_code, 302, connect.content)
        self.assertRegex(connect["Location"], r"^/app/matches/\d+/$")
        detail = self.client.get(connect["Location"])
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Accept")
