"""courier_intro_everywhere: on, only an accepted intro connects two people. Off, nothing changes."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.utils import timezone

from courier import conf, intros, search
from courier.models import CourierIntro, CourierSettings, IntroStatus
from item_requests.explore import public_open_requests_queryset
from item_requests.models import UNMATCHABLE_INTRO_SIDE, ItemRequest, RequestStatus
from item_requests.services import create_item_request, update_item_request
from matching.completion import complete_match
from matching.contact import contact_for_match
from matching.manual import IntroInstead, propose_user_match, set_match_status
from matching.models import Match, MatchStatus
from matching.services import sync_matches_for_request
from miniapp.auth import SESSION_USER_KEY
from notifications.services import notify_connected, notify_new_match
from tests.helpers import bearer_auth, make_user
from tests.test_courier_api import SHADOW_ID, ApiCase, days, package_payload, trip_payload

DELIVERED = ({"ok": True, "result": {"message_id": 1}}, {})
BLOCKED = (None, {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"})
DOWN = (None, {"ok": False, "error_code": 502, "description": "Bad Gateway"})
TIMEOUT = (None, {"description": "network error: timed out"})


class IntroCase(ApiCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = patch("courier.intros.courier_bot.call_telegram_api_detailed", return_value=DELIVERED)
        self.delivery = patcher.start()
        self.addCleanup(patcher.stop)

    def switch_on(self) -> None:
        row = CourierSettings.load()
        row.courier_intro_everywhere = True
        row.save()

    def login(self, user) -> None:
        session = self.client.session
        session[SESSION_USER_KEY] = user.pk
        session.save()

    def asked(self):
        """Everything the bot was made to send, by either way of sending."""
        return self.telegram.call_count + self.delivery.call_count


class FlagOffTests(IntroCase):
    def test_saving_a_request_still_connects_and_notifies(self) -> None:
        trip = self.trip(flight_date=days(20), date_to=days(20))
        self.telegram.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            package = self.package()
        match = Match.objects.get(demand_request=package, supply_request=trip)
        self.assertEqual(match.status, MatchStatus.CONNECTED)
        self.assertGreaterEqual(self.telegram.call_count, 1)
        self.assertEqual(contact_for_match(match, self.sara)["telegram_username"], "omar_secret")

    def test_propose_still_connects(self) -> None:
        trip = self.trip()
        mine = create_item_request(self.sara, package_payload(), sync_channel=False, sync_matches=False)
        match = propose_user_match(self.sara, trip, mine)
        self.assertEqual(match.status, MatchStatus.CONNECTED)
        self.assertFalse(CourierIntro.objects.exists())

    def test_accepting_an_intro_still_creates_no_match(self) -> None:
        trip = self.trip()
        intro = CourierIntro.objects.create(requester=self.sara, target=self.omar, target_request=trip)
        intros.respond(intro.pk, self.omar, accept=True)
        self.assertEqual(Match.objects.count(), 0)
        self.assertFalse(ItemRequest.objects.filter(user=self.sara).exists())

    def test_a_failed_send_still_leaves_the_intro_pending(self) -> None:
        trip = self.trip()
        self.telegram.return_value = None
        response = self.post(f"/requests/{trip.pk}/intro/")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(CourierIntro.objects.get().status, IntroStatus.PENDING)
        self.delivery.assert_not_called()

    def test_bot_ask_still_leaves_the_send_to_the_caller(self) -> None:
        trip = self.trip()
        reply = intros.request_intro(self.sara, trip)
        self.assertEqual(len(reply.others), 1)
        self.delivery.assert_not_called()

    def test_a_post_with_no_way_to_reach_is_still_found_by_the_bot_search(self) -> None:
        ghost = make_user(telegram_user_id=SHADOW_ID + 7, first_name="X")
        silent = create_item_request(ghost, trip_payload(), imported=True, sync_channel=False)
        self.assertIn(silent, search.route_search(role="DEMAND", origin=("CA", "toronto"), destination=("IR", "tehran")))


class NoNewMatchTests(IntroCase):
    def setUp(self) -> None:
        super().setUp()
        self.trip_row = self.trip(flight_date=days(20), date_to=days(20))
        self.switch_on()
        self.telegram.reset_mock()

    def test_saving_a_request_connects_nobody_and_tells_nobody(self) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            package = self.package()
            sync_matches_for_request(package)
        self.assertEqual(Match.objects.count(), 0)
        self.telegram.assert_not_called()
        self.assertEqual(search.counterparts(package), [self.trip_row])

    def test_propose_sends_an_intro_and_creates_nothing_else(self) -> None:
        with self.captureOnCommitCallbacks(execute=True), self.assertRaises(IntroInstead) as raised:
            propose_user_match(self.sara, self.trip_row)
        self.assertEqual(raised.exception.reply.code, "intro_sent")
        self.assertEqual(Match.objects.count(), 0)
        self.assertFalse(ItemRequest.objects.filter(user=self.sara).exists())
        self.assertEqual(CourierIntro.objects.get().status, IntroStatus.PENDING)
        self.assertEqual(self.delivery.call_count, 1)
        self.assertEqual(self.delivery.call_args.args[1]["chat_id"], self.omar.telegram_user_id)
        self.assertNotIn("omar_secret", self.delivery.call_args.args[1]["text"])
        self.assertNotIn("sara_secret", self.delivery.call_args.args[1]["text"])

    def test_propose_still_refuses_own_and_closed_requests(self) -> None:
        with self.assertRaises(ValidationError):
            propose_user_match(self.omar, self.trip_row)
        ItemRequest.objects.filter(pk=self.trip_row.pk).update(status=RequestStatus.CLOSED)
        self.trip_row.refresh_from_db()
        with self.assertRaises(ValidationError):
            propose_user_match(self.sara, self.trip_row)
        self.assertEqual(self.asked(), 0)

    def test_old_api_says_what_happened_without_a_match(self) -> None:
        response = self.client.post(f"/api/explore/{self.trip_row.pk}/connect/", content_type="application/json", **bearer_auth(self.sara))
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["code"], "intro_sent")
        self.assertNotIn("omar_secret", response.content.decode())
        self.assertEqual(Match.objects.count(), 0)


class ExistingMatchTests(IntroCase):
    def old_match(self) -> Match:
        with self.captureOnCommitCallbacks(execute=True):
            trip = self.trip(flight_date=days(20), date_to=days(20))
            package = self.package()
        match = Match.objects.get(demand_request=package, supply_request=trip)
        self.switch_on()
        self.telegram.reset_mock()
        return match

    def test_a_match_from_before_the_switch_is_not_rewritten_and_keeps_its_contact(self) -> None:
        match = self.old_match()
        before = Match.objects.filter(pk=match.pk).values().get()
        match.refresh_from_db()
        self.assertEqual(contact_for_match(match, self.sara)["telegram_username"], "omar_secret")
        self.assertEqual(propose_user_match(self.sara, match.supply_request).pk, match.pk)
        self.assertEqual(Match.objects.filter(pk=match.pk).values().get(), before)
        self.assertEqual(self.asked(), 0)

    def test_its_page_still_opens(self) -> None:
        match = self.old_match()
        self.login(self.sara)
        response = self.client.get(f"/app/matches/{match.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("omar_secret", response.content.decode())


class ContactFollowsAcceptanceTests(IntroCase):
    def later_match(self, status: str = MatchStatus.CONNECTED) -> Match:
        self.switch_on()
        trip = self.trip()
        package = self.package()
        self.telegram.reset_mock()
        return Match.objects.create(demand_request=package, supply_request=trip, score=80, status=status, initiated_by=self.sara)

    def test_a_later_match_with_no_acceptance_returns_no_contact(self) -> None:
        match = self.later_match()
        self.assertIsNone(contact_for_match(match, self.sara))
        self.assertIsNone(contact_for_match(match, self.omar))

    def test_it_returns_contact_after_acceptance(self) -> None:
        match = self.later_match()
        CourierIntro.objects.create(requester=self.sara, target=self.omar, target_request=match.supply_request, status=IntroStatus.ACCEPTED)
        self.assertEqual(contact_for_match(match, self.sara)["telegram_username"], "omar_secret")

    def test_notifications_do_not_carry_contact_without_acceptance(self) -> None:
        match = self.later_match()
        notify_connected(match)
        notify_new_match(match)
        self.telegram.assert_not_called()

    def test_an_admin_status_change_cannot_reveal_contact(self) -> None:
        match = self.later_match(status=MatchStatus.EXPIRED)
        with self.captureOnCommitCallbacks(execute=True):
            set_match_status(match, MatchStatus.CONNECTED)
        self.telegram.assert_not_called()
        self.assertIsNone(contact_for_match(match, self.sara))

    def test_match_page_routes_to_the_request_and_shows_nothing(self) -> None:
        match = self.later_match()
        self.login(self.sara)
        response = self.client.get(f"/app/matches/{match.pk}/")
        self.assertRedirects(response, f"/app/explore/{match.supply_request_id}/", fetch_redirect_response=False)
        self.assertNotIn("omar_secret", response.content.decode())
        ItemRequest.objects.filter(pk=match.supply_request_id).update(status=RequestStatus.CLOSED)
        self.assertRedirects(self.client.get(f"/app/matches/{match.pk}/"), "/app/matches/", fetch_redirect_response=False)
        self.assertRedirects(self.client.post(f"/app/matches/{match.pk}/", {"action": "complete"}), "/app/matches/", fetch_redirect_response=False)
        match.refresh_from_db()
        self.assertEqual(match.status, MatchStatus.CONNECTED)


class AcceptTests(IntroCase):
    def setUp(self) -> None:
        super().setUp()
        self.switch_on()
        self.trip_row = self.trip()
        self.telegram.reset_mock()

    def ask(self) -> CourierIntro:
        started = intros.request_intro(self.sara, self.trip_row)
        self.assertEqual(started.code, "intro_sent")
        return CourierIntro.objects.get(requester=self.sara, target_request=self.trip_row)

    def test_accept_connects_with_the_requesters_own_request_and_sends_only_the_intro_messages(self) -> None:
        package = self.package()
        intro = self.ask()
        self.assertEqual(intro.requester_request, package)
        reply = intros.respond(intro.pk, self.omar, accept=True)
        match = Match.objects.get(demand_request=package, supply_request=self.trip_row)
        self.assertEqual(match.status, MatchStatus.CONNECTED)
        self.assertEqual(match.initiated_by, self.sara)
        self.assertEqual(len(reply.others), 1)
        self.telegram.assert_not_called()
        self.assertEqual(contact_for_match(match, self.sara)["telegram_username"], "omar_secret")
        self.assertEqual(contact_for_match(match, self.omar)["telegram_username"], "sara_secret")

    def test_accept_with_no_own_request_makes_a_hidden_one_that_can_finish(self) -> None:
        intro = self.ask()
        with self.captureOnCommitCallbacks(execute=True):
            intros.respond(intro.pk, self.omar, accept=True)
        side = ItemRequest.objects.get(user=self.sara)
        self.assertEqual(side.unmatchable_reason, UNMATCHABLE_INTRO_SIDE)
        self.assertFalse(public_open_requests_queryset().filter(pk=side.pk).exists())
        self.assertNotIn(side, search.route_search(role="SUPPLY", origin=("CA", "toronto"), destination=("IR", "tehran")))
        self.assert_error(self.get(f"/requests/{side.pk}/", auth=bearer_auth(self.omar)), "not_found", "/requests/{id}/")
        self.telegram.assert_not_called()
        match = Match.objects.get(demand_request=side, supply_request=self.trip_row)
        self.assertEqual(complete_match(match, self.sara).status, MatchStatus.COMPLETED)

    def test_decline_connects_nobody(self) -> None:
        intro = self.ask()
        intros.respond(intro.pk, self.omar, accept=False)
        self.assertEqual(Match.objects.count(), 0)
        self.assertFalse(ItemRequest.objects.filter(user=self.sara).exists())

    def test_answer_after_the_request_closed_or_its_date_passed_connects_nobody(self) -> None:
        for change in ({"status": RequestStatus.CLOSED}, {"flight_date": timezone.localdate() - timedelta(days=1)}):
            with self.subTest(change=change):
                intro = self.ask()
                ItemRequest.objects.filter(pk=self.trip_row.pk).update(**change)
                reply = intros.respond(intro.pk, self.omar, accept=True)
                self.assertEqual(reply.text, "این درخواست دیگه فعال نیست.")
                self.assertEqual(reply.others, [])
                self.assertEqual(Match.objects.count(), 0)
                intro.delete()
                ItemRequest.objects.filter(pk=self.trip_row.pk).update(status=RequestStatus.ACTIVE, flight_date=days(12))

    def test_an_edit_that_breaks_the_rules_keeps_an_accepted_match(self) -> None:
        intro = self.ask()
        intros.respond(intro.pk, self.omar, accept=True)
        match = Match.objects.get()
        payload = trip_payload(capacity_kg="1.00", item_category_codes=["DOCUMENTS"])
        update_item_request(self.trip_row, payload)
        match.refresh_from_db()
        self.assertEqual(match.status, MatchStatus.CONNECTED)

    def test_accept_keeps_an_ended_match_as_it_was(self) -> None:
        package = self.package()
        ended = Match.objects.create(demand_request=package, supply_request=self.trip_row, score=80, status=MatchStatus.EXPIRED, initiated_by=self.sara)
        intro = self.ask()
        intros.respond(intro.pk, self.omar, accept=True)
        ended.refresh_from_db()
        self.assertEqual(ended.status, MatchStatus.EXPIRED)
        self.assertEqual(self.get(f"/me/intros/{intro.pk}/").json()["contact"]["telegram_username"], "omar_secret")

    def test_no_match_possible_still_accepts_the_intro(self) -> None:
        ItemRequest.objects.filter(pk=self.trip_row.pk).update(capacity_kg=None)
        self.trip_row.refresh_from_db()
        intro = self.ask()
        intros.respond(intro.pk, self.omar, accept=True)
        intro.refresh_from_db()
        self.assertEqual(intro.status, IntroStatus.ACCEPTED)
        self.assertEqual(Match.objects.count(), 0)

    def test_api_answer_connects_and_opens_the_match(self) -> None:
        package = self.package()
        created = self.post(f"/requests/{self.trip_row.pk}/intro/")
        self.assertEqual(created.status_code, 201, created.content)
        answered = self.post(f"/me/intros/{created.json()['id']}/respond/", {"accept": True}, auth=bearer_auth(self.omar))
        self.assertEqual(answered.status_code, 200, answered.content)
        match = Match.objects.get(demand_request=package)
        self.assertEqual(answered.json()["match_id"], match.pk)
        self.assertEqual(self.get(f"/me/matches/{match.pk}/").json()["contact"]["telegram_username"], "omar_secret")


class UndeliveredTests(IntroCase):
    def setUp(self) -> None:
        super().setUp()
        self.switch_on()
        self.trip_row = self.trip()
        self.telegram.reset_mock()

    def test_a_blocked_bot_ends_at_once_with_one_try(self) -> None:
        self.delivery.return_value = BLOCKED
        self.assert_error(self.post(f"/requests/{self.trip_row.pk}/intro/"), "intro_undelivered", "/requests/{id}/intro/")
        self.assertEqual(self.delivery.call_count, 1)
        self.assertEqual(CourierIntro.objects.get().status, IntroStatus.UNDELIVERED)

    def test_server_errors_and_timeouts_get_two_retries_then_end(self) -> None:
        for failure in (DOWN, TIMEOUT):
            with self.subTest(failure=failure):
                CourierIntro.objects.all().delete()
                self.delivery.reset_mock()
                self.delivery.return_value = failure
                self.assert_error(self.post(f"/requests/{self.trip_row.pk}/intro/"), "intro_undelivered", "/requests/{id}/intro/")
                self.assertEqual(self.delivery.call_count, 3)

    def _timed_out_but_landed(self) -> CourierIntro:
        self.delivery.return_value = TIMEOUT
        self.assert_error(self.post(f"/requests/{self.trip_row.pk}/intro/"), "intro_undelivered", "/requests/{id}/intro/")
        self.assertEqual(self.delivery.call_count, 3)
        intro = CourierIntro.objects.get()
        self.assertEqual(intro.status, IntroStatus.UNDELIVERED)
        return intro

    def test_three_timeouts_where_the_first_landed_and_the_target_accepts(self) -> None:
        intro = self._timed_out_but_landed()
        reply = intros.respond(intro.pk, intro.target, accept=True)
        self.assertIn("وصل شدید", reply.text)
        intro.refresh_from_db()
        self.assertEqual(intro.status, IntroStatus.ACCEPTED)
        self.assertIsNotNone(intro.answered_at)
        match = Match.objects.get(supply_request=self.trip_row)
        self.assertEqual(match.status, MatchStatus.CONNECTED)
        self.assertEqual(len(reply.others), 1)
        self.assertEqual(self.get(f"/requests/{self.trip_row.pk}/").json()["intro_status"], IntroStatus.ACCEPTED)

    def test_three_timeouts_where_the_first_landed_and_the_target_declines(self) -> None:
        intro = self._timed_out_but_landed()
        reply = intros.respond(intro.pk, intro.target, accept=False)
        self.assertIn("ثبت شد", reply.text)
        intro.refresh_from_db()
        self.assertEqual(intro.status, IntroStatus.DECLINED)
        self.assertFalse(Match.objects.exists())

    def test_a_landed_ask_answers_for_as_long_as_a_pending_one(self) -> None:
        intro = self._timed_out_but_landed()
        CourierIntro.objects.filter(pk=intro.pk).update(created_at=timezone.now() - timedelta(hours=conf.INTRO_EXPIRE_HOURS + 1))
        reply = intros.respond(intro.pk, intro.target, accept=True)
        self.assertIn("منقضی", reply.text)
        intro.refresh_from_db()
        self.assertEqual(intro.status, IntroStatus.UNDELIVERED)
        self.assertFalse(Match.objects.exists())

    def test_a_retry_that_arrives_is_a_sent_intro(self) -> None:
        self.delivery.side_effect = [DOWN, DELIVERED]
        self.assertEqual(self.post(f"/requests/{self.trip_row.pk}/intro/").status_code, 201)
        self.assertEqual(CourierIntro.objects.get().status, IntroStatus.PENDING)

    def test_it_does_not_count_against_the_daily_caps(self) -> None:
        row = CourierSettings.load()
        row.intro_daily_limit = 1
        row.save()
        self.delivery.return_value = BLOCKED
        self.post(f"/requests/{self.trip_row.pk}/intro/")
        self.delivery.return_value = DELIVERED
        other_trip = self.trip(make_user(telegram_user_id=7201, first_name="Lia"), flight_date=days(13), date_to=days(13))
        self.assertEqual(self.post(f"/requests/{other_trip.pk}/intro/").status_code, 201)

    def test_asking_again_after_the_target_returns_sends_again(self) -> None:
        self.delivery.return_value = BLOCKED
        self.post(f"/requests/{self.trip_row.pk}/intro/")
        self.delivery.return_value = DELIVERED
        response = self.post(f"/requests/{self.trip_row.pk}/intro/")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(CourierIntro.objects.get().status, IntroStatus.PENDING)

    def test_the_target_never_sees_it(self) -> None:
        self.delivery.return_value = BLOCKED
        self.post(f"/requests/{self.trip_row.pk}/intro/")
        intro = CourierIntro.objects.get()
        omar = bearer_auth(self.omar)
        self.assert_error(self.get(f"/me/intros/{intro.pk}/", auth=omar), "not_found", "/me/intros/{id}/")
        self.assert_error(self.post(f"/me/intros/{intro.pk}/respond/", {"accept": True}, auth=omar), "not_found", "/me/intros/{id}/respond/")
        self.assertEqual(self.get(f"/me/intros/{intro.pk}/").json()["status"], IntroStatus.UNDELIVERED)

    def test_the_bot_says_so(self) -> None:
        self.delivery.return_value = BLOCKED
        reply = intros.request_intro(self.sara, self.trip_row)
        self.assertEqual(reply.code, "intro_undelivered")
        self.assertEqual(reply.others, [])
        self.assertEqual(reply.text, "پیام به ایشان نرسید. مسافرها و بسته‌های دیگر همین مسیر را ببینید.")


class ImportedPostTests(IntroCase):
    def setUp(self) -> None:
        super().setUp()
        self.switch_on()
        self.ghost = make_user(telegram_user_id=SHADOW_ID + 8, first_name="Ghost", telegram_username="ghost_public")
        self.imported = create_item_request(self.ghost, trip_payload(), imported=True, source_url="https://t.me/somechannel/5", sync_channel=False)
        ItemRequest.objects.filter(pk=self.imported.pk).update(channel_message_id=5)
        self.telegram.reset_mock()

    def test_it_shows_what_the_author_published_and_sends_nothing(self) -> None:
        with self.assertRaises(IntroInstead) as raised:
            propose_user_match(self.sara, self.imported)
        page = intros.reply_for_page(raised.exception.reply)
        self.assertEqual(page["code"], "intro_unreachable")
        self.assertIn("@ghost_public", page["detail"])
        self.assertEqual(page["links"], [{"text": "🔗 آگهی اصلی در کانال", "url": "https://t.me/somechannel/5"}])
        self.assertFalse(CourierIntro.objects.exists())
        self.assertFalse(Match.objects.exists())
        self.assertEqual(self.asked(), 0)

    def test_the_miniapp_page_shows_it_too(self) -> None:
        self.login(self.sara)
        response = self.client.post(f"/app/explore/{self.imported.pk}/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("@ghost_public", body)
        self.assertIn("https://t.me/somechannel/5", body)
        self.assertFalse(Match.objects.exists())

    def test_with_nothing_published_it_is_not_found_by_search(self) -> None:
        silent_user = make_user(telegram_user_id=SHADOW_ID + 9, first_name="X")
        silent = create_item_request(silent_user, trip_payload(flight_date=days(14), date_to=days(14)), imported=True, sync_channel=False)
        rows = search.route_search(role="DEMAND", origin=("CA", "toronto"), destination=("IR", "tehran"))
        self.assertNotIn(silent, rows)
        self.assertIn(self.imported, rows)

    def test_once_the_author_joins_the_normal_intro_applies(self) -> None:
        User = type(self.ghost)
        User.objects.filter(pk=self.ghost.pk).update(telegram_user_id=7301)
        self.imported.refresh_from_db()
        reply = intros.request_intro(self.sara, self.imported)
        self.assertEqual(reply.code, "intro_sent")
