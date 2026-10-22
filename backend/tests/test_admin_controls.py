"""Operational controls in the admin: every switch and number changes what the code does."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User as StaffUser
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from api.admin_ops import RUN_JOBS, city_daily_counts, job_groups, run_job
from home.models import HomeAlert, HomeChannel, HomeListing, HomePost, HomePublication, HomeSource, PostStatus
from home.status import channels_with_recent_counts, home_status, sources_with_recent_counts
from item_requests.services import update_item_request
from outreach import conf
from outreach.accounts import account_gate, claim_for_account
from outreach.models import (
    OutreachAccount,
    OutreachMessage,
    OutreachOptOut,
    OutreachSettings,
    OutreachStatus,
)
from outreach.services import build_outreach_queue, claim_next_message, sending_gate
from tests.test_home import make_post, make_source
from tests.test_home_publish import de_listing
from tests.test_outreach import OutreachTestCase, _tehran_noon


def staff_client(test: TestCase) -> Client:
    StaffUser.objects.create_superuser("ops", "ops@example.com", "ops-pass")
    test.client.login(username="ops", password="ops-pass")
    return test.client


def set_outreach(**values) -> OutreachSettings:
    row = OutreachSettings.load()
    for name, value in values.items():
        setattr(row, name, value)
    row.save()
    return row


# ---------------------------------------------------------------- outreach settings


class OutreachConfTests(OutreachTestCase):
    def test_an_empty_row_behaves_like_the_environment(self) -> None:
        self.assertTrue(conf.building_enabled())
        self.assertTrue(conf.sending_enabled())
        self.assertEqual(conf.daily_limit(), 10)
        self.assertEqual(conf.min_score(), Decimal("80"))
        self.assertEqual(conf.send_window(), (10, 21))
        self.assertEqual(conf.max_post_age(), timedelta(days=5))
        self.assertEqual(conf.cooldown(), timedelta(days=14))
        effective = conf.effective()
        self.assertEqual(effective["daily_limit"], (10, "env"))
        self.assertEqual(effective["min_gap_minutes"], (0, "env"))

    @override_settings(OUTREACH_DAILY_LIMIT=10, OUTREACH_MIN_SCORE=80, OUTREACH_ENABLED=True)
    def test_the_admin_row_wins_over_the_environment(self) -> None:
        set_outreach(enabled=False, sending_enabled=False, daily_limit=3, min_score=95, send_start_hour=8, send_end_hour=23)
        self.assertFalse(conf.building_enabled())
        self.assertFalse(conf.sending_enabled())
        self.assertEqual(conf.daily_limit(), 3)
        self.assertEqual(conf.min_score(), Decimal("95"))
        self.assertEqual(conf.send_window(), (8, 23))
        self.assertEqual(conf.effective()["min_score"], (95, "admin"))

    def _weaker_match(self) -> None:
        update_item_request(self.demand, {"desired_date": "2027-08-10"})  # flight is a month later
        self.match.refresh_from_db()
        self.assertEqual(self.match.score, Decimal("85.26"))

    def test_threshold_is_read_from_the_admin_row(self) -> None:
        self._weaker_match()
        set_outreach(min_score=90)
        self.assertEqual(build_outreach_queue().as_dict()["created"], 0)
        set_outreach(min_score=85)
        self.assertEqual(build_outreach_queue().as_dict()["created"], 1)

    @override_settings(OUTREACH_MIN_SCORE=99)
    def test_a_lower_admin_threshold_overrides_a_strict_environment(self) -> None:
        self._weaker_match()
        self.assertEqual(build_outreach_queue().as_dict()["created"], 0)
        set_outreach(min_score=80)
        self.assertEqual(build_outreach_queue().as_dict()["created"], 1)

    def test_switches_and_window_gate_sending(self) -> None:
        self._queued()
        noon = _tehran_noon()
        set_outreach(sending_enabled=False)
        self.assertEqual(sending_gate(noon), "disabled")
        set_outreach(sending_enabled=True, send_start_hour=13, send_end_hour=20)
        self.assertEqual(sending_gate(noon), "outside_window")
        set_outreach(send_start_hour=None, send_end_hour=None, daily_limit=0)
        self.assertEqual(sending_gate(noon), "daily_limit")
        set_outreach(daily_limit=None)
        self.assertEqual(sending_gate(noon), "")

    def test_outreach_switch_stops_the_build_cron(self) -> None:
        set_outreach(enabled=False)
        self.assertEqual(run_job("outreach_build")["error"], "outreach disabled")
        set_outreach(enabled=True)
        self.assertEqual(run_job("outreach_build")["created"], 1)


# ---------------------------------------------------------------- sending accounts


class OutreachAccountTests(OutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        set_outreach(send_start_hour=0, send_end_hour=24)  # the API claims use the real clock

    def _claim(self, **body):
        return self.client.post("/api/outreach/claim/", body, format="json", **self._auth()).json()

    def test_without_an_account_the_claim_is_unchanged(self) -> None:
        queued = self._queued()
        claimed = self._claim()
        self.assertEqual(claimed["message"]["id"], queued.pk)
        queued.refresh_from_db()
        self.assertIsNone(queued.account)
        self.assertFalse(OutreachAccount.objects.exists())

    def test_a_named_account_is_registered_and_recorded(self) -> None:
        queued = self._queued()
        claimed = self._claim(account="Sina")
        self.assertEqual(claimed["message"]["id"], queued.pk)
        account = OutreachAccount.objects.get(name="sina")
        queued.refresh_from_db()
        self.assertEqual(queued.account, account)
        self.assertIsNotNone(account.last_sent_at)

    def test_per_account_limit_blocks_a_claim(self) -> None:
        self._queued()
        OutreachAccount.objects.create(name="sara", daily_limit=0)
        # 0 on the account row means no per-account limit; the default comes from settings.
        set_outreach(account_daily_limit=1)
        account = OutreachAccount.objects.get(name="sara")
        account.daily_limit = None
        account.save()
        noon = _tehran_noon()
        message, reason = claim_for_account("sara", noon)
        self.assertEqual(reason, "")
        message.status = OutreachStatus.FAILED
        message.save()
        self.assertEqual(claim_for_account("sara", noon + timedelta(hours=2)), (None, "account_daily_limit"))
        # Another account still has room under the total limit.
        self.assertEqual(account_gate(OutreachAccount.objects.create(name="sina"), noon), "")

    def test_the_account_row_overrides_the_default_limit(self) -> None:
        set_outreach(account_daily_limit=5)
        account = OutreachAccount.objects.create(name="sara", daily_limit=1)
        self.assertEqual(conf.account_daily_limit(account), 1)
        account.daily_limit = None
        self.assertEqual(conf.account_daily_limit(account), 5)

    def test_gap_between_messages_of_one_account(self) -> None:
        self._queued()
        set_outreach(min_gap_minutes=30)
        noon = _tehran_noon()
        OutreachAccount.objects.create(name="sina", last_sent_at=noon - timedelta(minutes=10))
        self.assertEqual(claim_for_account("sina", noon), (None, "account_gap"))
        message, reason = claim_for_account("sina", noon + timedelta(minutes=25))
        self.assertEqual(reason, "")
        self.assertIsNotNone(message)

    def test_switched_off_account_gets_nothing(self) -> None:
        self._queued()
        OutreachAccount.objects.create(name="sara", enabled=False)
        claimed = self._claim(account="sara")
        self.assertEqual(claimed, {"message": None, "reason": "account_disabled"})
        self.assertEqual(OutreachMessage.objects.get().status, OutreachStatus.QUEUED)

    def test_shared_gates_still_apply_to_accounts(self) -> None:
        self._queued()
        set_outreach(sending_enabled=False)
        self.assertEqual(claim_for_account("sina", _tehran_noon()), (None, "disabled"))
        self.assertEqual(claim_for_account("not a name!", _tehran_noon()), (None, "bad_account"))

    def test_account_limit_counts_only_its_own_messages(self) -> None:
        self._queued()
        noon = _tehran_noon()
        message, _ = claim_next_message(noon)  # claimed without an account
        self.assertIsNotNone(message)
        account = OutreachAccount.objects.create(name="sina", daily_limit=1)
        self.assertEqual(account_gate(account, noon), "")


# ---------------------------------------------------------------- admin pages


class OutreachAdminTests(OutreachTestCase):
    def setUp(self) -> None:
        super().setUp()
        staff_client(self)

    def _action(self, action: str, *messages: OutreachMessage):
        return self.client.post(
            "/admin/outreach/outreachmessage/",
            {"action": action, "_selected_action": [m.pk for m in messages]},
        )

    def test_queue_actions_change_status(self) -> None:
        message = self._queued()
        self._action("hold_selected", message)
        message.refresh_from_db()
        self.assertEqual(message.status, OutreachStatus.HELD)
        self._action("release_selected", message)
        message.refresh_from_db()
        self.assertEqual(message.status, OutreachStatus.QUEUED)
        self._action("skip_selected", message)
        message.refresh_from_db()
        self.assertEqual((message.status, message.error_code), (OutreachStatus.SKIPPED, "admin"))
        self._action("approve_selected", message)
        message.refresh_from_db()
        self.assertEqual((message.status, message.error_code), (OutreachStatus.QUEUED, ""))

    def test_approve_never_undoes_an_opt_out_or_a_sent_message(self) -> None:
        message = self._queued()
        OutreachMessage.objects.filter(pk=message.pk).update(status=OutreachStatus.SKIPPED, error_code="opted_out")
        self._action("approve_selected", message)
        message.refresh_from_db()
        self.assertEqual(message.status, OutreachStatus.SKIPPED)
        OutreachMessage.objects.filter(pk=message.pk).update(status=OutreachStatus.SENT, error_code="")
        self._action("skip_selected", message)
        message.refresh_from_db()
        self.assertEqual(message.status, OutreachStatus.SENT)

    def test_queue_list_filters_and_preview(self) -> None:
        message = self._queued()
        page = self.client.get("/admin/outreach/outreachmessage/", {"status__exact": "QUEUED"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "maryam_send")
        today = timezone.localtime().date()
        page = self.client.get(
            "/admin/outreach/outreachmessage/",
            {"created_at__year": today.year, "created_at__month": today.month, "created_at__day": today.day},
        )
        self.assertContains(page, "maryam_send")
        detail = self.client.get(f"/admin/outreach/outreachmessage/{message.pk}/change/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "@ali_carry")
        self.assertContains(detail, "100.00")
        self.assertContains(detail, "Text it would be sent with now")

    def test_adding_an_opt_out_skips_waiting_messages(self) -> None:
        message = self._queued()
        response = self.client.post(
            "/admin/outreach/outreachoptout/add/",
            {"telegram_username": "@maryam_send", "telegram_user_id": "", "source": "admin"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(OutreachOptOut.objects.get().telegram_username, "maryam_send")
        message.refresh_from_db()
        self.assertEqual((message.status, message.error_code), (OutreachStatus.SKIPPED, "opted_out"))
        self.assertEqual(self.client.get("/admin/outreach/outreachoptout/").status_code, 200)

    def test_settings_page_saves_and_blank_means_environment(self) -> None:
        listing = self.client.get("/admin/outreach/outreachsettings/")
        self.assertEqual(listing.status_code, 302)
        url = listing["Location"]
        page = self.client.get(url)
        self.assertContains(page, "Values in use")
        self.assertContains(page, "Server default (environment)")
        data = {
            "enabled": "",
            "sending_enabled": "false",
            "daily_limit": "4",
            "account_daily_limit": "2",
            "min_gap_minutes": "20",
            "send_start_hour": "9",
            "send_end_hour": "22",
            "min_score": "85",
            "max_post_age_days": "",
            "cooldown_days": "",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, getattr(response, "context_data", None) and response.context_data["adminform"].form.errors)
        row = OutreachSettings.load()
        self.assertIsNone(row.enabled)
        self.assertFalse(row.sending_enabled)
        self.assertEqual((row.daily_limit, row.min_score, row.send_start_hour), (4, 85, 9))
        self.assertTrue(conf.building_enabled())
        self.assertFalse(conf.sending_enabled())
        bad = self.client.post(url, {**data, "send_start_hour": "22", "send_end_hour": "9"})
        self.assertEqual(bad.status_code, 200)
        self.assertEqual(OutreachSettings.load().send_start_hour, 9)
        bad = self.client.post(url, {**data, "min_score": "101"})
        self.assertEqual(bad.status_code, 200)

    def test_accounts_list_is_editable(self) -> None:
        account = OutreachAccount.objects.create(name="sina")
        page = self.client.get("/admin/outreach/outreachaccount/")
        self.assertContains(page, "form-0-enabled")
        response = self.client.post(
            "/admin/outreach/outreachaccount/",
            {
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "1",
                "form-0-id": str(account.pk),
                "form-0-daily_limit": "3",
                "form-0-min_gap_minutes": "15",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertEqual((account.enabled, account.daily_limit, account.min_gap_minutes), (False, 3, 15))
        self.client.post("/admin/outreach/outreachaccount/", {"action": "enable_selected", "_selected_action": [account.pk]})
        account.refresh_from_db()
        self.assertTrue(account.enabled)

    def test_sidebar_links_to_the_new_pages(self) -> None:
        text = self.client.get("/admin/").content.decode()
        for path in ("/admin/ops/run/", "/admin/ops/numbers/", "/admin/outreach/outreachsettings/", "/admin/outreach/outreachaccount/"):
            self.assertIn(path, text)


class HousingAdminTests(TestCase):
    def setUp(self) -> None:
        staff_client(self)
        HomeSource.objects.all().delete()
        self.source = make_source()

    def test_sources_show_last_seven_days(self) -> None:
        now = timezone.now()
        make_post(self.source, 1, text="a", posted_at=now - timedelta(days=1), status=PostStatus.ACCEPTED)
        make_post(self.source, 2, text="b", posted_at=now - timedelta(days=2), status=PostStatus.REJECTED)
        make_post(self.source, 3, text="c", posted_at=now - timedelta(days=20), status=PostStatus.ACCEPTED)
        row = sources_with_recent_counts().get(pk=self.source.pk)
        self.assertEqual((row.posts_recent, row.accepted_recent), (2, 1))
        page = self.client.get("/admin/home/homesource/")
        self.assertContains(page, "Posts 7 d")
        self.assertContains(page, "form-0-enabled")
        source = next(s for s in home_status()["sources"] if s["username"] == "berlin_khane")
        self.assertEqual((source["posts"], source["posts_7d"], source["accepted_7d"]), (3, 2, 1))
        self.client.post("/admin/home/homesource/", {"action": "disable_selected", "_selected_action": [self.source.pk]})
        self.source.refresh_from_db()
        self.assertFalse(self.source.enabled)

    def test_channel_routing_table_counts_posts_and_toggles(self) -> None:
        city = HomeChannel.objects.create(country="DE", city="Testburg", aliases="Vorort", username="testburg_home")
        country = HomeChannel.objects.create(country="ZZ", city="", username="zz_home")
        now = timezone.now()
        old, fresh, older = de_listing(1), de_listing(2), de_listing(3)
        HomePublication.objects.create(listing=fresh, channel=city, published_at=now - timedelta(hours=2))
        HomePublication.objects.create(listing=old, channel=city, published_at=now - timedelta(days=3))
        HomePublication.objects.create(listing=older, channel=city, published_at=None)
        HomeListing.objects.filter(pk=fresh.pk).update(channel=country, published_at=now - timedelta(hours=1))
        HomeListing.objects.filter(pk=old.pk).update(channel=country, published_at=now - timedelta(days=9))
        rows = {c.username: c for c in channels_with_recent_counts()}
        self.assertEqual((rows["testburg_home"].posts_24h, rows["testburg_home"].posts_7d), (1, 2))
        self.assertEqual((rows["zz_home"].posts_24h, rows["zz_home"].posts_7d), (1, 1))
        page = self.client.get("/admin/home/homechannel/", {"country__exact": "DE"})
        self.assertContains(page, "Posts 24 h")
        self.assertContains(page, "vorort")
        self.client.post("/admin/home/homechannel/", {"action": "disable_selected", "_selected_action": [city.pk]})
        city.refresh_from_db()
        self.assertFalse(city.enabled)
        run_page = self.client.get("/admin/home/run/")
        self.assertContains(run_page, "Posts 24 h")


# ---------------------------------------------------------------- run now


class RunNowTests(TestCase):
    def setUp(self) -> None:
        staff_client(self)

    def test_page_lists_every_job(self) -> None:
        page = self.client.get("/admin/ops/run/")
        self.assertEqual(page.status_code, 200)
        for name in RUN_JOBS:
            self.assertContains(page, f'value="{name}"')
        self.assertEqual([g["title"] for g in job_groups()], ["Courier market", "Outreach", "Housing"])

    @patch("home.job.run_home_job", return_value={"ok": True, "step": "review", "review": {"reviewed": 2}})
    def test_home_steps_call_the_job_with_a_time_budget(self, job) -> None:
        page = self.client.post("/admin/ops/run/", {"job": "home_review"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(job.call_args.kwargs["step"], "review")
        self.assertIn("stop_at", job.call_args.kwargs)
        self.assertContains(page, "&quot;reviewed&quot;: 2")
        self.assertContains(page, "AI review: done")
        for name, step in (("home_all", "all"), ("home_ingest", "ingest"), ("home_media", "media"), ("home_publish", "publish"), ("home_alerts", "alerts"), ("home_vision", "vision")):
            run_job(name)
            self.assertEqual(job.call_args.kwargs["step"], step)

    @patch("market.ingest.market_ingest_enabled", return_value=True)
    @patch("market.extract.publish_stored_requests", return_value={"ok": True, "publish": {"published": 1}})
    @patch("market.extract.convert_stored_posts", return_value={"ok": True, "migrate": {"created": 2}})
    @patch("market.extract.extract_channels_only", return_value={"ok": True, "ingest": {"stored": 3}})
    def test_market_jobs_call_the_cron_functions(self, extract, convert, publish, _enabled) -> None:
        self.assertEqual(run_job("market_extract")["ingest"], {"stored": 3})
        self.assertEqual(extract.call_args.kwargs["days"], 1)
        self.assertEqual(run_job("market_convert")["migrate"], {"created": 2})
        self.assertEqual(run_job("market_publish")["publish"], {"published": 1})
        self.assertTrue(convert.called and publish.called)

    @patch("market.ingest.market_ingest_enabled", return_value=False)
    def test_market_switch_is_respected(self, _enabled) -> None:
        self.assertEqual(run_job("market_extract")["error"], "market ingest disabled")
        self.assertEqual(run_job("market_convert")["error"], "market ingest disabled")

    @patch("home.job.run_home_job", side_effect=RuntimeError("boom"))
    def test_errors_are_reported_not_raised(self, _job) -> None:
        result = run_job("home_all")
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])
        self.assertEqual(run_job("drop_tables")["error"], "unknown job 'drop_tables'")
        page = self.client.post("/admin/ops/run/", {"job": "home_all"})
        self.assertContains(page, "boom")

    def test_staff_only_and_csrf(self) -> None:
        self.client.logout()
        self.assertEqual(self.client.post("/admin/ops/run/", {"job": "home_all"}).status_code, 302)
        strict = Client(enforce_csrf_checks=True)
        strict.login(username="ops", password="ops-pass")
        with patch("home.job.run_home_job") as job:
            self.assertEqual(strict.post("/admin/ops/run/", {"job": "home_all"}).status_code, 403)
            job.assert_not_called()
        self.assertEqual(self.client.get("/admin/ops/numbers/").status_code, 302)


# ---------------------------------------------------------------- numbers


class NumbersTests(OutreachTestCase):
    def test_counts_per_city_per_day(self) -> None:
        HomeSource.objects.all().delete()
        listing = de_listing(1)
        HomeListing.objects.filter(pk=listing.pk).update(city="Berlin", published_at=timezone.now(), channel=HomeChannel.objects.filter(country="DE").first())
        HomeAlert.objects.create(telegram_user_id=1, chat_id=1, country="CA", city="Toronto")
        numbers = city_daily_counts()
        self.assertEqual(len(numbers["days"]), 14)
        self.assertEqual(numbers["days"][-1], timezone.localtime().date())
        tables = {t["key"]: t for t in numbers["tables"]}
        self.assertEqual(tables["housing_accepted"]["rows"][0]["city"], "Berlin (DE)")
        self.assertEqual(tables["housing_accepted"]["rows"][0]["counts"][-1], 1)
        self.assertEqual(tables["housing_published"]["total"], 1)
        self.assertEqual(tables["alerts_saved"]["rows"][0]["city"], "Toronto (CA)")
        self.assertEqual(tables["courier_requests"]["total"], 2)
        self.assertEqual(tables["courier_matches"]["total"], 1)

        staff_client(self)
        page = self.client.get("/admin/ops/numbers/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Courier matches")
        self.assertContains(page, "Toronto (CA)")

    def test_many_cities_fold_into_other(self) -> None:
        for n in range(35):
            HomeAlert.objects.create(telegram_user_id=n, chat_id=n, country="DE", city=f"City{n:02d}")
        table = next(t for t in city_daily_counts()["tables"] if t["key"] == "alerts_saved")
        self.assertEqual(len(table["rows"]), 30)
        self.assertEqual(table["rows"][-1]["city"], "6 other cities")
        self.assertEqual(table["total"], 35)
