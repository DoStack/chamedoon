from __future__ import annotations

import re
from pathlib import Path

from django.test import Client, override_settings
from rest_framework.test import APITestCase

from item_requests.models import ItemRequest, RequestType
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


def _list(client: Client, path: str):
    return client.get(path, HTTP_X_KOOLBAR_LIST="1")


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
        self.assertContains(landing, "Connecting Travelers &amp; Senders Across Borders")
        self.assertContains(landing, "100% Free for Everyone")
        self.assertContains(landing, "hero-actions")
        self.assertContains(landing, 'href="/browse/"')
        self.assertContains(landing, 'id="share-site"')
        self.assertContains(landing, "header-share")
        self.assertContains(landing, "miniapp/icons/24/share.svg")
        self.assertContains(landing, "miniapp/icons/28/package.svg")
        self.assertContains(landing, "miniapp/icons/28/luggage.svg")
        self.assertContains(landing, "Share")
        self.assertNotContains(landing, "landing-share-btn")
        self.assertNotContains(landing, "landing-share-label")
        self.assertContains(landing, "How it works?")
        self.assertContains(landing, 'href="/how-it-works/"')
        self.assertContains(landing, "home-community")
        self.assertContains(landing, "https://t.me/+26pUh8_5u0w1MTVk")
        self.assertNotContains(landing, "Need to send something abroad?")
        self.assertNotContains(landing, "Need to send?")
        self.assertNotContains(landing, "landing-about")
        self.assertNotContains(landing, "about-accordion")
        self.assertNotContains(landing, "listing-skeleton")
        self.assertContains(landing, "miniapp/favicon.svg")
        self.assertContains(landing, "apple-touch-icon.png")
        self.assertContains(landing, "site.webmanifest")
        how_it_works = self.client.get("/how-it-works/")
        self.assertEqual(how_it_works.status_code, 200)
        self.assertContains(how_it_works, "How It Works")
        self.assertContains(how_it_works, "Need to send?")
        self.assertContains(how_it_works, "Have extra luggage space?")
        self.assertContains(how_it_works, "Find a match")
        self.assertContains(how_it_works, "connect privately on Telegram")
        self.assertContains(how_it_works, "No fees")
        self.assertContains(how_it_works, "No commission")
        self.assertContains(how_it_works, 'href="/"')
        home = self.client.get("/app/")
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, 'id="tg-form"')
        self.assertContains(home, 'name="startapp"')
        about = self.client.get("/app/about/")
        self.assertEqual(about.status_code, 200)
        self.assertContains(about, "Need to send?")
        self.assertContains(about, 'href="/app/"')

    @override_settings(TELEGRAM_CHANNEL_URL="", TELEGRAM_CHANNEL_USERNAME="koolbar_channel")
    def test_landing_channel_icon_uses_invite_not_username(self) -> None:
        landing = self.client.get("/")
        self.assertContains(landing, "https://t.me/+26pUh8_5u0w1MTVk")
        self.assertNotContains(landing, "https://t.me/koolbar_channel")

    @override_settings(
        TELEGRAM_CHANNEL_URL="https://t.me/koolbar_market",
        TELEGRAM_CHANNEL_USERNAME="koolbar_market",
        TELEGRAM_GROUP_USERNAME="koolbar_chat",
    )
    def test_home_shows_channel_and_group_icons(self) -> None:
        _login(self.client, self.user)
        home = self.client.get("/app/")
        self.assertContains(home, "home-community")
        self.assertContains(home, "https://t.me/koolbar_market")
        self.assertContains(home, "https://t.me/koolbar_chat")
        self.assertContains(home, "miniapp/icons/24/channel.svg")
        self.assertContains(home, "miniapp/icons/24/chat.svg")
        self.assertContains(home, 'aria-label="Channel"')
        self.assertContains(home, 'aria-label="Group"')
        landing = self.client.get("/")
        self.assertContains(landing, "home-community")
        self.assertContains(landing, "https://t.me/koolbar_market")
        self.assertContains(landing, "https://t.me/koolbar_chat")

    @override_settings(TELEGRAM_BOT_USERNAME="CB_koolbarbot", TELEGRAM_MINI_APP_SHORT_NAME="app")
    def test_public_browse_plp_and_pdp_open_telegram_to_match(self) -> None:
        supply = create_item_request(self.other, {**SUPPLY_PAYLOAD, "origin_city": "mashhad"})
        landing = self.client.get("/")
        self.assertContains(landing, "https://t.me/CB_koolbarbot/app?startapp=demand")
        self.assertContains(landing, "https://t.me/CB_koolbarbot/app?startapp=supply")
        self.assertContains(landing, 'href="/browse/"')
        page = self.client.get("/browse/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "listing-skeleton")
        self.assertContains(page, "browse-filters")
        self.assertContains(page, 'class="browse-page"')
        self.assertContains(page, 'id="filter-sheet"')
        self.assertContains(page, 'id="open-filters"')
        self.assertContains(page, "<details")
        self.assertNotContains(page, "Filters (")
        self.assertNotContains(page, 'id="clear-filters"')
        self.assertNotContains(page, "Show results")
        self.assertContains(page, "miniapp/icons/24/cancel.svg")
        self.assertContains(page, 'href="/"')
        self.assertNotContains(page, 'id="share-site"')
        listing = _list(self.client, "/browse/")
        self.assertContains(listing, f'href="/browse/{supply.pk}/"')
        self.assertContains(listing, "listing-card")
        self.assertNotContains(listing, "Can carry personal items")
        empty = _list(self.client, "/browse/?origin_city=no-such-city")
        self.assertContains(empty, "listing-empty")
        self.assertContains(empty, "No open requests match these filters.")
        self.assertContains(empty, "Clear filters")
        self.assertContains(empty, "listing-empty-clear")
        filtered_page = self.client.get("/browse/?origin_city=no-such-city")
        self.assertContains(filtered_page, 'class="browse-filter-count"')
        self.assertContains(filtered_page, "(1)")
        detail = self.client.get(f"/browse/{supply.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Request match in Telegram")
        self.assertContains(detail, "Can carry personal items")
        self.assertContains(detail, "pdp-card")
        self.assertContains(detail, "chip-supply")
        self.assertContains(detail, "Capacity")
        self.assertContains(detail, "cat-chips")
        self.assertContains(detail, f"https://t.me/CB_koolbarbot?startapp=explore_{supply.pk}")
        self.assertNotContains(detail, "my_request_id")
        self.assertContains(detail, 'href="/browse/"')

    def test_farsi_pages_preload_iransans(self) -> None:
        self.client.cookies[LOCALE_COOKIE] = "fa"
        page = self.client.get("/app/login/")
        self.assertContains(page, 'lang="fa"')
        self.assertContains(page, 'dir="rtl"')
        self.assertContains(page, "IRANSansWeb_FaNum.woff2")
        landing = self.client.get("/")
        self.assertContains(landing, "چطور کار می‌کند؟")
        self.assertContains(landing, 'href="/how-it-works/"')
        self.assertNotContains(landing, "می‌خواهید چیزی را به کشور دیگری بفرستید؟")
        guide = self.client.get("/how-it-works/")
        self.assertContains(guide, "می‌خواهید چیزی ارسال کنید؟")
        self.assertContains(guide, "جای خالی در چمدان دارید؟")
        self.assertContains(guide, "کاملاً رایگان")
        self.assertContains(landing, "اشتراک‌گذاری")

    def test_farsi_routes_keep_origin_then_destination(self) -> None:
        create_item_request(self.other, SUPPLY_PAYLOAD)
        self.client.cookies[LOCALE_COOKIE] = "fa"
        _login(self.client, self.user)
        listing = _list(self.client, "/app/explore/")
        self.assertContains(listing, " → ")
        self.assertNotContains(listing, " ← ")
        html = listing.content.decode()
        self.assertLess(html.find("تهران"), html.find("تورنتو"))
        self.client.cookies[LOCALE_COOKIE] = "en"
        listing_en = _list(self.client, "/app/explore/")
        self.assertContains(listing_en, " → ")
        self.assertNotContains(listing_en, " ← ")

    def test_pages_follow_telegram_color_scheme(self) -> None:
        page = self.client.get("/app/login/")
        html = page.content.decode()
        self.assertIn("telegram-web-app.js", html)
        self.assertIn("miniapp/startapp.js", html)
        self.assertIn("miniapp/theme.js", html)
        self.assertLess(html.index("telegram-web-app.js"), html.index("miniapp/startapp.js"))
        self.assertLess(html.index("miniapp/startapp.js"), html.index("miniapp/theme.js"))
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
        self.assertIn(".chip-demand", app_css)
        self.assertIn(".checks-grid", app_css)
        self.assertIn(".cat-chips", app_css)

    def test_unified_back_navigation_is_wired(self) -> None:
        page = self.client.get("/app/login/")
        html = page.content.decode()
        self.assertIn("miniapp/nav.js", html)
        self.assertLess(html.index("miniapp/theme.js"), html.index("miniapp/nav.js"))
        self.assertContains(page, "data-nav-back")
        startapp_js = (
            Path(__file__).resolve().parents[1] / "miniapp/static/miniapp/startapp.js"
        ).read_text()
        self.assertIn("initDataUnsafe.start_param", startapp_js)
        self.assertIn("explore_", startapp_js)
        self.assertIn("window.koolbarStartParam", startapp_js)
        self.assertIn("searchParams.set(\"startapp\"", startapp_js)
        self.assertIn("pathname.indexOf(\"/app/login\")", startapp_js)
        theme_js = (
            Path(__file__).resolve().parents[1] / "miniapp/static/miniapp/theme.js"
        ).read_text()
        self.assertIn("koolbarStartParam", theme_js)
        nav_js = (
            Path(__file__).resolve().parents[1] / "miniapp/static/miniapp/nav.js"
        ).read_text()
        self.assertIn("function goBack", nav_js)
        self.assertIn("isMiniAppPath", nav_js)
        self.assertIn("window.koolbarGoBack", nav_js)
        self.assertIn("window.koolbarRegisterBack", nav_js)
        self.assertIn("window.koolbarSyncBack", nav_js)
        self.assertIn("BackButton", nav_js)
        self.assertIn("backButtonClicked", nav_js)
        self.assertIn("sheet-open", nav_js)
        self.assertIn("history.back", nav_js)
        self.assertIn("offClick", nav_js)
        self.assertIn("openTelegramLink", nav_js)
        landing = self.client.get("/")
        self.assertContains(landing, "miniapp/nav.js")
        self.assertNotContains(landing, "data-nav-back")
        _login(self.client, self.user)
        home = self.client.get("/app/")
        self.assertContains(home, "miniapp/nav.js")
        self.assertNotContains(home, "data-nav-back")
        demand = create_item_request(self.user, DEMAND_PAYLOAD)
        detail = self.client.get(f"/app/requests/{demand.pk}/")
        self.assertContains(detail, "data-nav-back")
        about = self.client.get("/app/about/")
        self.assertContains(about, "data-nav-back")
        self.assertContains(about, 'href="/app/"')
        explore = self.client.get("/app/explore/")
        self.assertContains(explore, "data-nav-back")
        self.assertContains(explore, "data-close-filters")
        browse = self.client.get("/browse/")
        self.assertContains(browse, "data-nav-back")
        self.assertContains(browse, 'href="/"')

    @override_settings(DEBUG=False, TELEGRAM_BOT_USERNAME="CB_koolbarbot")
    def test_production_login_asks_to_open_telegram(self) -> None:
        landing = self.client.get("/")
        self.assertContains(landing, "https://t.me/CB_koolbarbot/app")
        page = self.client.get("/app/login/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Open Koolbar in Telegram")
        self.assertContains(page, "Open Mini App")
        self.assertContains(page, "Open the Koolbar bot")
        self.assertContains(page, "https://t.me/CB_koolbarbot")
        self.assertContains(page, "openTelegramLink")
        self.assertContains(page, "openMiniAppInPlace")
        self.assertNotContains(page, "window.location.href = botChat")

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

    def test_startapp_explore_opens_listing_pdp(self) -> None:
        demand = create_item_request(self.user, DEMAND_PAYLOAD)
        _login(self.client, self.other)
        response = self.client.get(f"/app/?startapp=explore_{demand.pk}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/app/explore/{demand.pk}/")

    def test_explore_login_keeps_startapp_for_listing_pdp(self) -> None:
        demand = create_item_request(self.user, DEMAND_PAYLOAD)
        response = self.client.get(f"/app/explore/{demand.pk}/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/app/login/?startapp=explore_{demand.pk}")

    def test_create_demand_from_html_form(self) -> None:
        _login(self.client, self.user)
        response = self.client.post(
            "/app/demand/new/",
            {
                "origin_country": "IR",
                "origin_city": "tehran",
                "destination_country": "CA",
                "destination_city": "toronto",
                "destination_cities": ["toronto", "vancouver"],
                "desired_date": "2027-09-07",
                "weight_kg": "2",
                "item_category_codes": ["CLOTHES"],
                "description": "Bag",
            },
        )
        self.assertEqual(response.status_code, 302, response.content)
        self.assertRegex(response["Location"], r"^/app/requests/\d+/created/$")
        created = self.client.get(response["Location"])
        self.assertContains(created, "No matching requests right now.")
        self.assertContains(created, "Browse open requests")
        self.assertContains(created, "My requests")
        self.assertNotContains(created, "Your request</h2>")
        item = ItemRequest.objects.get(user=self.user, type=RequestType.DEMAND)
        self.assertEqual(item.destination_city, "toronto")
        self.assertEqual(item.destination_cities, [{"country": "CA", "city": "toronto"}])
        listing = _list(self.client, "/app/requests/")
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
        page = self.client.get("/app/requests/")
        self.assertContains(page, "listing-skeleton")
        self.assertContains(page, "miniapp/icons/28/package.svg")
        self.assertContains(page, "miniapp/icons/28/luggage.svg")

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
        html = page.content.decode()
        self.assertLess(html.find('data-chip="cargo"'), html.find('data-chip="trip"'))
        self.assertContains(page, "revealActiveChip")
        self.assertContains(page, "scrollIntoView")
        self.assertContains(page, "Confirm and publish")
        self.assertContains(page, "Check this summary")
        self.assertContains(page, 'data-step="review"')
        self.assertContains(page, 'id="review-table"')
        self.assertContains(page, 'class="dl"')
        self.assertContains(page, "Desired date")
        self.assertContains(page, 'id="review-route"')
        self.assertContains(page, 'const arrow = "→"')
        self.assertContains(page, "option-list")
        self.assertContains(page, 'id="city-custom"')
        self.assertContains(page, "City not listed")
        self.assertContains(page, "Use this city")
        self.assertContains(page, "isOriginCity")
        self.assertContains(page, "is-disabled")
        self.assertContains(page, "data-same-city")
        self.assertContains(page, '"code": "IR"')
        self.assertContains(page, r"\ud83c\uddee\ud83c\uddf7")
        self.assertContains(page, 'name="origin_country"')
        self.assertContains(page, 'name="destination_city"')
        self.assertContains(page, "requestType === \"SUPPLY\"")
        self.assertContains(page, 'next === "dest-city" && multiDest')
        self.assertContains(page, "function asCityValues")
        self.assertContains(page, "typeof value === \"string\"")
        self.assertContains(page, "if (!multiDest) clearDestCities()")
        self.assertContains(page, "Choose the destination city.")
        self.assertNotContains(page, "Choose one or more destination cities, then tap continue.")
        self.assertNotContains(page, ": [selected].filter(Boolean)")
        self.assertContains(page, 'data-kg-field')
        self.assertContains(page, 'name="weight_kg"')
        self.assertNotContains(page, "data-draft-key")
        self.assertNotContains(page, "localStorage.setItem")
        self.assertNotContains(page, "sessionStorage.setItem")
        self.assertContains(page, '<input type="date" name="desired_date"')
        self.assertNotContains(page, '<input type="date" name="flight_date"')
        self.assertContains(page, 'id="cargo-categories"')
        self.assertContains(page, "checks-grid")
        self.assertContains(page, "setReviewChips")
        self.assertContains(page, "👕")
        self.assertContains(page, "📄")
        self.assertContains(page, "koolbarRegisterBack")
        self.assertContains(page, "stepWizardBack")
        self.assertContains(page, "data-nav-back")
        self.assertNotContains(page, 'id="wizard-back"')
        self.assertNotContains(page, "btn-back")

    def test_supply_form_starts_with_all_items_carried(self) -> None:
        _login(self.client, self.user)
        page = self.client.get("/app/supply/new/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "wizard-form")
        self.assertContains(page, 'id="city-custom"')
        self.assertContains(page, "City not listed")
        html = page.content.decode()
        self.assertLess(html.find('data-chip="trip"'), html.find('data-chip="cargo"'))
        self.assertContains(page, 'class="wizard-step"', count=4)
        self.assertContains(page, "cargo-board")
        self.assertContains(page, "I can carry")
        self.assertContains(page, "I will not carry")
        self.assertContains(page, "toggleDestCity")
        self.assertContains(page, "Choose one or more destination cities, then tap continue.")
        self.assertContains(page, 'next === "dest-city" && multiDest')
        self.assertContains(page, 'name="capacity_kg"')
        self.assertNotContains(page, 'name="capacity_kg" value="" inputmode="decimal" data-required="1" data-kg-field')
        self.assertNotContains(page, 'data-kg-hint></span>')
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
        self.assertRegex(response["Location"], r"^/app/requests/\d+/created/$")
        detail = self.client.get(response["Location"])
        self.assertContains(detail, "Your request is live")
        self.assertNotContains(detail, "Your request</h2>")
        self.assertNotContains(detail, 'class="dl"')
        self.assertContains(detail, "Browse open requests")
        self.assertContains(detail, "My requests")

    def test_wizard_shows_matching_supplies_and_filtered_list(self) -> None:
        create_item_request(self.other, {**SUPPLY_PAYLOAD, "capacity_kg": "3.00", "description": "Tight bag"})
        create_item_request(self.other, {**SUPPLY_PAYLOAD, "capacity_kg": "5.00", "description": "Usual bag"})
        create_item_request(self.other, {**SUPPLY_PAYLOAD, "capacity_kg": "10.00", "description": "Big bag"})
        create_item_request(self.other, {**SUPPLY_PAYLOAD, "capacity_kg": "20.00", "description": "Huge suitcase"})
        create_item_request(
            self.other,
            {**SUPPLY_PAYLOAD, "capacity_kg": "1.00", "description": "Too small"},
        )
        create_item_request(
            self.other,
            {**SUPPLY_PAYLOAD, "flight_date": "2027-09-01", "description": "Already flying"},
        )
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
        self.assertRegex(response["Location"], r"^/app/requests/\d+/created/$")
        picks = self.client.get(response["Location"])
        self.assertContains(picks, "Your request is live")
        self.assertContains(picks, "We found 4 matching requests for your needs.")
        self.assertContains(picks, "🧳 3 KG")
        self.assertContains(picks, "🧳 5 KG")
        self.assertContains(picks, "🧳 10 KG")
        self.assertContains(picks, "🧳 20 KG")
        self.assertNotContains(picks, "🧳 1 KG")
        self.assertNotContains(picks, "✈️ 2027-09-01")
        self.assertContains(picks, "See all matching requests")
        self.assertContains(picks, "My requests")
        self.assertContains(picks, "/app/explore/?")
        self.assertContains(picks, "type=SUPPLY")
        self.assertContains(picks, "flight_after=2027-09-07")
        self.assertContains(picks, "weight_kg=2")
        self.assertNotContains(picks, "Your request</h2>")
        pk = response["Location"].split("/")[3]
        pdp = self.client.get(f"/app/requests/{pk}/")
        self.assertContains(pdp, "Suggested matches")
        self.assertNotContains(pdp, "Your request is live")
        self.assertContains(pdp, "🧳 3 KG")

    def test_demand_wizard_survives_imported_matches_without_telegram_spam(self) -> None:
        from unittest.mock import patch

        create_item_request(
            self.other,
            {**SUPPLY_PAYLOAD, "description": "Imported bag"},
            imported=True,
            source_url="https://t.me/koolbar_international/99",
        )
        _login(self.client, self.user)
        with patch("notifications.services.notify_new_match") as notify:
            with self.captureOnCommitCallbacks(execute=True):
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
        self.assertRegex(response["Location"], r"^/app/requests/\d+/created/$")
        notify.assert_not_called()
        picks = self.client.get(response["Location"])
        self.assertEqual(picks.status_code, 200)
        self.assertContains(picks, "We found 1 matching requests for your needs.")
        self.assertContains(picks, "/app/explore/")
        self.assertContains(picks, "Your request is live")
        self.assertContains(picks, "See all matching requests")
        self.assertNotContains(picks, "Your request</h2>")

    def test_supply_created_page_lists_matching_demands(self) -> None:
        create_item_request(
            self.other,
            {**DEMAND_PAYLOAD, "destination_city": "toronto", "description": "To Toronto"},
        )
        create_item_request(
            self.other,
            {**DEMAND_PAYLOAD, "destination_city": "vancouver", "description": "To Vancouver"},
        )
        create_item_request(
            self.other,
            {**DEMAND_PAYLOAD, "desired_date": "2027-09-20", "description": "After flight"},
        )
        create_item_request(
            self.other,
            {**DEMAND_PAYLOAD, "weight_kg": "20.00", "description": "Too heavy"},
        )
        create_item_request(
            self.other,
            {**DEMAND_PAYLOAD, "item_category_codes": ["MEDICINE"], "description": "Medicine only"},
        )
        _login(self.client, self.user)
        response = self.client.post(
            "/app/supply/new/",
            {
                "origin_country": "IR",
                "origin_city": "tehran",
                "destination_country": "CA",
                "destination_city": "vancouver",
                "destination_cities": ["toronto", "vancouver"],
                "flight_date": "2027-09-10",
                "date_from": "2027-09-01",
                "date_to": "2027-09-15",
                "capacity_kg": "8",
                "item_category_codes": ["CLOTHES", "DOCUMENTS", "PERSONAL_ITEMS"],
                "description": "Trip bag",
            },
        )
        self.assertEqual(response.status_code, 302, response.content)
        picks = self.client.get(response["Location"])
        self.assertContains(picks, "We found 2 matching requests for your needs.")
        self.assertContains(picks, "Toronto")
        self.assertContains(picks, "Vancouver")
        self.assertNotContains(picks, "To Toronto")
        self.assertNotContains(picks, "To Vancouver")
        self.assertNotContains(picks, "After flight")
        self.assertNotContains(picks, "Too heavy")
        self.assertNotContains(picks, "Medicine only")
        self.assertContains(picks, "type=DEMAND")
        self.assertContains(picks, "date_to=2027-09-10")
        self.assertContains(picks, "destination=CA%3Atoronto")
        self.assertContains(picks, "destination=CA%3Avancouver")
        self.assertContains(picks, "See all matching requests")
        self.assertContains(picks, "My requests")
        self.assertNotContains(picks, "Your request</h2>")

    def test_explore_and_propose_match(self) -> None:
        demand = create_item_request(self.user, DEMAND_PAYLOAD)
        supply = create_item_request(self.other, {**SUPPLY_PAYLOAD, "origin_city": "mashhad"})
        _login(self.client, self.user)
        page = self.client.get("/app/explore/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "listing-skeleton")
        self.assertContains(page, "listing-card-skel")
        self.assertContains(page, "list-loader.js")
        self.assertNotContains(page, "Browse open send")
        self.assertNotContains(page, "Posted by Ali")
        self.assertNotContains(page, "Request match")
        self.assertNotContains(page, "Carry from")
        self.assertContains(page, 'class="search-trigger')
        self.assertContains(page, 'id="open-filters"')
        self.assertContains(page, 'id="filter-sheet"')
        self.assertContains(page, "Show results")
        self.assertContains(page, 'id="clear-filters"')
        self.assertContains(page, 'id="apply-filters"')
        self.assertNotContains(page, "browse-filters")
        self.assertNotContains(page, 'class="browse-page"')
        self.assertContains(page, "miniapp/icons/24/search.svg")
        self.assertContains(page, "miniapp/icons/24/cancel.svg")
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
        listing = _list(self.client, "/app/explore/")
        self.assertContains(listing, "chip-supply")
        self.assertContains(listing, "🧳 5 KG")
        self.assertContains(listing, "✈️ 2027-09-10")
        self.assertContains(listing, "👕")
        self.assertContains(listing, "⛔")
        self.assertContains(listing, f'href="/app/explore/{supply.pk}/"')
        self.assertContains(listing, "listing-card")
        self.assertNotContains(listing, "Can carry personal items")
        home = self.client.get("/app/")
        self.assertContains(home, "Signed in as")
        self.assertContains(home, "How it works?")
        self.assertContains(home, 'href="/app/about/"')
        guide = self.client.get("/app/about/")
        self.assertEqual(guide.status_code, 200)
        self.assertContains(guide, "How It Works")
        self.assertContains(guide, "Need to send?")
        self.assertContains(guide, "Have extra luggage space?")
        self.assertContains(guide, "100% Free")
        self.assertContains(guide, "No hidden charges")
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
        self.assertContains(detail, "Waiting for approval")
        self.assertContains(detail, "Cancel request")
        self.assertNotContains(detail, ">Accept<")
        from matching.models import Match

        match = Match.objects.get()
        _login(self.client, self.other)
        other_page = self.client.get(connect["Location"])
        self.assertContains(other_page, "Accept")
        self.assertContains(other_page, "They requested this match")
        accept_match(match, self.other)

    def test_custom_city_shows_in_explore_results_and_filters(self) -> None:
        demand = create_item_request(self.user, {**DEMAND_PAYLOAD, "origin_city": "Bandar"})
        _login(self.client, self.user)
        page = self.client.get("/app/explore/")
        self.assertContains(page, "Bandar")
        self.assertContains(page, 'data-city="bandar"')
        listing = _list(self.client, "/app/explore/")
        self.assertContains(listing, "Bandar")
        self.assertContains(listing, "Yours")
        self.assertContains(listing, f'href="/app/requests/{demand.pk}/"')
        filtered = _list(self.client, "/app/explore/?origin_country=IR&origin_city=bandar")
        self.assertContains(filtered, f'href="/app/requests/{demand.pk}/"')

        other = Client()
        _login(other, self.other)
        other_page = other.get("/app/explore/")
        self.assertContains(other_page, "Bandar")
        other_listing = _list(other, "/app/explore/")
        self.assertContains(other_listing, "Bandar")
        self.assertContains(other_listing, f'href="/app/explore/{demand.pk}/"')
        self.assertNotContains(other_listing, "Yours")

        browse = self.client.get("/browse/")
        self.assertContains(browse, "Bandar")
        browse_listing = _list(self.client, "/browse/")
        self.assertContains(browse_listing, "Bandar")
        self.assertContains(browse_listing, f'href="/browse/{demand.pk}/"')

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
        page = _list(self.client, "/app/matches/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "🇮🇷")
        self.assertContains(page, "🇨🇦")
        self.assertContains(page, "Tehran")
        self.assertContains(page, "Toronto")
        self.assertContains(page, " → ")
        self.assertNotContains(page, " ← ")
        self.assertContains(page, "✈️ 2027-09-10")
        self.assertNotContains(page, "Carry from Sep 1 to Sep 15")
        self.assertContains(page, "🧳 2 KG")
        self.assertContains(page, "🧳 5 KG")
        self.assertContains(page, 'class="muted match-meta"')
        self.assertContains(page, "match-head")
        self.assertContains(page, "match-weights")
        self.assertContains(page, "listing-card")
        shell = self.client.get("/app/matches/")
        self.assertContains(shell, "Active")
        self.assertContains(shell, "History")
        self.assertNotContains(page, "tgui-icon-chevron")
        self.assertNotContains(page, "Travel:")
        self.assertNotContains(page, "chip-demand")
        self.assertNotContains(page, "chip-supply")
        self.assertNotContains(page, "2027-09-01")
        detail = self.client.get(f"/app/matches/{Match.objects.get().pk}/")
        self.assertContains(detail, "Carry from Sep 1 to Sep 15")

    def test_request_pdp_shows_owner_accept_and_requester_waiting(self) -> None:
        demand = create_item_request(self.user, DEMAND_PAYLOAD)
        supply = create_item_request(self.other, SUPPLY_PAYLOAD)
        from matching.models import Match, MatchStatus

        match = Match.objects.get()
        _login(self.client, self.user)
        page = self.client.get(f"/app/requests/{demand.pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Your request")
        self.assertContains(page, "Suggested matches")
        self.assertContains(page, "These are the best matches")
        self.assertContains(page, "Strong match")
        self.assertNotContains(page, "Pick a match")
        self.assertContains(page, 'class="dl"')
        self.assertContains(page, "Desired date")
        self.assertContains(page, "Weight")
        self.assertContains(page, "<dt>Weight</dt>")
        self.assertContains(page, "cat-chips")
        self.assertContains(page, "chip-cat")
        self.assertContains(page, "pdp-card")
        self.assertContains(page, "pdp-head")
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
        self.assertContains(page, "Accept")
        self.assertContains(page, "My requests")
        self.assertContains(page, 'href="/app/requests/"')
        self.assertContains(page, "request-tools")
        self.assertContains(page, "btn-muted")
        self.assertContains(page, "Edit")
        self.assertContains(page, "Delete")
        self.assertNotContains(page, "Cancel request")
        accepted = self.client.post(
            f"/app/requests/{demand.pk}/",
            {"action": "accept", "match_id": str(match.pk)},
        )
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(accepted["Location"], f"/app/matches/{match.pk}/")
        match.refresh_from_db()
        self.assertEqual(match.status, MatchStatus.ACCEPTED)

        _login(self.client, self.other)
        other_page = self.client.get(f"/app/requests/{supply.pk}/")
        self.assertContains(other_page, "Your request")
        self.assertContains(other_page, "Posted by Leila")
        self.assertContains(other_page, "Accepted")

    def test_request_pdp_reject_asks_to_close_listing(self) -> None:
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
        self.assertEqual(rejected["Location"], f"/app/matches/{match.pk}/?close=1")
        page = self.client.get(rejected["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Close your listing?")
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
        self.assertEqual(rejected["Location"], f"/app/matches/{match.pk}/?close=1")
        page = self.client.get(rejected["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Close your listing?")
        leftover = self.client.get(f"/app/matches/{match.pk}/")
        self.assertEqual(leftover.status_code, 200)
        match.refresh_from_db()
        self.assertEqual(match.status, MatchStatus.REJECTED)
        closed = self.client.post(f"/app/matches/{match.pk}/", {"action": "close_listing"})
        self.assertEqual(closed.status_code, 302)
        self.assertEqual(closed["Location"], "/app/matches/?history=1")
        match.owner_request().refresh_from_db()
        self.assertEqual(match.owner_request().status, "CLOSED")
        history = _list(self.client, "/app/matches/?history=1")
        self.assertContains(history, "Rejected")

    def test_requester_can_cancel_pending_match(self) -> None:
        create_item_request(self.user, DEMAND_PAYLOAD)
        create_item_request(self.other, SUPPLY_PAYLOAD)
        from matching.models import Match, MatchStatus

        match = Match.objects.get()
        _login(self.client, self.other)
        page = self.client.get(f"/app/matches/{match.pk}/")
        self.assertContains(page, "Waiting for approval")
        self.assertContains(page, "Cancel request")
        cancelled = self.client.post(f"/app/matches/{match.pk}/", {"action": "cancel"})
        self.assertEqual(cancelled.status_code, 302)
        match.refresh_from_db()
        self.assertEqual(match.status, MatchStatus.CANCELLED)
        history = _list(self.client, "/app/matches/?history=1")
        self.assertContains(history, "Cancelled")

    def test_connected_match_shows_telegram_id_and_dm_link(self) -> None:
        self.other.telegram_username = "ali_carry"
        self.other.save(update_fields=["telegram_username"])
        create_item_request(self.user, DEMAND_PAYLOAD)
        create_item_request(self.other, SUPPLY_PAYLOAD)
        from matching.models import Match

        match = Match.objects.get()
        accept_match(match, self.user)
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
        cancel_item_request(match.demand_request)
        _login(self.client, self.user)
        page = self.client.get(f"/app/matches/{match.pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Message on Telegram")
        self.assertNotContains(page, 'id="dm-link"')
        _login(self.client, self.other)
        other_page = self.client.get(f"/app/matches/{match.pk}/")
        self.assertEqual(other_page.status_code, 200)
        self.assertNotContains(other_page, "Message on Telegram")
        self.assertNotContains(other_page, 'id="dm-link"')

    def test_supply_close_asks_if_package_was_sent(self) -> None:
        supply = create_item_request(self.other, SUPPLY_PAYLOAD)
        _login(self.client, self.other)
        page = self.client.get(f"/app/requests/{supply.pk}/")
        self.assertContains(page, "My requests")
        self.assertContains(page, "Delete")
        self.assertContains(page, "request-tools")
        self.assertNotContains(page, "Cancel request")
        self.assertNotContains(page, "Close request")
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
