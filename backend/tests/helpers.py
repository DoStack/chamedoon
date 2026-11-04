from __future__ import annotations

import re

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


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}\S*)?$")
_HASH = re.compile(r"\b(?=[0-9]*[a-f])[0-9a-f]{8,}\b")
NL = chr(10)


def readable_text(body) -> str:
    """Every key and value of a JSON body, one per line. Dates and content hashes (in a value or in a URL) stay out, since a contact cannot hide
    in them and any digits can turn up there by chance. Short numbers stay out for the same reason: an id or a price."""
    if isinstance(body, dict):
        return NL.join(part for key, value in body.items() for part in (str(key), readable_text(value)))
    if isinstance(body, list):
        return NL.join(readable_text(value) for value in body)
    if isinstance(body, str):
        return "" if _DATE.match(body) else _HASH.sub("", body)
    if isinstance(body, int) and not isinstance(body, bool) and abs(body) >= 10**7:
        return str(body)
    return ""
