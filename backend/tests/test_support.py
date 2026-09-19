from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User as StaffUser
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from matching.models import Match, MatchStatus
from miniapp.auth import SESSION_USER_KEY, startapp_from_request, startapp_path
from support.models import ClosedBy, SenderType, SupportTicket, TicketStatus
from support.services import add_admin_message, add_user_message, auto_close_stale_tickets, create_ticket
from tests.helpers import TEST_SECRET, bearer_auth, make_user
from tests.test_requests import DEMAND_PAYLOAD, SUPPLY_PAYLOAD


def _login(client: Client, user) -> None:
    session = client.session
    session[SESSION_USER_KEY] = user.pk
    session.save()


def _list(client: Client, path: str):
    return client.get(path, HTTP_X_KOOLBAR_LIST="1")


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False, TELEGRAM_BOT_TOKEN="", TELEGRAM_BOT_USERNAME="CB_koolbarbot")
class SupportApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.user = make_user(telegram_user_id=88001, first_name="Leila")
        self.other = make_user(telegram_user_id=88002, first_name="Ali")
        self.staff = StaffUser.objects.create_superuser("support-admin", "admin@example.com", "secret")

    def test_create_ticket_is_open_and_owned_by_auth_user(self) -> None:
        created = self.client.post(
            "/api/support-tickets/",
            {"subject": "MATCH_PROBLEM", "message": "I cannot contact the person I matched with.", "user_id": self.other.pk},
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(created.status_code, 201, created.content)
        payload = created.json()
        self.assertEqual(payload["subject"], "MATCH_PROBLEM")
        self.assertEqual(payload["status"], TicketStatus.OPEN)
        self.assertEqual(len(payload["messages"]), 1)
        self.assertEqual(payload["messages"][0]["sender_type"], SenderType.USER)
        ticket = SupportTicket.objects.get(pk=payload["id"])
        self.assertEqual(ticket.user_id, self.user.pk)
        self.assertNotEqual(ticket.user_id, self.other.pk)

    def test_create_requires_subject_and_message(self) -> None:
        missing_subject = self.client.post(
            "/api/support-tickets/",
            {"message": "Need help"},
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(missing_subject.status_code, 400)
        missing_message = self.client.post(
            "/api/support-tickets/",
            {"subject": "OTHER"},
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(missing_message.status_code, 400)

    def test_user_sees_only_own_tickets_sorted_by_activity(self) -> None:
        older = create_ticket(self.user, {"subject": "OTHER", "message": "First"})
        newer = create_ticket(self.user, {"subject": "TECHNICAL_ISSUE", "message": "Second"})
        create_ticket(self.other, {"subject": "REPORT_USER", "message": "Not yours"})
        add_user_message(older, self.user, {"message": "Bump"})
        listing = self.client.get("/api/support-tickets/", **bearer_auth(self.user))
        self.assertEqual(listing.status_code, 200)
        ids = [row["id"] for row in listing.json()]
        self.assertEqual(ids, [older.pk, newer.pk])
        hidden = self.client.get(f"/api/support-tickets/{create_ticket(self.other, {'subject': 'OTHER', 'message': 'Secret'}).pk}/", **bearer_auth(self.user))
        self.assertEqual(hidden.status_code, 404)

    def test_user_reply_keeps_open_and_reopens_in_queue(self) -> None:
        ticket = create_ticket(self.user, {"subject": "CANNOT_CONTACT_USER", "message": "Cannot reach them"})
        open_reply = self.client.post(
            f"/api/support-tickets/{ticket.pk}/messages/",
            {"message": "Still waiting"},
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(open_reply.status_code, 200, open_reply.content)
        self.assertEqual(open_reply.json()["status"], TicketStatus.OPEN)
        add_admin_message(ticket, self.staff, {"message": "Please check your Telegram username."})
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.IN_QUEUE)
        queued_reply = self.client.post(
            f"/api/support-tickets/{ticket.pk}/messages/",
            {"message": "I checked it and still cannot contact them."},
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(queued_reply.status_code, 200, queued_reply.content)
        self.assertEqual(queued_reply.json()["status"], TicketStatus.OPEN)

    def test_user_can_close_and_cannot_message_or_reopen(self) -> None:
        ticket = create_ticket(self.user, {"subject": "OTHER", "message": "Close me"})
        closed = self.client.post(f"/api/support-tickets/{ticket.pk}/close/", **bearer_auth(self.user))
        self.assertEqual(closed.status_code, 200, closed.content)
        self.assertEqual(closed.json()["status"], TicketStatus.CLOSED)
        self.assertEqual(closed.json()["closed_by"], ClosedBy.USER)
        blocked = self.client.post(
            f"/api/support-tickets/{ticket.pk}/messages/",
            {"message": "Please reopen"},
            format="json",
            **bearer_auth(self.user),
        )
        self.assertEqual(blocked.status_code, 400)

    @patch("notifications.services.send_telegram_message", return_value=True)
    def test_admin_reply_sets_in_queue_and_notifies(self, mocked_send) -> None:
        ticket = create_ticket(self.user, {"subject": "MATCH_PROBLEM", "message": "Match issue"})
        add_admin_message(ticket, self.staff, {"message": "We are looking into this."})
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.IN_QUEUE)
        self.assertIsNotNone(ticket.last_admin_message_at)
        mocked_send.assert_called_once()
        self.assertEqual(mocked_send.call_args.args[0], self.user.telegram_user_id)
        self.assertIn("Chamedoon Support", mocked_send.call_args.args[1])
        self.assertIn("Match Problem", mocked_send.call_args.args[1])
        button = mocked_send.call_args.kwargs["reply_markup"]["inline_keyboard"][0][0]
        self.assertEqual(button["text"], "Open Support Ticket")
        self.assertIn(f"ticket_{ticket.pk}", button["url"])

    @patch("notifications.services.send_telegram_message", return_value=True)
    def test_auto_close_after_72_hours_without_user_reply(self, mocked_send) -> None:
        waiting = create_ticket(self.user, {"subject": "OTHER", "message": "Help"})
        add_admin_message(waiting, self.staff, {"message": "Any update?"})
        waiting.last_admin_message_at = timezone.now() - timedelta(hours=73)
        waiting.save(update_fields=["last_admin_message_at"])

        replied = create_ticket(self.other, {"subject": "OTHER", "message": "Help"})
        add_admin_message(replied, self.staff, {"message": "Please reply"})
        add_user_message(replied, self.other, {"message": "I replied"})
        replied.last_admin_message_at = timezone.now() - timedelta(hours=73)
        replied.save(update_fields=["last_admin_message_at"])

        fresh = create_ticket(self.user, {"subject": "TECHNICAL_ISSUE", "message": "Fresh"})
        add_admin_message(fresh, self.staff, {"message": "Wait"})

        mocked_send.reset_mock()
        closed = auto_close_stale_tickets()
        self.assertEqual(closed, 1)
        waiting.refresh_from_db()
        replied.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(waiting.status, TicketStatus.CLOSED)
        self.assertEqual(waiting.closed_by, ClosedBy.SYSTEM)
        self.assertEqual(replied.status, TicketStatus.OPEN)
        self.assertEqual(fresh.status, TicketStatus.IN_QUEUE)
        self.assertTrue(any("3 days" in call.args[1] for call in mocked_send.call_args_list))

    @override_settings(CRON_SECRET="cron-test-secret")
    def test_auto_close_cron_requires_secret(self) -> None:
        denied = self.client.get("/api/cron/auto-close-tickets/")
        self.assertEqual(denied.status_code, 403)
        response = self.client.get(
            "/api/cron/auto-close-tickets/",
            HTTP_AUTHORIZATION="Bearer cron-test-secret",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "closed": 0})


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=True)
class SupportMiniAppTests(APITestCase):
    def setUp(self) -> None:
        self.user = make_user(telegram_user_id=88101, first_name="Leila")
        self.other = make_user(telegram_user_id=88102, first_name="Ali")

    def test_home_has_support_entry(self) -> None:
        _login(self.client, self.user)
        home = self.client.get("/app/")
        self.assertContains(home, "Support")
        self.assertContains(home, 'href="/app/support/"')
        self.assertNotContains(home, 'data-count="support"')

    def test_home_counts_open_and_queued_support_tickets(self) -> None:
        create_ticket(self.user, {"subject": "OTHER", "message": "Need help"})
        queued = create_ticket(self.user, {"subject": "TECHNICAL_ISSUE", "message": "App issue"})
        closed = create_ticket(self.user, {"subject": "MATCH_PROBLEM", "message": "Old ticket"})
        queued.status = TicketStatus.IN_QUEUE
        queued.save(update_fields=["status"])
        closed.status = TicketStatus.CLOSED
        closed.save(update_fields=["status"])
        create_ticket(self.other, {"subject": "REPORT_USER", "message": "Not yours"})
        _login(self.client, self.user)
        home = self.client.get("/app/")
        self.assertContains(home, 'data-count="support">2</span>')
        self.assertNotContains(home, 'data-count="matches"')

    def test_user_can_create_list_reply_and_close_ticket(self) -> None:
        _login(self.client, self.user)
        empty = _list(self.client, "/app/support/")
        self.assertContains(empty, "You have no support tickets yet.")
        form = self.client.get("/app/support/new/")
        self.assertContains(form, "Create Support Ticket")
        self.assertContains(form, "Match Problem")
        created = self.client.post(
            "/app/support/new/",
            {"subject": "CANNOT_CONTACT_USER", "message": "I cannot contact the person I matched with."},
        )
        self.assertEqual(created.status_code, 302)
        ticket = SupportTicket.objects.get()
        self.assertEqual(created["Location"], f"/app/support/{ticket.pk}/?created=1")
        page = self.client.get(f"/app/support/{ticket.pk}/?created=1")
        self.assertContains(page, "Ticket created successfully.")
        self.assertContains(page, "Cannot Contact User")
        self.assertContains(page, "I cannot contact the person I matched with.")
        listing = _list(self.client, "/app/support/")
        self.assertContains(listing, "Cannot Contact User")
        self.assertContains(listing, "Open")
        replied = self.client.post(
            f"/app/support/{ticket.pk}/",
            {"action": "reply", "message": "Still cannot reach them."},
        )
        self.assertEqual(replied.status_code, 302)
        page = self.client.get(f"/app/support/{ticket.pk}/")
        self.assertContains(page, "Still cannot reach them.")
        self.assertContains(page, "Close Ticket")
        self.assertNotContains(page, "onsubmit")
        self.assertNotContains(page, "confirm(")
        closed = self.client.post(f"/app/support/{ticket.pk}/", {"action": "close"})
        self.assertEqual(closed.status_code, 302)
        page = self.client.get(f"/app/support/{ticket.pk}/")
        self.assertContains(page, "Closed")
        self.assertContains(page, "Create a new ticket")
        blocked = self.client.post(
            f"/app/support/{ticket.pk}/",
            {"action": "reply", "message": "Please reopen"},
        )
        self.assertContains(blocked, "Closed tickets cannot receive new messages.")

    def test_other_user_cannot_open_ticket(self) -> None:
        ticket = create_ticket(self.user, {"subject": "OTHER", "message": "Mine"})
        _login(self.client, self.other)
        hidden = self.client.get(f"/app/support/{ticket.pk}/")
        self.assertEqual(hidden.status_code, 404)

    def test_startapp_opens_support_ticket(self) -> None:
        self.assertEqual(startapp_path("support"), "/app/support/")
        self.assertEqual(startapp_path("ticket_42"), "/app/support/42/")
        request = type("Req", (), {"GET": {}, "POST": {}, "path": "/app/support/42/"})()
        self.assertEqual(startapp_from_request(request), "ticket_42")


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False)
class SupportAdminTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.user = make_user(telegram_user_id=88201, first_name="Leila", telegram_username="leila_send")
        self.other = make_user(telegram_user_id=88202, first_name="Omar", telegram_username="omar_travel")
        self.staff = StaffUser.objects.create_superuser("ops", "ops@example.com", "secret")
        self.admin = Client()
        self.admin.force_login(self.staff)

    def test_admin_list_filters_and_related_activity(self) -> None:
        create_item_request(self.user, DEMAND_PAYLOAD)
        create_item_request(self.other, SUPPLY_PAYLOAD)
        match = Match.objects.get()
        ticket = create_ticket(self.user, {"subject": "MATCH_PROBLEM", "message": "Need help with this match"})
        listing = self.admin.get(reverse("admin:support_supportticket_changelist"))
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "Support Tickets")
        self.assertContains(listing, "Leila")
        self.assertContains(listing, "Match Problem")
        open_only = self.admin.get(reverse("admin:support_supportticket_changelist") + "?status__exact=OPEN")
        self.assertContains(open_only, f">{ticket.pk}<")
        detail = self.admin.get(reverse("admin:support_supportticket_change", args=[ticket.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Need help with this match")
        self.assertContains(detail, "User Profile")
        self.assertContains(detail, "support-profile-modal")
        self.assertContains(detail, "support-send")
        self.assertContains(detail, "support-chip is-current is-open")
        self.assertContains(detail, str(self.user.telegram_user_id))
        self.assertContains(detail, "@leila_send")
        self.assertContains(detail, "Demand listings")
        self.assertContains(detail, reverse("admin:item_requests_itemrequest_change", args=[match.demand_request_id]))
        self.assertContains(detail, reverse("admin:matching_match_change", args=[match.pk]))
        self.assertContains(detail, "Connected matches")

    @patch("notifications.services.send_telegram_message", return_value=True)
    def test_admin_can_reply_and_close(self, _mocked_send) -> None:
        ticket = create_ticket(self.user, {"subject": "TECHNICAL_ISSUE", "message": "App is stuck"})
        url = reverse("admin:support_supportticket_change", args=[ticket.pk])
        replied = self.admin.post(url, {"support_action": "reply", "reply": "Please reopen the Mini App."})
        self.assertEqual(replied.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.IN_QUEUE)
        self.assertEqual(ticket.messages.filter(sender_type=SenderType.ADMIN).count(), 1)
        closed = self.admin.post(url, {"support_action": "close"})
        self.assertEqual(closed.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.CLOSED)
        self.assertEqual(ticket.closed_by, ClosedBy.ADMIN)
        reopened = self.admin.post(url, {"support_action": "OPEN"})
        self.assertEqual(reopened.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.CLOSED)
