"""Courier intro notices by email for email-only accounts, behind `email_login_on` (off by default)."""

from __future__ import annotations

from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from courier import intros
from courier.models import CourierIntro, CourierSettings, IntroStatus
from home.models import HomeSettings
from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from tests.helpers import TEST_SECRET, make_user
from tests.test_courier_api import package_payload, trip_payload
from users.identity import new_email_id

EMAIL = "traveler@example.org"


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False, TELEGRAM_CHANNEL_ENABLED=False)
class IntroEmailTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        seed_catalog()
        CourierSettings.objects.filter(pk=1).delete()
        CourierSettings.objects.create(pk=1, courier_v2=True, intros_enabled=True)
        row = HomeSettings.load()
        row.web_login_on = True
        row.email_login_on = True
        row.save()
        patcher = patch("notifications.telegram.call_telegram_api", return_value={"ok": True, "result": {"message_id": 1}})
        self.telegram = patcher.start()
        self.addCleanup(patcher.stop)
        self.mailer = make_user(telegram_user_id=new_email_id(), first_name="Mina", email=EMAIL, email_verified_at=timezone.now())
        self.sara = make_user(telegram_user_id=7001, first_name="Sara", telegram_username="sara_secret")
        self.trip = create_item_request(self.mailer, trip_payload(), sync_channel=False)
        self.package = create_item_request(self.sara, package_payload(), sync_channel=False)

    def ask(self, requester, target_request):
        with self.captureOnCommitCallbacks(execute=True):
            return intros.request_intro(requester, target_request)

    def test_ask_to_email_only_target_goes_by_email(self) -> None:
        reply = self.ask(self.sara, self.trip)
        self.assertEqual(reply.code, "intro_sent")
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [EMAIL])
        intro = CourierIntro.objects.get()
        self.assertIn(f"/app/me/intros/{intro.pk}/", message.body)
        self.assertNotIn("sara_secret", message.body)
        self.assertNotIn("Sara", message.body)
        self.assertEqual(reply.others, [])

    def test_answer_to_email_only_requester_goes_by_email(self) -> None:
        self.ask(self.mailer, self.package)  # the target (Sara) gets a bot message, not mail
        self.assertEqual(mail.outbox, [])
        intro = CourierIntro.objects.get()
        with self.captureOnCommitCallbacks(execute=True):
            intros.respond(intro.pk, self.sara, accept=True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [EMAIL])
        self.assertIn("قبول شد", mail.outbox[0].body)
        self.assertNotIn("sara_secret", mail.outbox[0].body)

    def test_decline_mail(self) -> None:
        self.ask(self.mailer, self.package)
        intro = CourierIntro.objects.get()
        with self.captureOnCommitCallbacks(execute=True):
            intros.respond(intro.pk, self.sara, accept=False)
        self.assertEqual(CourierIntro.objects.get().status, IntroStatus.DECLINED)
        self.assertIn("انجام نشد", mail.outbox[0].subject)

    def test_flag_off_sends_nothing(self) -> None:
        HomeSettings.objects.update(email_login_on=False)
        reply = self.ask(self.sara, self.trip)
        self.assertEqual(reply.code, "intro_unreachable")
        self.assertEqual(mail.outbox, [])
        self.assertFalse(CourierIntro.objects.exists())

    def test_unverified_email_is_not_reachable(self) -> None:
        type(self.mailer).objects.filter(pk=self.mailer.pk).update(email_verified_at=None)
        self.trip = type(self.trip).objects.get(pk=self.trip.pk)
        self.assertEqual(self.ask(self.sara, self.trip).code, "intro_unreachable")
        self.assertEqual(mail.outbox, [])

    def test_no_sender_configured_sends_nothing(self) -> None:
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="", WEB_EMAIL_FROM=""):
            self.assertEqual(self.ask(self.sara, self.trip).code, "intro_unreachable")
        self.assertEqual(mail.outbox, [])

    def test_never_calls_telegram_for_email_ids(self) -> None:
        self.ask(self.sara, self.trip)
        for call in self.telegram.call_args_list:
            payload = call.args[1] if len(call.args) > 1 else call.kwargs.get("payload", {})
            self.assertNotEqual(payload.get("chat_id"), self.mailer.telegram_user_id)

    def test_telegram_accounts_unchanged(self) -> None:
        reply = self.ask(self.mailer, self.package)
        self.assertEqual(reply.code, "intro_sent")
        self.assertEqual(len(reply.others), 1)
        self.assertEqual(mail.outbox, [])
