"""Sending sign-in codes by email. Ready only when an SMTP host and a sender (WEB_EMAIL_FROM) are configured."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger("web")

SUBJECT = "کد ورود چمدون: {code}"
BODY = "کد ورود شما به چمدون: {code}\n\nاین کد ۱۰ دقیقه اعتبار دارد. اگر شما درخواست نداده‌اید، این ایمیل را نادیده بگیرید.\n\nChamedoon sign-in code: {code} (valid 10 minutes)."


def ready() -> bool:
    backend = getattr(settings, "EMAIL_BACKEND", "")
    if backend.endswith("locmem.EmailBackend"):
        return True  # tests
    return bool(settings.EMAIL_HOST and settings.WEB_EMAIL_FROM)


def send_code(email: str, code: str) -> bool:
    try:
        send_mail(SUBJECT.format(code=code), BODY.format(code=code), settings.WEB_EMAIL_FROM or settings.DEFAULT_FROM_EMAIL, [email])
        return True
    except Exception:  # noqa: BLE001
        logger.exception("web_email_send_failed")
        return False


def notify(email: str, subject: str, body: str) -> bool:
    """A plain notice to an email-only account (where Telegram accounts get a bot message)."""
    if not ready() or not email:
        return False
    try:
        send_mail(subject, body, settings.WEB_EMAIL_FROM or settings.DEFAULT_FROM_EMAIL, [email])
        return True
    except Exception:  # noqa: BLE001
        logger.exception("web_email_notify_failed")
        return False
