from __future__ import annotations

from users.models import User
from users.tokens import issue_access_token

TEST_SECRET = "test-secret-key-that-is-long-enough-for-hs256-ok"


def make_user(*, telegram_user_id: int, first_name: str = "Test", **kwargs) -> User:
    return User.objects.create(
        telegram_user_id=telegram_user_id,
        first_name=first_name,
        **kwargs,
    )


def bearer_auth(user: User) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {issue_access_token(user)}"}
