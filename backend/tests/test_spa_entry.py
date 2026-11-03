from django.test import TestCase, override_settings

from home.models import HomeSettings
from miniapp.auth import SESSION_USER_KEY
from miniapp.shell import HOME_BOT_SESSION_KEY
from tests.test_telegram_auth import TEST_BOT_TOKEN, build_init_data
from users.models import User

HOME_BOT_TOKEN = "999:home-bot-token"


@override_settings(TELEGRAM_BOT_TOKEN=TEST_BOT_TOKEN, HOME_BOT_TOKEN=HOME_BOT_TOKEN, DEBUG=False)
class ShellEntryTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create(telegram_user_id=880100, first_name="Sara")
        row = HomeSettings.load()
        row.miniapp_v2 = False
        row.miniapp_v2_allowlist = "880100"
        row.bot_token = ""
        row.save()

    def sign_in(self, home_bot: bool = False) -> None:
        session = self.client.session
        session[SESSION_USER_KEY] = self.user.pk
        if home_bot:
            session[HOME_BOT_SESSION_KEY] = True
        session.save()

    def flag_off_for_this_person(self) -> None:
        HomeSettings.objects.update(miniapp_v2_allowlist="1")

    def assert_goes(self, url: str, target: str) -> None:
        response = self.client.get(url)
        self.assertEqual((response.status_code, response.headers.get("Location")), (302, target), url)

    def test_housing_links_go_to_the_shell(self) -> None:
        self.sign_in()
        self.assert_goes("/app/home/", "/app/v2/")
        self.assert_goes("/app/home/12/", "/app/v2/listing/12")
        self.assert_goes("/app/home/list/?country=CA&city=North%20York&type=room&min=800&max=1200", "/app/v2/?city=northyork&types=room&min=800&max=1200")
        self.assert_goes("/app/home/list/?country=CA", "/app/v2/")

    def test_telegram_start_param_stays_on_the_url(self) -> None:
        self.sign_in()
        self.assert_goes("/app/home/?tgWebAppStartParam=home_ca_toronto_src-chtor", "/app/v2/?tgWebAppStartParam=home_ca_toronto_src-chtor")
        self.assert_goes("/app/home/12/?startapp=home_12", "/app/v2/listing/12?startapp=home_12")

    def test_startapp_links_reach_the_shell_through_the_old_entry(self) -> None:
        self.sign_in()
        response = self.client.get("/app/?startapp=home_12", follow=True)
        self.assertEqual(response.redirect_chain[-1][0], "/app/v2/listing/12")

    def test_listing_path_with_home_prefix_reaches_the_shell(self) -> None:
        self.sign_in()
        response = self.client.get("/app/home/home_12/", follow=True)
        self.assertEqual(response.redirect_chain[-1][0], "/app/v2/listing/12")

    def test_consumed_housing_startapp_still_goes_to_the_shell(self) -> None:
        self.sign_in()
        self.client.get("/app/?startapp=home")
        self.assert_goes("/app/?startapp=home", "/app/home/")

    def test_flag_off_keeps_todays_pages(self) -> None:
        self.flag_off_for_this_person()
        self.sign_in(home_bot=True)
        for url in ("/app/", "/app/home/", "/app/home/list/?country=CA"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get("/app/?startapp=home_12").headers["Location"], "/app/home/12/")

    def test_main_bot_session_keeps_the_chamedoon_home_and_courier(self) -> None:
        self.sign_in()
        self.assertEqual(self.client.get("/app/").status_code, 200)
        self.assertEqual(self.client.get("/app/courier/").status_code, 200)

    def test_home_bot_session_goes_from_the_bare_entry_to_the_shell(self) -> None:
        self.sign_in(home_bot=True)
        self.assert_goes("/app/", "/app/home/")

    def test_flows_not_built_stay_on_templates(self) -> None:
        self.sign_in()
        for url in ("/app/home/new/", "/app/home/mine/", "/app/home/alerts/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_home_bot_login_marks_the_session_and_reaches_the_shell(self) -> None:
        data = build_init_data(telegram_user_id=880100, first_name="Sara", bot_token=HOME_BOT_TOKEN)
        response = self.client.post("/app/login/", {"init_data": data}, follow=True)
        self.assertTrue(self.client.session.get(HOME_BOT_SESSION_KEY))
        self.assertEqual(response.redirect_chain[-1][0], "/app/v2/")

    def test_main_bot_login_clears_a_housing_mark(self) -> None:
        self.sign_in(home_bot=True)
        self.client.post("/app/login/", {"init_data": build_init_data(telegram_user_id=880100, first_name="Sara")})
        self.assertFalse(self.client.session.get(HOME_BOT_SESSION_KEY))
        self.assertEqual(self.client.get("/app/").status_code, 200)

    def test_main_bot_login_does_not_mark_the_session(self) -> None:
        data = build_init_data(telegram_user_id=880100, first_name="Sara")
        self.client.post("/app/login/", {"init_data": data})
        self.assertFalse(self.client.session.get(HOME_BOT_SESSION_KEY))
