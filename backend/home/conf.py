"""Effective Chamedoon Home configuration: the admin settings row first, then the server environment."""

from __future__ import annotations

from django.conf import settings

from home.models import HomeSettings

MAX_LOOKBACK_DAYS = 30
MAX_REVIEW_BATCH = 100
MAX_MEDIA_BATCH = 60


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
    if step == "publish":
        return current.publish_enabled
    if step == "alerts":
        return current.alerts_enabled
    return True


def lookback_days() -> int:
    return max(1, min(MAX_LOOKBACK_DAYS, row().lookback_days or MAX_LOOKBACK_DAYS))


def review_batch() -> int:
    return max(1, min(MAX_REVIEW_BATCH, row().review_batch or 60))


def media_batch() -> int:
    return max(1, min(MAX_MEDIA_BATCH, row().media_batch or 30))


def review_model() -> str:
    return row().review_model.strip() or _env("HOME_REVIEW_MODEL") or "gpt-5-mini"


def review_workers() -> int:
    return max(1, min(16, row().review_workers or 8))


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


def bot_token() -> str:
    """The housing bot's token: Home settings first, then HOME_BOT_TOKEN. Never the courier bot's token."""
    return row().bot_token.strip() or _env("HOME_BOT_TOKEN")


def bot_username() -> str:
    """Username of the housing bot, for the Mini App links under each post."""
    return (row().bot_username.strip() or _env("HOME_BOT_USERNAME") or "chamedoon_home_bot").lstrip("@")


def mini_app_link(listing_id: int | None = None) -> str:
    start = f"home_{listing_id}" if listing_id else "home"
    return f"https://t.me/{bot_username()}?startapp={start}"


def publish_batch() -> int:
    return max(1, min(10, row().publish_batch or 5))


def publish_window_days() -> int:
    return max(1, min(30, row().publish_window_days or 3))


def publish_daily_cap() -> int:
    return max(1, min(200, row().publish_daily_cap or 40))


def publish_phone() -> bool:
    return row().publish_phone


def publish_blocker() -> str:
    """Why posting cannot run now, or an empty string."""
    if not home_enabled():
        return "Chamedoon Home is switched off in Home settings"
    if not row().publish_enabled:
        return "publishing is switched off in Home settings"
    if not bot_token():
        return "the housing bot token is not set in Home settings"
    from home.models import HomeChannel

    if not HomeChannel.objects.filter(enabled=True).exists():
        return "no housing channel is switched on"
    return ""


def publish_ready() -> bool:
    return not publish_blocker()


def alerts_daily_cap() -> int:
    return max(1, min(50, row().alerts_daily_cap or 10))


def webhook_secret() -> str:
    """Secret Telegram echoes in X-Telegram-Bot-Api-Secret-Token: Home settings first, then HOME_WEBHOOK_SECRET."""
    return row().webhook_secret.strip() or _env("HOME_WEBHOOK_SECRET")


def alert_start_link(country: str = "", city: str = "") -> str:
    """Deep link that opens the alert setup in the housing bot, city pre-filled: ?start=alert_home_<cc>_<city>."""
    from home.alerts import city_slug

    parts = ["alert_home"]
    if country:
        parts.append(country.lower())
        if city:
            parts.append(city_slug(city))
    return f"https://t.me/{bot_username()}?start={'_'.join(parts)}"


def alerts_blocker() -> str:
    """Why alerts cannot go out now, or an empty string."""
    if not home_enabled():
        return "Chamedoon Home is switched off in Home settings"
    if not row().alerts_enabled:
        return "alerts are switched off in Home settings"
    if not bot_token():
        return "the housing bot token is not set in Home settings"
    return ""
