"""Courier intro notices by email, for accounts that signed in with email only (no Telegram chat to message).

Off unless `HomeSettings.email_login_on` is on and a mail sender is configured (`web.mail.ready`). A notice carries the
route and a link to the web app, never the other person's name or contact: contact still waits for a two-sided intro.
Sent after the database commit, so a rolled-back intro never sends mail.
"""

from __future__ import annotations

import logging

from django.db import transaction

from users.identity import is_email_id

logger = logging.getLogger("courier")

APP_URL = "https://chamedoon.bigde.li/app/"
SIGN = "\n\nچمدون، باربری\nChamedoon courier"


def email_target(user) -> str | None:
    """The verified address to notify instead of Telegram, or None (Telegram account, flag off, or no sender)."""
    if user is None or not is_email_id(user.telegram_user_id):
        return None
    if not (user.email and user.email_verified_at):
        return None
    from home import conf as home_conf
    from web import mail

    if not home_conf.email_login_on() or not mail.ready():
        return None
    return user.email


def _later(email: str, subject: str, body: str) -> None:
    def deliver() -> None:
        from web import mail

        if not mail.notify(email, subject, body + SIGN):
            logger.warning("courier_email_notice_failed")

    transaction.on_commit(deliver)


def intro_requested(intro, route: str) -> bool:
    email = email_target(intro.target)
    if not email:
        return False
    _later(
        email,
        "چمدون: درخواست معرفی تازه",
        f"کسی برای {route} درخواست معرفی فرستاده است.\nبرای قبول یا رد، وارد چمدون شوید:\n{APP_URL}me/intros/{intro.pk}/\n\n"
        f"Someone asked to be introduced for {route}. Open Chamedoon to accept or decline.",
    )
    return True


def intro_answered(intro, route: str, *, accepted: bool) -> bool:
    email = email_target(intro.requester)
    if not email:
        return False
    if accepted:
        text = f"درخواست معرفی شما برای {route} قبول شد.\nراه تماس را در چمدون ببینید:\n{APP_URL}me/intros/{intro.pk}/\n\nYour intro for {route} was accepted."
        subject = "چمدون: معرفی قبول شد"
    else:
        text = f"معرفی برای {route} این بار انجام نشد. مسافرها و بسته‌های دیگر را ببینید:\n{APP_URL}\n\nYour intro for {route} did not go ahead this time."
        subject = "چمدون: معرفی انجام نشد"
    _later(email, subject, text)
    return True
