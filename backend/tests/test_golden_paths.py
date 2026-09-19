from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from django.test import Client, override_settings
from rest_framework.test import APITestCase

from item_requests.models import RequestStatus
from item_requests.seed import seed_catalog
from item_requests.services import create_item_request
from matching.contact import intro_draft_for_match, telegram_dm_contact
from matching.models import Match, MatchRating, MatchStatus
from miniapp.auth import SESSION_USER_KEY
from notifications.telegram import connected_markup
from tests.helpers import TEST_SECRET, make_user
from tests.test_requests import DEMAND_PAYLOAD, SUPPLY_PAYLOAD

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def _login(client: Client, user) -> None:
    session = client.session
    session[SESSION_USER_KEY] = user.pk
    session.save()


def _draft_from_url(url: str) -> str:
    return unquote(parse_qs(urlparse(url).query)["text"][0])


def _hrefs(html: str) -> list[str]:
    return re.findall(r'href="([^"]+)"', html)


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=True)
class MessageOnTelegramContractTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.sender = make_user(
            telegram_user_id=88001,
            first_name="Leila",
            telegram_username="leila_send",
        )
        self.traveler = make_user(
            telegram_user_id=88002,
            first_name="Ali",
            telegram_username="ali_carry",
        )
        create_item_request(self.sender, DEMAND_PAYLOAD)
        create_item_request(self.traveler, SUPPLY_PAYLOAD)
        self.match = Match.objects.get()

    def test_miniapp_dm_href_is_https_tme_with_sender_draft(self) -> None:
        _login(self.client, self.sender)
        page = self.client.get(f"/app/matches/{self.match.pk}/")
        html = page.content.decode()
        href = next(url for url in _hrefs(html) if url.startswith("https://t.me/ali_carry?text="))
        draft = _draft_from_url(href)
        self.assertEqual(self.match.status, MatchStatus.CONNECTED)
        self.assertContains(page, "Message on Telegram")
        self.assertContains(page, 'id="dm-link"')
        self.assertIn("من یه بار دارم", draft)
        self.assertIn("Ali", draft)
        self.assertIn("👕 لباس", draft)
        self.assertNotIn("من ظرفیت دارم", draft)
        self.assertEqual(draft, intro_draft_for_match(self.match, self.sender))
        self.assertNotIn("tg://resolve", html)

    def test_miniapp_dm_href_is_https_tme_with_traveler_draft(self) -> None:
        _login(self.client, self.traveler)
        page = self.client.get(f"/app/matches/{self.match.pk}/")
        html = page.content.decode()
        href = next(url for url in _hrefs(html) if url.startswith("https://t.me/leila_send?text="))
        draft = _draft_from_url(href)
        self.assertContains(page, "Message on Telegram")
        self.assertIn("من ظرفیت دارم", draft)
        self.assertIn("می‌تونم ببرم", draft)
        self.assertIn("Leila", draft)
        self.assertNotIn("من یه بار دارم", draft)
        self.assertEqual(draft, intro_draft_for_match(self.match, self.traveler))

    def test_completed_match_uses_appreciate_https_link(self) -> None:
        _login(self.client, self.sender)
        finish = self.client.post(f"/app/matches/{self.match.pk}/", {"action": "complete"})
        self.assertEqual(finish.status_code, 302)
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, MatchStatus.COMPLETED)
        page = self.client.get(f"/app/matches/{self.match.pk}/")
        html = page.content.decode()
        href = next(url for url in _hrefs(html) if url.startswith("https://t.me/ali_carry?text="))
        draft = _draft_from_url(href)
        self.assertContains(page, "Appreciate by message")
        self.assertNotContains(page, "Message on Telegram")
        self.assertIn("ممنونم", draft)
        self.assertNotIn("tg://resolve", html)

    def test_missing_username_hides_dm_button(self) -> None:
        self.traveler.telegram_username = ""
        self.traveler.save(update_fields=["telegram_username"])
        _login(self.client, self.sender)
        page = self.client.get(f"/app/matches/{self.match.pk}/")
        self.assertNotContains(page, "Message on Telegram")
        self.assertNotContains(page, 'id="dm-link"')
        self.assertContains(page, "They have no public @username")
        self.assertContains(page, "من یه بار دارم")

    def test_bot_glass_button_uses_https_url_with_text(self) -> None:
        draft = intro_draft_for_match(self.match, self.sender)
        markup = connected_markup(self.traveler, draft)
        urls = [btn["url"] for row in markup["inline_keyboard"] for btn in row if "url" in btn]
        dm = next(url for url in urls if url.startswith("https://t.me/ali_carry?text="))
        self.assertEqual(_draft_from_url(dm), draft)
        self.assertTrue(any(btn.get("text") == "Message on Telegram" for row in markup["inline_keyboard"] for btn in row))
        self.assertFalse(any(url.startswith("tg://") for url in urls))

    def test_contact_https_url_keeps_draft_and_never_becomes_the_page_href_via_tg(self) -> None:
        contact = telegram_dm_contact(self.traveler, "سلام کولبر")
        self.assertTrue(contact["https_url"].startswith("https://t.me/ali_carry?text="))
        self.assertEqual(_draft_from_url(contact["https_url"]), "سلام کولبر")
        self.assertEqual(contact["telegram_url"], contact["https_url"])

    def test_nav_js_leaves_mobile_and_web_tme_links_alone(self) -> None:
        nav = (BACKEND / "miniapp/static/miniapp/nav.js").read_text()
        self.assertIn("if (!isDesktopApp()) return", nav)
        self.assertIn('platform === "tdesktop"', nav)
        self.assertIn('platform === "macos"', nav)
        self.assertIn('platform === "linux"', nav)
        self.assertIn("a[data-telegram-link]", nav)
        self.assertIn("openDesktopUserChat", nav)
        self.assertIn('"https://t.me/" + username', nav)
        self.assertIn('path_full: "/" + username', nav)
        self.assertIn("[data-desktop-copy]", nav)
        self.assertNotIn("onTelegramLinkClick", nav)
        self.assertNotIn("tg://resolve?domain=", nav)
        self.assertNotIn("prepareDesktopDmLinks", nav)
        self.assertNotIn('closest("a[href]")', nav)
        self.assertNotIn("closest('a[href]')", nav)
        self.assertNotIn("android", nav)
        self.assertNotIn("iphone", nav)
        self.assertNotIn("weba", nav)

    def test_desktop_copy_ui_stays_hidden_by_default(self) -> None:
        _login(self.client, self.sender)
        page = self.client.get(f"/app/matches/{self.match.pk}/")
        self.assertContains(page, "data-desktop-copy")
        self.assertContains(page, " hidden")
        self.assertContains(page, "Copy message")
        self.assertContains(page, 'href="https://t.me/ali_carry?text=')

    def test_web_match_page_is_a_plain_https_link(self) -> None:
        page = (REPO / "apps/web/app/app/matches/[id]/page.tsx").read_text()
        self.assertIn("href={match.counterpart.telegram_url}", page)
        self.assertNotIn("preventDefault", page)
        self.assertNotIn("openTelegramDm", page)
        self.assertNotIn("tg_url", page)
        self.assertNotIn("tg://resolve", page)

    def test_match_template_uses_https_url_not_tg_url(self) -> None:
        template = (BACKEND / "miniapp/templates/miniapp/match_detail.html").read_text()
        self.assertIn("counterpart.https_url|default:counterpart.telegram_url", template)
        self.assertNotIn("counterpart.tg_url", template)
        self.assertIn("https_url", template)


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=True)
class GoldenProductFlowTests(APITestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        seed_catalog()

    def setUp(self) -> None:
        self.sender = make_user(telegram_user_id=88101, first_name="Sara")
        self.traveler = make_user(
            telegram_user_id=88102,
            first_name="Omar",
            telegram_username="omar_travel",
        )

    def test_create_pair_connects_and_reveals_contact(self) -> None:
        _login(self.client, self.sender)
        created = self.client.post(
            "/app/demand/new/",
            {
                "origin_country": "IR",
                "origin_city": "tehran",
                "destination_country": "CA",
                "destination_city": "toronto",
                "destination_cities": ["toronto"],
                "desired_date": "2027-09-07",
                "weight_kg": "2",
                "item_category_codes": ["CLOTHES"],
                "description": "Bag",
            },
        )
        self.assertEqual(created.status_code, 302, created.content)
        create_item_request(self.traveler, SUPPLY_PAYLOAD)
        match = Match.objects.get()
        self.assertEqual(match.status, MatchStatus.CONNECTED)

        page = self.client.get(f"/app/matches/{match.pk}/")
        self.assertContains(page, "Message on Telegram")
        self.assertContains(page, 'href="https://t.me/omar_travel?text=')
        self.assertContains(page, "Telegram ID")
        self.assertContains(page, str(self.traveler.telegram_user_id))
        self.assertContains(page, "@omar_travel")

    def test_finish_then_rate_archives_the_match(self) -> None:
        create_item_request(self.sender, DEMAND_PAYLOAD)
        create_item_request(self.traveler, SUPPLY_PAYLOAD)
        match = Match.objects.get()
        _login(self.client, self.sender)
        finish = self.client.post(f"/app/matches/{match.pk}/", {"action": "complete"})
        self.assertEqual(finish.status_code, 302)
        match.refresh_from_db()
        self.assertEqual(match.status, MatchStatus.COMPLETED)
        match.demand_request.refresh_from_db()
        self.assertEqual(match.demand_request.status, RequestStatus.COMPLETED)

        rate = self.client.post(
            f"/app/matches/{match.pk}/",
            {"action": "rate", "score": "5", "comment": "On time."},
        )
        self.assertEqual(rate.status_code, 302)
        self.assertEqual(MatchRating.objects.get(match=match, rater=self.sender).score, 5)

        active = self.client.get("/app/matches/", HTTP_X_KOOLBAR_LIST="1")
        self.assertNotContains(active, f'href="/app/matches/{match.pk}/"')
        history = self.client.get("/app/matches/?archive=1", HTTP_X_KOOLBAR_LIST="1")
        self.assertContains(history, f'href="/app/matches/{match.pk}/"')
        self.assertContains(history, "Finished")

    def test_match_statuses_are_only_connected_completed_expired(self) -> None:
        self.assertEqual(
            set(MatchStatus.values),
            {MatchStatus.CONNECTED, MatchStatus.COMPLETED, MatchStatus.EXPIRED},
        )
        self.assertNotIn("PENDING", MatchStatus.values)
        self.assertNotIn("ACCEPTED", MatchStatus.values)
        self.assertNotIn("REJECTED", MatchStatus.values)

    def test_explore_hides_own_listing_and_telegram(self) -> None:
        create_item_request(self.sender, DEMAND_PAYLOAD)
        listing = create_item_request(
            self.traveler,
            {**SUPPLY_PAYLOAD, "origin_city": "mashhad"},
        )
        _login(self.client, self.sender)
        explore = self.client.get("/app/explore/", HTTP_X_KOOLBAR_LIST="1")
        self.assertContains(explore, f'href="/app/explore/{listing.pk}/"')
        self.assertNotContains(explore, "@omar_travel")
        self.assertNotContains(explore, "Message on Telegram")
        self.assertNotContains(explore, "Posted by Omar")

    def test_health_endpoint_is_ok(self) -> None:
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["database"], "ok")
