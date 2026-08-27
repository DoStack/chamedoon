from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from django.conf import settings
from django.test import override_settings
from rest_framework.test import APITestCase
import jwt

from users.models import User
from users.tokens import issue_access_token

TEST_BOT_TOKEN = "test-bot-token"


def build_init_data(
    *,
    telegram_user_id: int,
    first_name: str,
    last_name: str | None = None,
    username: str | None = None,
    auth_date: int | None = None,
    bot_token: str = TEST_BOT_TOKEN,
    tamper_hash: bool = False,
) -> str:
    user: dict[str, object] = {"id": telegram_user_id, "first_name": first_name}
    if last_name is not None:
        user["last_name"] = last_name
    if username is not None:
        user["username"] = username

    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAEAAQ",
        "user": json.dumps(user, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if tamper_hash:
        digest = "0" * 64
    fields["hash"] = digest
    return urlencode(fields)


@override_settings(
    TELEGRAM_BOT_TOKEN=TEST_BOT_TOKEN,
    DEBUG=False,
    SECRET_KEY="test-secret-key-that-is-long-enough-for-hs256-ok",
)
class TelegramAuthTests(APITestCase):
    def test_valid_init_data_creates_user_and_returns_token(self) -> None:
        init_data = build_init_data(
            telegram_user_id=1001,
            first_name="Sara",
            last_name="Karimi",
            username="sara_k",
        )

        response = self.client.post(
            "/api/auth/telegram/",
            {"init_data": init_data},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("token", payload)
        self.assertEqual(payload["user"]["telegram_user_id"], 1001)
        self.assertEqual(payload["user"]["telegram_username"], "sara_k")
        self.assertEqual(payload["user"]["first_name"], "Sara")
        self.assertEqual(User.objects.filter(telegram_user_id=1001).count(), 1)

    def test_username_is_not_identity_same_telegram_id_updates_username(self) -> None:
        User.objects.create(
            telegram_user_id=2002,
            telegram_username="old_name",
            first_name="Ali",
        )
        init_data = build_init_data(
            telegram_user_id=2002,
            first_name="Ali",
            username="new_name",
        )

        response = self.client.post(
            "/api/auth/telegram/",
            {"init_data": init_data},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(telegram_user_id=2002).count(), 1)
        user = User.objects.get(telegram_user_id=2002)
        self.assertEqual(user.telegram_username, "new_name")
        self.assertEqual(response.json()["user"]["id"], user.id)

    def test_same_username_different_telegram_ids_are_different_users(self) -> None:
        first = build_init_data(telegram_user_id=3001, first_name="A", username="shared")
        second = build_init_data(telegram_user_id=3002, first_name="B", username="shared")

        self.client.post("/api/auth/telegram/", {"init_data": first}, format="json")
        self.client.post("/api/auth/telegram/", {"init_data": second}, format="json")

        self.assertEqual(User.objects.filter(telegram_username="shared").count(), 2)
        self.assertEqual(User.objects.filter(telegram_user_id=3001).count(), 1)
        self.assertEqual(User.objects.filter(telegram_user_id=3002).count(), 1)

    def test_invalid_hash_is_rejected(self) -> None:
        init_data = build_init_data(
            telegram_user_id=4001,
            first_name="Nima",
            tamper_hash=True,
        )

        response = self.client.post(
            "/api/auth/telegram/",
            {"init_data": init_data},
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(User.objects.count(), 0)

    def test_expired_init_data_is_rejected(self) -> None:
        init_data = build_init_data(
            telegram_user_id=4002,
            first_name="Nima",
            auth_date=int(time.time()) - 90_000,
        )

        response = self.client.post(
            "/api/auth/telegram/",
            {"init_data": init_data},
            format="json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(User.objects.count(), 0)

    def test_me_requires_authentication(self) -> None:
        response = self.client.get("/api/me/")
        self.assertEqual(response.status_code, 401)

    def test_me_returns_authenticated_user_only(self) -> None:
        user = User.objects.create(
            telegram_user_id=5001,
            telegram_username="me_user",
            first_name="Leila",
        )
        other = User.objects.create(
            telegram_user_id=5002,
            telegram_username="other",
            first_name="Omar",
        )
        token = issue_access_token(user)

        response = self.client.get("/api/me/", HTTP_AUTHORIZATION=f"Bearer {token}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["id"], user.id)
        self.assertEqual(payload["telegram_user_id"], 5001)
        self.assertEqual(payload["first_name"], "Leila")
        self.assertNotEqual(payload["id"], other.id)

    def test_token_cannot_impersonate_another_telegram_id(self) -> None:
        user = User.objects.create(
            telegram_user_id=6001,
            first_name="Owner",
        )
        User.objects.create(
            telegram_user_id=6002,
            first_name="Victim",
        )
        token = issue_access_token(user)
        stolen = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        stolen["telegram_user_id"] = 6002
        tampered = jwt.encode(stolen, settings.SECRET_KEY, algorithm="HS256")

        response = self.client.get("/api/me/", HTTP_AUTHORIZATION=f"Bearer {tampered}")
        self.assertEqual(response.status_code, 401)

    def test_inactive_user_cannot_authenticate(self) -> None:
        user = User.objects.create(
            telegram_user_id=7001,
            first_name="Closed",
            is_active=False,
        )
        token = issue_access_token(user)

        me_response = self.client.get("/api/me/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(me_response.status_code, 401)

        init_data = build_init_data(telegram_user_id=7001, first_name="Closed")
        auth_response = self.client.post(
            "/api/auth/telegram/",
            {"init_data": init_data},
            format="json",
        )
        self.assertEqual(auth_response.status_code, 403)

    def test_dev_user_is_rejected_when_debug_is_false(self) -> None:
        response = self.client.post(
            "/api/auth/telegram/",
            {
                "dev_user": {
                    "telegram_user_id": 8001,
                    "first_name": "Dev",
                }
            },
            format="json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(User.objects.count(), 0)

    @override_settings(
        TELEGRAM_BOT_TOKEN=TEST_BOT_TOKEN,
        DEBUG=True,
        SECRET_KEY="test-secret-key-that-is-long-enough-for-hs256-ok",
    )
    def test_dev_user_works_when_debug_is_true(self) -> None:
        response = self.client.post(
            "/api/auth/telegram/",
            {
                "dev_user": {
                    "telegram_user_id": 8002,
                    "first_name": "Dev",
                    "telegram_username": "localdev",
                }
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["telegram_user_id"], 8002)
        self.assertTrue(response.json()["token"])
