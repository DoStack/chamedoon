from __future__ import annotations

from django.test import override_settings
from rest_framework.test import APITestCase

from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from tests.helpers import TEST_SECRET, make_user
from tests.test_requests import DEMAND_PAYLOAD
from users.models import User

BOT_SECRET = "test-bot-secret"


@override_settings(SECRET_KEY=TEST_SECRET, BOT_SERVICE_SECRET=BOT_SECRET, DEBUG=False)
class BotApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def test_sync_user_requires_bot_secret(self) -> None:
        response = self.client.post(
            "/api/bot/sync-user/",
            {"telegram_user_id": 95001, "first_name": "Sara"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_sync_user_upserts_by_telegram_id_not_username(self) -> None:
        headers = {"HTTP_X_BOT_SECRET": BOT_SECRET}
        first = self.client.post(
            "/api/bot/sync-user/",
            {
                "telegram_user_id": 95002,
                "first_name": "Sara",
                "telegram_username": "old_name",
            },
            format="json",
            **headers,
        )
        second = self.client.post(
            "/api/bot/sync-user/",
            {
                "telegram_user_id": 95002,
                "first_name": "Sara",
                "telegram_username": "new_name",
            },
            format="json",
            **headers,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(User.objects.filter(telegram_user_id=95002).count(), 1)
        self.assertEqual(User.objects.get(telegram_user_id=95002).telegram_username, "new_name")

    def test_summary_returns_request_and_match_counts(self) -> None:
        user = make_user(telegram_user_id=95003, first_name="Leila")
        create_item_request(user, DEMAND_PAYLOAD)
        response = self.client.get(
            "/api/bot/users/95003/summary/",
            HTTP_X_BOT_SECRET=BOT_SECRET,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["request_count"], 1)
        self.assertEqual(payload["active_request_count"], 1)
        self.assertEqual(payload["match_count"], 0)

    def test_bot_can_create_demand_request(self) -> None:
        make_user(telegram_user_id=95004, first_name="Sara")
        response = self.client.post(
            "/api/bot/requests/",
            {
                "telegram_user_id": 95004,
                **DEMAND_PAYLOAD,
            },
            format="json",
            HTTP_X_BOT_SECRET=BOT_SECRET,
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["type"], "DEMAND")
        self.assertEqual(response.json()["origin_city"], "tehran")
