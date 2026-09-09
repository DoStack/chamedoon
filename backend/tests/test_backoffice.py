from __future__ import annotations

import json
import re
from decimal import Decimal
from html import unescape
from unittest.mock import patch

from django.contrib.auth.models import User as StaffUser
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from item_requests.seed import seed_catalog
from item_requests.services import create_item_request, update_item_request
from matching.manual import create_manual_match, set_match_status
from matching.models import Match, MatchStatus
from matching.scoring import calculate_score, capacity_score, category_score, date_proximity_score
from tests.helpers import TEST_SECRET, bearer_auth, make_user
from tests.test_requests import DEMAND_PAYLOAD, SUPPLY_PAYLOAD
from users.services import deactivate_user


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False)
class ManualMatchTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.demand_user = make_user(telegram_user_id=97001, first_name="Sara")
        self.supply_user = make_user(telegram_user_id=97002, first_name="Ali")
        self.demand = create_item_request(self.demand_user, DEMAND_PAYLOAD)
        self.supply = create_item_request(
            self.supply_user,
            {**SUPPLY_PAYLOAD, "origin_city": "mashhad", "origin_country": "IR"},
        )

    def test_manual_match_requires_override_when_rules_fail(self) -> None:
        with self.assertRaises(ValidationError):
            create_manual_match(self.demand, self.supply)
        self.assertEqual(Match.objects.filter(demand_request=self.demand, supply_request=self.supply).count(), 0)

        match = create_manual_match(self.demand, self.supply, override_rules=True)
        self.assertEqual(match.status, MatchStatus.SUGGESTED)
        self.assertEqual(match.score, Decimal("64.00"))

    def test_manual_match_without_override_when_compatible(self) -> None:
        sender = make_user(telegram_user_id=97003, first_name="Nima")
        traveler = make_user(telegram_user_id=97004, first_name="Omar")
        demand = create_item_request(
            sender,
            {**DEMAND_PAYLOAD, "destination_city": "vancouver", "destination_country": "CA"},
        )
        supply = create_item_request(
            traveler,
            {**SUPPLY_PAYLOAD, "destination_city": "vancouver", "destination_country": "CA"},
        )
        Match.objects.filter(demand_request=demand, supply_request=supply).delete()
        match = create_manual_match(demand, supply)
        self.assertEqual(match.status, MatchStatus.SUGGESTED)
        self.assertEqual(match.score, Decimal("94.00"))

    def test_deactivate_user_cancels_requests_and_expires_matches(self) -> None:
        demand = create_item_request(
            self.demand_user,
            {**DEMAND_PAYLOAD, "destination_city": "vancouver", "destination_country": "CA"},
        )
        create_item_request(
            self.supply_user,
            {**SUPPLY_PAYLOAD, "destination_city": "vancouver", "destination_country": "CA"},
        )
        self.assertTrue(Match.objects.filter(demand_request=demand, status=MatchStatus.SUGGESTED).exists())
        deactivate_user(self.demand_user)
        self.demand_user.refresh_from_db()
        demand.refresh_from_db()
        self.assertFalse(self.demand_user.is_active)
        self.assertEqual(demand.status, "CANCELLED")
        self.assertEqual(Match.objects.get(demand_request=demand).status, MatchStatus.EXPIRED)

    def test_set_match_status_connected(self) -> None:
        match = create_manual_match(self.demand, self.supply, override_rules=True)
        with patch("notifications.services.send_telegram_message", return_value=True):
            updated = set_match_status(match, MatchStatus.CONNECTED)
        self.assertEqual(updated.status, MatchStatus.CONNECTED)


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False)
class MatchingGapTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.demand_user = make_user(telegram_user_id=98001, first_name="Leila")
        self.supply_user = make_user(telegram_user_id=98002, first_name="Omar", telegram_username="omar_ops")
        self.outsider = make_user(telegram_user_id=98003, first_name="Nima")

    def test_inclusive_date_overlap_matches(self) -> None:
        create_item_request(
            self.demand_user,
            {**DEMAND_PAYLOAD, "desired_date": "2027-09-15"},
        )
        create_item_request(
            self.supply_user,
            {**SUPPLY_PAYLOAD, "date_from": "2027-09-15", "date_to": "2027-09-15"},
        )
        self.assertEqual(Match.objects.count(), 1)

    def test_score_components_for_spec_example(self) -> None:
        demand = create_item_request(self.demand_user, DEMAND_PAYLOAD)
        supply = create_item_request(self.supply_user, SUPPLY_PAYLOAD)
        self.assertEqual(date_proximity_score(demand, supply), Decimal("20.00"))
        self.assertEqual(capacity_score(demand, supply), Decimal("4.00"))
        self.assertEqual(category_score(demand, supply), Decimal("10.00"))
        self.assertEqual(calculate_score(demand, supply), Decimal("94.00"))

    def test_connected_match_survives_request_edit(self) -> None:
        demand = create_item_request(self.demand_user, DEMAND_PAYLOAD)
        create_item_request(self.supply_user, SUPPLY_PAYLOAD)
        match = Match.objects.get()
        self.client.post(f"/api/matches/{match.id}/accept/", **bearer_auth(self.demand_user))
        self.client.post(f"/api/matches/{match.id}/accept/", **bearer_auth(self.supply_user))
        match.refresh_from_db()
        self.assertEqual(match.status, MatchStatus.CONNECTED)
        update_item_request(demand, {"description": "Updated clothes"})
        match.refresh_from_db()
        self.assertEqual(match.status, MatchStatus.CONNECTED)

    def test_outsider_cannot_accept_or_reject(self) -> None:
        create_item_request(self.demand_user, DEMAND_PAYLOAD)
        create_item_request(self.supply_user, SUPPLY_PAYLOAD)
        match = Match.objects.get()
        accept = self.client.post(f"/api/matches/{match.id}/accept/", **bearer_auth(self.outsider))
        reject = self.client.post(f"/api/matches/{match.id}/reject/", **bearer_auth(self.outsider))
        self.assertEqual(accept.status_code, 404)
        self.assertEqual(reject.status_code, 404)

    def test_inactive_user_cannot_use_api(self) -> None:
        create_item_request(self.demand_user, DEMAND_PAYLOAD)
        deactivate_user(self.demand_user)
        response = self.client.get("/api/me/", **bearer_auth(self.demand_user))
        self.assertEqual(response.status_code, 401)


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=True)
class BackOfficeAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()
        StaffUser.objects.create_superuser("ops", "ops@example.com", "ops-pass")

    def setUp(self) -> None:
        self.client.login(username="ops", password="ops-pass")
        self.demand_user = make_user(telegram_user_id=99001, first_name="Sara")
        self.supply_user = make_user(telegram_user_id=99002, first_name="Ali")
        self.demand = create_item_request(self.demand_user, DEMAND_PAYLOAD)
        self.supply = create_item_request(
            self.supply_user,
            {**SUPPLY_PAYLOAD, "origin_city": "mashhad"},
        )

    def test_admin_pages_load(self) -> None:
        for path in (
            "/admin/",
            "/admin/users/user/",
            "/admin/item_requests/itemrequest/",
            "/admin/matching/match/",
            "/admin/item_requests/category/",
            "/admin/market/extract/",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
        home = self.client.get("/admin/")
        self.assertContains(home, "Operations dashboard")
        self.assertContains(home, "ops-kpi-grid")
        self.assertContains(home, "Live marketplace")
        self.assertContains(home, "Active demand")
        self.assertContains(home, "Active supply")
        self.assertContains(home, "Unmatched demand")
        self.assertContains(home, "Suggested matches")
        self.assertContains(home, "14-day funnel")
        self.assertContains(home, "Match pipeline")
        self.assertContains(home, "Top live routes")
        self.assertContains(home, "Match counts")
        self.assertContains(home, "Route breakdown")
        self.assertContains(home, "Channel publish")
        self.assertContains(home, "Waiting too long")
        self.assertContains(home, "Expiring in 3 days")
        self.assertNotContains(home, "Recent imported requests")
        self.assertContains(home, "Channel crawl health")
        self.assertContains(home, "Dashboard")
        self.assertContains(home, "Market posts")
        self.assertContains(home, "Manual extract")
        self.assertContains(home, "New requests (14 days)")
        self.assertContains(home, "New users (14 days)")
        self.assertContains(home, "Market skip reasons")
        self.assertContains(home, 'data-type="line"')
        self.assertContains(home, 'data-type="bar"')
        self.assertContains(home, 'class="chart"')
        self.assertIn("Demand", home.content.decode())
        self.assertIn("tehran", home.content.decode())
        canvases = re.findall(r'data-value="([^"]*)"', home.content.decode())
        self.assertGreaterEqual(len(canvases), 6)
        for raw in canvases:
            payload = json.loads(unescape(raw))
            self.assertIn("labels", payload)
            self.assertIn("datasets", payload)
        from django.urls import reverse

        self.assertEqual(reverse("admin:index"), "/admin/")
        self.assertEqual(reverse("admin:dashboard"), "/admin/dashboard/")
        extra = self.client.get("/admin/dashboard/")
        self.assertEqual(extra.status_code, 302)
        self.assertEqual(extra["Location"], "/admin/")
        root = self.client.get("/dashboard/")
        self.assertEqual(root.status_code, 302)
        self.assertEqual(root["Location"], "/admin/")
        requests_page = self.client.get("/admin/item_requests/itemrequest/")
        self.assertContains(requests_page, "navigationOpen: true")
        self.assertContains(requests_page, "active")

    def test_dashboard_requires_staff(self) -> None:
        self.client.logout()
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])
        extra = self.client.get("/admin/dashboard/")
        self.assertEqual(extra.status_code, 302)
        self.assertIn("/admin/login/", extra["Location"])

    def test_unfold_sidebar_config_is_safe_at_import(self) -> None:
        from django.conf import settings

        for group in settings.UNFOLD["SIDEBAR"]["navigation"]:
            self.assertIsInstance(group["title"], str)
            for item in group["items"]:
                self.assertIsInstance(item["title"], str)
                self.assertTrue(callable(item["link"]))
                self.assertTrue(callable(item["active"]))

    def test_admin_can_create_manual_match_with_override(self) -> None:
        response = self.client.post(
            "/admin/matching/match/add/",
            {
                "demand_request": self.demand.id,
                "supply_request": self.supply.id,
                "status": MatchStatus.SUGGESTED,
                "override_rules": "on",
                "_save": "Save",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(
            Match.objects.filter(demand_request=self.demand, supply_request=self.supply).exists()
        )
