from __future__ import annotations

from users.models import User


def telegram_dm_contact(user: User) -> dict:
    username = (user.telegram_username or "").strip() or None
    telegram_user_id = int(user.telegram_user_id)
    https_url = f"https://t.me/{username}" if username else None
    return {
        "first_name": user.first_name,
        "telegram_username": username,
        "telegram_user_id": telegram_user_id,
        "https_url": https_url,
        "telegram_url": https_url or f"tg://user?id={telegram_user_id}",
    }
