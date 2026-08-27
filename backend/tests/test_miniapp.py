from __future__ import annotations

import re
from pathlib import Path

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

    def test_pages_follow_telegram_color_scheme(self) -> None:
        page = self.client.get("/app/login/")
        html = page.content.decode()
        self.assertIn("telegram-web-app.js", html)
        self.assertIn("miniapp/theme.js", html)
        self.assertLess(html.index("telegram-web-app.js"), html.index("miniapp/theme.js"))
        self.assertLess(html.index("miniapp/theme.js"), html.index("miniapp/app.css"))
        self.assertIn('name="color-scheme"', html)
        theme_js = (
            Path(__file__).resolve().parents[1] / "miniapp/static/miniapp/theme.js"
        ).read_text()
        self.assertIn("colorScheme", theme_js)
        self.assertIn("themeChanged", theme_js)
        self.assertIn("tgui-dark", theme_js)
        app_css = (
            Path(__file__).resolve().parents[1] / "miniapp/static/miniapp/app.css"
        ).read_text()
        self.assertIn("html:not([data-tg-color-scheme])", app_css)
        self.assertIn('html[data-tg-color-scheme="light"]', app_css)

    @override_settings(DEBUG=False, TELEGRAM_BOT_USERNAME="CB_koolbarbot")
    def test_production_login_asks_to_open_telegram(self) -> None:
        landing = self.client.get("/")
        self.assertContains(landing, "https://t.me/CB_koolbarbot/app")
        page = self.client.get("/app/login/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Open Koolbar in Telegram")
        self.assertContains(page, "Open Mini App")
        self.assertContains(page, "Open the Koolbar bot")
        self.assertContains(page, "https://t.me/CB_koolbarbot/app")
        self.assertContains(page, "https://t.me/CB_koolbarbot")
        self.assertContains(page, "tg://resolve")
        self.assertContains(page, "openTelegramLink")

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
        self.assertContains(listing, "card-facts")
        self.assertContains(listing, "card-fact-matches")
        self.assertContains(listing, "miniapp/icons/28/heart.svg")
        self.assertContains(listing, 'title="0 matches"')
        self.assertContains(listing, "match-head")
        self.assertContains(listing, "listing-card")
        self.assertContains(listing, "card-list")
        self.assertNotContains(listing, "tgui-icon-chevron")
        self.assertNotContains(listing, "Carry from")
        self.assertContains(listing, "📅 2027-09-07")
        self.assertContains(listing, "🧳 2 KG")
        self.assertContains(listing, "miniapp/icons/28/archive.svg")

    def test_demand_form_is_a_three_step_wizard(self) -> None:
        _login(self.client, self.user)
        page = self.client.get("/app/demand/new/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "wizard-form")
        self.assertContains(page, "place-stepper")
        self.assertContains(page, "wizard-sticky")
        self.assertContains(page, "pinWizardChrome")
        self.assertContains(page, 'id="origin-flag"')
        self.assertContains(page, 'id="dest-flag"')
        self.assertContains(page, "Choose the origin country.")
        self.assertContains(page, 'data-chip="review"')
        self.assertContains(page, "revealActiveChip")
        self.assertContains(page, "scrollIntoView")
        self.assertContains(page, "Confirm and publish")
        self.assertContains(page, "Check this summary")
        self.assertContains(page, 'data-step="review"')
        self.assertContains(page, 'id="review-table"')
        self.assertContains(page, 'class="dl"')
        self.assertContains(page, "Desired date")
        self.assertContains(page, 'id="review-route"')
        self.assertContains(page, "option-list")
        self.assertContains(page, '"code": "IR"')
        self.assertContains(page, r"\ud83c\uddee\ud83c\uddf7")
        self.assertContains(page, 'name="origin_country"')
        self.assertContains(page, 'name="destination_city"')
        self.assertNotContains(page, "data-draft-key")
        self.assertNotContains(page, "localStorage.setItem")
        self.assertNotContains(page, "sessionStorage.setItem")
        self.assertContains(page, '<input type="date" name="desired_date"')
        self.assertNotContains(page, '<input type="date" name="flight_date"')
        self.assertContains(page, 'id="cargo-categories"')
        self.assertContains(page, "👕")
        self.assertContains(page, "📄")

    def test_supply_form_starts_with_all_items_carried(self) -> None:
        _login(self.client, self.user)
        page = self.client.get("/app/supply/new/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "wizard-form")
        self.assertContains(page, 'class="wizard-step"', count=4)
        self.assertContains(page, "cargo-board")
        self.assertContains(page, "I can carry")
        self.assertContains(page, "I will not carry")
        self.assertContains(page, 'id="review-table"')
        self.assertContains(page, 'class="dl"')
        self.assertContains(page, "Flight date")
        self.assertContains(page, "Nothing excluded yet.")
        self.assertContains(page, "Everything starts as can-carry.")
        self.assertContains(page, 'value="DOCUMENTS"')
        self.assertContains(page, 'value="CLOTHES"')
        self.assertContains(page, "👕")
        self.assertContains(page, "📄")
        html = page.content.decode()
        checked = re.findall(r'name="item_category_codes" value="([^"]+)"\s+checked', html)
        self.assertCountEqual(
            checked,
            [
                "DOCUMENTS",
                "CLOTHES",
                "PERSONAL_ITEMS",
                "ELECTRONICS",
                "FOOD",
                "MEDICINE",
                "CIGARETTES",
                "FRAGILE",
                "PET",
                "OTHER",
            ],
        )
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
        self.assertIn("is-refuse", html)
        self.assertIn("🇮🇷", html)
        self.assertIn("🇨🇦", html)

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
        self.assertContains(detail, "Your request")
        self.assertContains(detail, "Matched requests")
        self.assertContains(detail, 'class="dl"')
        self.assertContains(detail, "Flight date")
        self.assertContains(detail, "Carry from Sep 1 to Sep 15")

    def test_explore_and_propose_match(self) -> None:
        demand = create_item_request(self.user, DEMAND_PAYLOAD)
        supply = create_item_request(self.other, {**SUPPLY_PAYLOAD, "origin_city": "mashhad"})
        _login(self.client, self.user)
        page = self.client.get("/app/explore/")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Browse open send")
        self.assertNotContains(page, "Posted by Ali")
        self.assertNotContains(page, "Request match")
        self.assertNotContains(page, "Carry from")
        self.assertContains(page, 'class="search-trigger')
        self.assertContains(page, 'id="open-filters"')
        self.assertContains(page, 'id="filter-sheet"')
        self.assertContains(page, "miniapp/icons/24/search.svg")
        self.assertContains(page, "Any origin")
        self.assertContains(page, "Mashhad")
        self.assertContains(page, "Toronto")
        self.assertContains(page, 'name="category"')
        self.assertContains(page, 'value="CLOTHES"')
        self.assertNotContains(page, 'name="category" value="MEDICINE"')
        self.assertNotContains(page, "Vancouver")
        self.assertNotContains(page, "<select")
        self.assertNotContains(page, 'class="btn btn-secondary btn-icon"')
        self.assertNotContains(page, "Signed in as")
        self.assertNotContains(page, "chip-active")
        self.assertContains(page, "chip-supply")
        self.assertContains(page, "🧳 5 KG")
        self.assertContains(page, "✈️ 2027-09-10")
        self.assertContains(page, "👕")
        self.assertContains(page, "⛔")
        self.assertContains(page, f'href="/app/explore/{supply.pk}/"')
        self.assertContains(page, "listing-card")
        home = self.client.get("/app/")
        self.assertContains(home, "Signed in as")
        filtered = self.client.get("/app/explore/?category=CLOTHES&category=DOCUMENTS")
        self.assertEqual(filtered.status_code, 200)
        self.assertContains(filtered, 'name="category" value="CLOTHES"')
        self.assertContains(filtered, "has-filters")
        self.assertContains(filtered, 'class="filter-count"')
        self.assertContains(filtered, ">2<")
        detail = self.client.get(f"/app/explore/{supply.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Posted by Ali")
        self.assertContains(detail, "Request match")
        self.assertContains(detail, 'class="dl"')
        self.assertContains(detail, "Flight date")
        self.assertContains(detail, "Carry from Sep 1 to Sep 15")
        self.assertContains(detail, "Clothes")
        self.assertContains(detail, "pick-list")
        self.assertContains(detail, "🧳 2 KG")
        self.assertNotContains(detail, "<select")
        via_own = self.client.get(f"/app/requests/{supply.pk}/")
        self.assertEqual(via_own.status_code, 302)
        self.assertEqual(via_own["Location"], f"/app/explore/{supply.pk}/")
        connect = self.client.post(f"/app/explore/{supply.pk}/", {"my_request_id": str(demand.pk)})
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

    def test_explore_pdp_lists_requests_instead_of_select(self) -> None:
        first = create_item_request(self.user, DEMAND_PAYLOAD)
        create_item_request(self.user, {**DEMAND_PAYLOAD, "weight_kg": "6.00"})
        supply = create_item_request(self.other, {**SUPPLY_PAYLOAD, "origin_city": "mashhad"})
        _login(self.client, self.user)
        page = self.client.get(f"/app/explore/{supply.pk}/")
        html = page.content.decode()
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "<select")
        self.assertContains(page, "Which of your requests should we pair?")
        self.assertContains(page, "pick-list")
        self.assertContains(page, "🧳 2 KG")
        self.assertContains(page, "🧳 6 KG")
        radios = re.findall(r"<input type=\"radio\"[^>]*>", html)
        self.assertEqual(len(radios), 2)
        self.assertIn("checked", radios[0])
        self.assertEqual(sum("checked" in radio for radio in radios), 1)
        chosen = self.client.post(f"/app/explore/{supply.pk}/", {"my_request_id": str(first.pk)})
        self.assertEqual(chosen.status_code, 302)

    def test_match_list_card_shows_flags_and_friendly_dates(self) -> None:
        create_item_request(self.user, DEMAND_PAYLOAD)
        create_item_request(self.other, SUPPLY_PAYLOAD)
        from matching.models import Match

        _login(self.client, self.user)
        page = self.client.get("/app/matches/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "🇮🇷")
        self.assertContains(page, "🇨🇦")
        self.assertContains(page, "Tehran")
        self.assertContains(page, "Toronto")
        self.assertContains(page, "✈️ 2027-09-10")
        self.assertNotContains(page, "Carry from Sep 1 to Sep 15")
        self.assertContains(page, "🧳 2 KG")
        self.assertContains(page, "🧳 5 KG")
        self.assertContains(page, 'class="muted match-meta"')
        self.assertContains(page, "match-head")
        self.assertContains(page, "match-weights")
        self.assertContains(page, "listing-card")
        self.assertNotContains(page, "tgui-icon-chevron")
        self.assertNotContains(page, "Travel:")
        self.assertNotContains(page, "chip-demand")
        self.assertNotContains(page, "chip-supply")
        self.assertNotContains(page, "2027-09-01")
        detail = self.client.get(f"/app/matches/{Match.objects.get().pk}/")
        self.assertContains(detail, "Carry from Sep 1 to Sep 15")

    def test_request_pdp_shows_matches_and_both_sides_can_request(self) -> None:
        demand = create_item_request(self.user, DEMAND_PAYLOAD)
        supply = create_item_request(self.other, SUPPLY_PAYLOAD)
        from matching.models import Match, MatchStatus

        match = Match.objects.get()
        _login(self.client, self.user)
        page = self.client.get(f"/app/requests/{demand.pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Your request")
        self.assertContains(page, "Matched requests")
        self.assertContains(page, 'class="dl"')
        self.assertContains(page, "Desired date")
        self.assertContains(page, "Flight date")
        self.assertContains(page, "chip-demand")
        self.assertContains(page, "chip-supply")
        self.assertContains(page, "🧳 2 KG")
        self.assertContains(page, "🧳 5 KG")
        self.assertContains(page, "✈️ 2027-09-10")
        self.assertContains(page, "Carry from Sep 1 to Sep 15")
        self.assertContains(page, "Posted by Ali")
        self.assertContains(page, 'name="action" value="accept"')
        self.assertContains(page, 'name="action" value="reject"')
        self.assertContains(page, 'name="match_id" value="%s"' % match.pk)
        self.assertContains(page, "Request")
        self.assertNotContains(page, ">Accept")
        requested = self.client.post(
            f"/app/requests/{demand.pk}/",
            {"action": "accept", "match_id": str(match.pk)},
        )
        self.assertEqual(requested.status_code, 302)
        self.assertEqual(requested["Location"], f"/app/requests/{demand.pk}/")
        waiting = self.client.get(f"/app/requests/{demand.pk}/")
        self.assertContains(waiting, "Waiting for them to accept")
        match.refresh_from_db()
        self.assertEqual(match.status, MatchStatus.ACCEPTED_BY_DEMAND)

        _login(self.client, self.other)
        other_page = self.client.get(f"/app/requests/{supply.pk}/")
        self.assertContains(other_page, "Your request")
        self.assertContains(other_page, "Posted by Leila")
        self.assertContains(other_page, "They requested this match")
        self.assertContains(other_page, "Accept")
        accepted = self.client.post(
            f"/app/requests/{supply.pk}/",
            {"action": "accept", "match_id": str(match.pk)},
        )
        self.assertEqual(accepted.status_code, 302)
        match.refresh_from_db()
        self.assertEqual(match.status, MatchStatus.CONNECTED)
        connected = self.client.get(f"/app/requests/{supply.pk}/")
        self.assertContains(connected, "Connected")

    def test_request_pdp_reject_stays_on_request(self) -> None:
        demand = create_item_request(self.user, DEMAND_PAYLOAD)
        create_item_request(self.other, SUPPLY_PAYLOAD)
        from matching.models import Match, MatchStatus

        match = Match.objects.get()
        _login(self.client, self.user)
        rejected = self.client.post(
            f"/app/requests/{demand.pk}/",
            {"action": "reject", "match_id": str(match.pk)},
        )
        self.assertEqual(rejected.status_code, 302)
        self.assertEqual(rejected["Location"], f"/app/requests/{demand.pk}/")
        page = self.client.get(f"/app/requests/{demand.pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Posted by Ali")
        match.refresh_from_db()
        self.assertEqual(match.status, MatchStatus.REJECTED)

    def test_match_pdp_reject_does_not_404(self) -> None:
        create_item_request(self.user, DEMAND_PAYLOAD)
        create_item_request(self.other, SUPPLY_PAYLOAD)
        from matching.models import Match, MatchStatus

        match = Match.objects.get()
        _login(self.client, self.user)
        rejected = self.client.post(f"/app/matches/{match.pk}/", {"action": "reject"})
        self.assertEqual(rejected.status_code, 302)
        self.assertEqual(rejected["Location"], "/app/matches/")
        page = self.client.get(rejected["Location"])
        self.assertEqual(page.status_code, 200)
        leftover = self.client.get(f"/app/matches/{match.pk}/")
        self.assertEqual(leftover.status_code, 302)
        self.assertEqual(leftover["Location"], "/app/matches/")
        match.refresh_from_db()
        self.assertEqual(match.status, MatchStatus.REJECTED)

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
        self.assertEqual(page.status_code, 302)
        self.assertEqual(page["Location"], "/app/matches/")
        _login(self.client, self.other)
        other_page = self.client.get(f"/app/matches/{match.pk}/")
        self.assertEqual(other_page.status_code, 302)
        self.assertEqual(other_page["Location"], "/app/matches/")

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
