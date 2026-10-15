"""Effective Chamedoon Home configuration: the admin settings row first, then the server environment."""

from __future__ import annotations

from django.conf import settings

from home.models import HomeSettings

MAX_LOOKBACK_DAYS = 30
MAX_REVIEW_BATCH = 40
MAX_MEDIA_BATCH = 20


def row() -> HomeSettings:
    return HomeSettings.load()


def _env(name: str, default: str = "") -> str:
    return (getattr(settings, name, default) or default).strip()


def home_enabled() -> bool:
    return bool(getattr(settings, "HOME_ENABLED", True)) and row().enabled


def step_enabled(step: str) -> bool:
    current = row()
    if not (getattr(settings, "HOME_ENABLED", True) and current.enabled):
        return False
    if step == "ingest":
        return current.ingest_enabled
    if step == "review":
        return current.review_enabled
    if step == "media":
        return current.media_enabled
    if step == "vision":
        return current.vision_enabled or bool(getattr(settings, "HOME_VISION_ENABLED", False))
    return True


def lookback_days() -> int:
    return max(1, min(MAX_LOOKBACK_DAYS, row().lookback_days or MAX_LOOKBACK_DAYS))


def review_batch() -> int:
    return max(1, min(MAX_REVIEW_BATCH, row().review_batch or 15))


def media_batch() -> int:
    return max(1, min(MAX_MEDIA_BATCH, row().media_batch or 12))


def vision_model() -> str:
    return row().vision_model.strip() or _env("HOME_VISION_MODEL")


def storage_values() -> dict:
    """Backend plus every R2 value, the admin row winning over the environment."""
    current = row()
    return {
        "backend": current.media_backend.strip() or _env("HOME_MEDIA_BACKEND").lower(),
        "account_id": current.r2_account_id.strip() or _env("R2_ACCOUNT_ID"),
        "access_key": current.r2_access_key_id.strip() or _env("R2_ACCESS_KEY_ID"),
        "secret_key": current.r2_secret_access_key.strip() or _env("R2_SECRET_ACCESS_KEY"),
        "bucket": current.r2_bucket.strip() or _env("HOME_MEDIA_BUCKET"),
        "public_base": current.r2_public_base.strip() or _env("HOME_MEDIA_PUBLIC_BASE"),
    }


def missing_storage_values() -> list[str]:
    values = storage_values()
    if values["backend"] != "r2":
        return []
    labels = {
        "account_id": "R2 account id",
        "access_key": "R2 access key id",
        "secret_key": "R2 secret access key",
        "bucket": "R2 bucket",
        "public_base": "R2 public URL",
    }
    return [label for key, label in labels.items() if not values[key]]
