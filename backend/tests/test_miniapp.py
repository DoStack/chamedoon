from __future__ import annotations

import re

from django.test import Client, override_settings
from rest_framework.test import APITestCase

from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from matching.acceptance import accept_match
from miniapp.auth import SESSION_USER_KEY
from miniapp.i18n import LOCALE_COOKIE
from tests.helpers import TEST_SECRET, make_user
from tests.test_requests import DEMAND_PAYLOAD, SUPPLY_PAYLOAD


def _login(client: Client, user) -> None:
    session = client.session
    session[SESSION_USER_KEY] = user.pk
    session.save()


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=True)
class MiniAppTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.user = make_user(telegram_user_id=99001, first_name="Leila")
        self.other = make_user(telegram_user_id=99002, first_name="Ali")

    def test_landing_and_login_redirect(self) -> None:
        landing = self.client.get("/")
        self.assertEqual(landing.status_code, 200)
        home = self.client.get("/app/")
        self.assertEqual(home.status_code, 302)
        self.assertIn("/app/login/", home["Location"])

    def test_farsi_pages_preload_iransans(self) -> None:
        self.client.cookies[LOCALE_COOKIE] = "fa"
        page = self.client.get("/app/login/")
        self.assertContains(page, 'lang="fa"')
        self.assertContains(page, 'dir="rtl"')
        self.assertContains(page, "IRANSansWeb_FaNum.woff2")

    @override_settings(DEBUG=False, TELEGRAM_BOT_USERNAME="CB_koolbarbot")
    def test_production_login_asks_to_open_telegram(self) -> None:
        landing = self.client.get("/")
        self.assertContains(landing, "https://t.me/CB_koolbarbot/app")
        page = self.client.get("/app/login/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Open Koolbar in Telegram")
        self.assertContains(page, "https://t.me/CB_koolbarbot/app")

    def test_debug_login_sets_session(self) -> None:
        response = self.client.post(
            "/app/login/",
            {"telegram_user_id": "42", "first_name": "Dev"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/app/")
        self.assertTrue(self.client.session.get(SESSION_USER_KEY))

    def test_startapp_request_opens_request_detail(self) -> None:
        demand = create_item_request(self.user, DEMAND_PAYLOAD)
        _login(self.client, self.user)
        response = self.client.get(f"/app/?startapp=request_{demand.pk}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/app/requests/{demand.pk}/")

    def test_create_demand_from_html_form(self) -> None:
        _login(self.client, self.user)
        response = self.client.post(
            "/app/demand/new/",
            {
                "origin_country": "IR",
                "origin_city": "tehran",
                "destination_country": "CA",
                "destination_city": "toronto",
                "desired_date": "2027-09-07",
                "weight_kg": "2",
                "item_category_codes": ["CLOTHES"],
                "description": "Bag",
            },
        )
        self.assertEqual(response.status_code, 302, response.content)
        self.assertRegex(response["Location"], r"^/app/requests/\d+/$")
        listing = self.client.get("/app/requests/")
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "Tehran")
        self.assertContains(listing, "chip-active")
        self.assertContains(listing, "chip-demand")
        self.assertContains(listing, "miniapp/icons/28/archive.svg")

    def test_demand_form_is_a_three_step_wizard(self) -> None:
        _login(self.client, self.user)
        page = self.client.get("/app/demand/new/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "wizard-form")
        self.assertContains(page, "place-stepper")
        self.assertContains(page, "Choose the origin country.")
        self.assertContains(page, "option-list")
        self.assertContains(page, '"code": "IR"')
        self.assertContains(page, r"\ud83c\uddee\ud83c\uddf7")
        self.assertContains(page, 'name="origin_country"')
        self.assertContains(page, 'name="destination_city"')
        self.assertContains(page, 'data-draft-key="DEMAND-new"')
        self.assertContains(page, '<input type="date" name="desired_date"')
        self.assertNotContains(page, '<input type="date" name="flight_date"')
        self.assertContains(page, 'id="cargo-categories"')
        self.assertContains(page, "👕")
        self.assertContains(page, "📄")

    def test_supply_form_uses_opt_in_category_checks(self) -> None:
        _login(self.client, self.user)
        page = self.client.get("/app/supply/new/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "wizard-form")
        self.assertContains(page, 'class="wizard-step"', count=3)
        self.assertContains(page, 'id="cargo-categories"')
        self.assertNotContains(page, "cargo-board")
        self.assertContains(page, "I can carry")
        self.assertContains(page, "Select the items you can carry.")
        self.assertContains(page, 'value="DOCUMENTS"')
        self.assertContains(page, 'value="CLOTHES"')
        self.assertContains(page, "👕")
        self.assertContains(page, "📄")
        self.assertIsNone(re.search(r'name="item_category_codes" value="[^"]+"\s+checked', page.content.decode()))
        self.assertContains(page, '<input type="date" name="flight_date"')
        self.assertContains(page, '<input type="date" name="date_from"')
        self.assertContains(page, '<input type="date" name="date_to"')
        self.assertNotContains(page, '<input type="date" name="desired_date"')

    def test_edit_supply_checks_carried_categories(self) -> None:
        _login(self.client, self.user)
        supply = create_item_request(self.user, SUPPLY_PAYLOAD)
        page = self.client.get(f"/app/requests/{supply.pk}/?edit=1")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "wizard-form")
        html = page.content.decode()
        checked = re.findall(r'name="item_category_codes" value="([^"]+)"\s+checked', html)
        self.assertCountEqual(checked, ["CLOTHES", "DOCUMENTS", "PERSONAL_ITEMS"])

    def test_create_supply_from_html_form(self) -> None:
        _login(self.client, self.user)
        response = self.client.post(
            "/app/supply/new/",
            {
                "origin_country": "IR",
                "origin_city": "tehran",
                "destination_country": "CA",
                "destination_city": "toronto",
                "flight_date": "2027-09-10",
                "date_from": "2027-09-01",
                "date_to": "2027-09-15",
                "capacity_kg": "8",
                "item_category_codes": [
                    "DOCUMENTS",
                    "CLOTHES",
                    "PERSONAL_ITEMS",
                    "ELECTRONICS",
                    "FOOD",
                    "FRAGILE",
                    "OTHER",
                ],
                "excluded_category_codes": ["MEDICINE", "CIGARETTES"],
                "description": "Trip bag",
            },
        )
        self.assertEqual(response.status_code, 302, response.content)
        self.assertRegex(response["Location"], r"^/app/requests/\d+/$")
        detail = self.client.get(response["Location"])
        self.assertContains(detail, "Clothes")
        self.assertContains(detail, "Medicine")

    def test_explore_and_propose_match(self) -> None:
        create_item_request(self.user, DEMAND_PAYLOAD)
        supply = create_item_request(self.other, {**SUPPLY_PAYLOAD, "origin_city": "mashhad"})
        _login(self.client, self.user)
        page = self.client.get("/app/explore/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Ali")
        self.assertContains(page, 'class="filter-trigger"')
        self.assertContains(page, 'id="open-filters"')
        self.assertContains(page, 'id="filter-sheet"')
        self.assertContains(page, "Route, dates, items")
        self.assertContains(page, "Any origin")
        self.assertContains(page, "Mashhad")
        self.assertContains(page, "Toronto")
        self.assertContains(page, 'name="category"')
        self.assertContains(page, 'value="CLOTHES"')
        self.assertNotContains(page, 'name="category" value="MEDICINE"')
        self.assertNotContains(page, "Vancouver")
        self.assertNotContains(page, "<select")
        self.assertNotContains(page, 'class="btn btn-secondary btn-icon"')
        filtered = self.client.get("/app/explore/?category=CLOTHES&category=DOCUMENTS")
        self.assertEqual(filtered.status_code, 200)
        self.assertContains(filtered, 'name="category" value="CLOTHES"')
        self.assertContains(filtered, "Clothes · Documents")
        connect = self.client.post("/app/explore/", {"request_id": str(supply.id)})
        self.assertEqual(connect.status_code, 302, connect.content)
        self.assertRegex(connect["Location"], r"^/app/matches/\d+/$")
        detail = self.client.get(connect["Location"])
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Request")
        self.assertNotContains(detail, ">Accept<")
        from matching.models import Match

        match = Match.objects.get()
        accept_match(match, self.user)
        _login(self.client, self.other)
        other_page = self.client.get(connect["Location"])
        self.assertContains(other_page, "Accept")
        self.assertContains(other_page, "They requested this match")

    def test_match_list_card_shows_flags_and_friendly_dates(self) -> None:
        create_item_request(self.user, DEMAND_PAYLOAD)
        create_item_request(self.other, SUPPLY_PAYLOAD)
        _login(self.client, self.user)
        page = self.client.get("/app/matches/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "🇮🇷")
        self.assertContains(page, "🇨🇦")
        self.assertContains(page, "Tehran")
        self.assertContains(page, "Toronto")
        self.assertContains(page, "✈️ 2027-09-10")
        self.assertContains(page, "Carry from Sep 1 to Sep 15")
        self.assertContains(page, "🧳 2 KG")
        self.assertContains(page, "🧳 5 KG")
        self.assertContains(page, 'class="muted match-meta"')
        self.assertNotContains(page, "Travel:")
        self.assertNotContains(page, "chip-demand")
        self.assertNotContains(page, "chip-supply")
        self.assertNotContains(page, "2027-09-01")

    def test_connected_match_shows_telegram_id_and_dm_link(self) -> None:
        self.other.telegram_username = "ali_carry"
        self.other.save(update_fields=["telegram_username"])
        create_item_request(self.user, DEMAND_PAYLOAD)
        create_item_request(self.other, SUPPLY_PAYLOAD)
        from matching.models import Match

        match = Match.objects.get()
        accept_match(match, self.user)
        match.refresh_from_db()
        accept_match(match, self.other)
        _login(self.client, self.user)
        page = self.client.get(f"/app/matches/{match.pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Telegram ID")
        self.assertContains(page, str(self.other.telegram_user_id))
        self.assertContains(page, "@ali_carry")
        self.assertContains(page, "https://t.me/ali_carry")
        self.assertContains(page, "Message on Telegram")
        self.assertNotContains(page, "Appreciate by message")
        self.assertContains(page, "من یه بسته دارم")
        self.assertContains(page, "👕 لباس")
        self.assertContains(page, "تهران")

    def test_finish_order_then_rate_from_request(self) -> None:
        create_item_request(self.user, DEMAND_PAYLOAD)
        create_item_request(self.other, SUPPLY_PAYLOAD)
        from matching.models import Match
        from item_requests.models import RequestStatus

        match = Match.objects.get()
        accept_match(match, self.user)
        match.refresh_from_db()
        accept_match(match, self.other)
        _login(self.client, self.user)
        finish = self.client.post(f"/app/matches/{match.pk}/", {"action": "complete"})
        self.assertEqual(finish.status_code, 302)
        match.refresh_from_db()
        self.assertEqual(match.status, "COMPLETED")
        self.user.item_requests.first().refresh_from_db()
        demand = match.demand_request
        demand.refresh_from_db()
        self.assertEqual(demand.status, RequestStatus.COMPLETED)
        page = self.client.get(f"/app/matches/{match.pk}/")
        self.assertContains(page, "Rate this order")
        self.assertContains(page, "chip-completed")
        self.assertContains(page, "Appreciate by message")
        self.assertNotContains(page, "Message on Telegram")
        self.assertContains(page, "Telegram ID")
        self.assertContains(page, "ممنونم")
        self.assertNotContains(page, "Match score")
        self.assertNotContains(page, "Strong match")
        rate = self.client.post(
            f"/app/matches/{match.pk}/",
            {"action": "rate", "score": "5", "comment": "On time and careful."},
        )
        self.assertEqual(rate.status_code, 302)
        from matching.models import MatchRating

        self.assertEqual(MatchRating.objects.get(match=match, rater=self.user).score, 5)
        page = self.client.get(f"/app/matches/{match.pk}/")
        self.assertNotContains(page, "Your rating")
        self.assertNotContains(page, "On time and careful.")
        self.assertNotContains(page, "★★★★★")
        request_page = self.client.get(f"/app/requests/{demand.pk}/")
        self.assertNotContains(request_page, "Your rating")
        self.assertNotContains(request_page, "★★★★★")
        self.assertNotContains(request_page, "On time and careful.")
        self.assertContains(request_page, "chip-completed")

        _login(self.client, self.other)
        other_rate = self.client.post(
            f"/app/matches/{match.pk}/",
            {"action": "rate", "score": "4", "comment": "Sender was easy to meet."},
        )
        self.assertEqual(other_rate.status_code, 302)
        self.assertEqual(MatchRating.objects.get(match=match, rater=self.other).score, 4)
        _login(self.client, self.user)
        both = self.client.get(f"/app/matches/{match.pk}/")
        self.assertNotContains(both, "On time and careful.")
        self.assertNotContains(both, "Sender was easy to meet.")
        self.assertNotContains(both, "Their rating")
        request_page = self.client.get(f"/app/requests/{demand.pk}/")
        self.assertNotContains(request_page, "Sender was easy to meet.")

    def test_cancel_connected_hides_contact_in_miniapp(self) -> None:
        create_item_request(self.user, DEMAND_PAYLOAD)
        create_item_request(self.other, SUPPLY_PAYLOAD)
        from matching.models import Match
        from item_requests.services import cancel_item_request

        match = Match.objects.get()
        accept_match(match, self.user)
        match.refresh_from_db()
        accept_match(match, self.other)
        cancel_item_request(match.demand_request)
        _login(self.client, self.user)
        page = self.client.get(f"/app/matches/{match.pk}/")
        self.assertEqual(page.status_code, 404)
        _login(self.client, self.other)
        other_page = self.client.get(f"/app/matches/{match.pk}/")
        self.assertEqual(other_page.status_code, 404)

    def test_supply_close_asks_if_package_was_sent(self) -> None:
        supply = create_item_request(self.other, SUPPLY_PAYLOAD)
        _login(self.client, self.other)
        page = self.client.get(f"/app/requests/{supply.pk}/")
        self.assertContains(page, "Close request")
        self.assertNotContains(page, "Cancel request")
        confirm = self.client.get(f"/app/requests/{supply.pk}/?close=1")
        self.assertContains(confirm, "Did you send the package?")
        self.assertContains(confirm, "Yes, I sent the package")
        self.assertContains(confirm, "Just close")
        closed = self.client.post(
            f"/app/requests/{supply.pk}/",
            {"action": "close", "package_sent": "0"},
        )
        self.assertEqual(closed.status_code, 302)
        supply.refresh_from_db()
        self.assertEqual(supply.status, "CANCELLED")
        self.assertFalse(supply.package_sent)
