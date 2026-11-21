"""Secrets of browser sign-in. Stored only as keyed hashes (HMAC-SHA256 with SECRET_KEY)."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from django.conf import settings


def hash_secret(value: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), (value or "").encode(), hashlib.sha256).hexdigest()


def new_code() -> str:
    """192 bits, URL-safe ([A-Za-z0-9_-]), 32 characters: `weblogin_` + code is 41 of Telegram's 64."""
    return secrets.token_urlsafe(24)


def new_poll_token() -> str:
    return secrets.token_urlsafe(32)


def new_match() -> int:
    return 10 + secrets.randbelow(90)


def new_email_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def same(value: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_secret(value), stored_hash or "")
