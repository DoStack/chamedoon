from unittest import mock

from django.test import SimpleTestCase, override_settings

from spa import views

PRODUCTION = {"VERCEL": "1", "VERCEL_ENV": "production"}


@override_settings(ALLOWED_HOSTS=["testserver"])
class SpaShellTests(SimpleTestCase):
    """The SPA is the Mini App at the main URLs for everyone: courier at /app/, housing at /app/home/ (2026-11-11)."""

    def _get(self, path="/app/home/", tmp_index="<!doctype html>shell", env=PRODUCTION):
        index = mock.Mock()
        index.exists.return_value = tmp_index is not None
        index.read_text.return_value = tmp_index
        with mock.patch.dict("os.environ", env, clear=False), mock.patch.object(views, "SHELL_INDEX", index):
            return self.client.get(path)

    def test_main_urls_return_the_shell_for_everyone(self):
        for path in ("/app/", "/app/?tgWebAppStartParam=board_ca_20261110", "/app/request/12", "/app/me/requests", "/app/home/", "/app/home/listing/12?city=toronto"):
            with self.subTest(path=path):
                response = self._get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"shell", response.content)
                self.assertEqual(response["Cache-Control"], "no-cache")

    def test_shell_can_be_framed_by_telegram_web(self):
        response = self._get()
        self.assertNotIn("X-Frame-Options", response)
        self.assertIn("web.telegram.org", response["Content-Security-Policy"])

    def test_no_session_cookie(self):
        self.assertFalse(self._get().cookies)

    def test_missing_build_is_503(self):
        self.assertEqual(self._get(tmp_index=None).status_code, 503)

    def test_post_is_refused(self):
        self.assertEqual(self.client.post("/app/home/").status_code, 405)
        self.assertEqual(self.client.post("/app/").status_code, 405)


@override_settings(ALLOWED_HOSTS=["testserver"])
class MovedTests(SimpleTestCase):
    """Old addresses answer 301 to the same screen, with the query (Telegram's launch data) kept."""

    CASES = [
        ("/app/v2/", "/app/home/"),
        ("/app/v2/listing/7", "/app/home/listing/7"),
        ("/app/v2/courier/", "/app/"),
        ("/app/v2/courier/request/5", "/app/request/5"),
        ("/app/courier/", "/app/"),
        ("/app/courier/alerts/", "/app/alerts"),
        ("/app/demand/new/", "/app/post/package"),
        ("/app/supply/new/", "/app/post/trip"),
        ("/app/requests/", "/app/me/requests"),
        ("/app/requests/9/", "/app/me/requests/9"),
        ("/app/requests/9/created/", "/app/me/requests/9"),
        ("/app/matches/4/", "/app/me/matches/4"),
        ("/app/explore/", "/app/"),
        ("/app/explore/5/", "/app/request/5"),
        ("/app/explore_5/", "/app/request/5"),
        ("/app/support/", "/app/me/support"),
        ("/app/support/new/", "/app/me/support/new"),
        ("/app/support/3/", "/app/me/support/3"),
        ("/app/about/", "/app/guide"),
        ("/app/home/new/", "/app/home/post"),
        ("/app/home/mine/", "/app/home/mine"),
        ("/app/home/12/", "/app/home/listing/12"),
        ("/app/home/home_12/", "/app/home/listing/12"),
        ("/app/home/alerts/3/open/", "/app/home/alerts/3"),
    ]

    def test_old_addresses_move_permanently(self):
        for old, new in self.CASES:
            with self.subTest(old=old):
                response = self.client.get(old)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], new)

    def test_the_query_is_kept(self):
        response = self.client.get("/app/v2/courier/", {"tgWebAppStartParam": "board_de_20261110"})
        self.assertEqual(response["Location"], "/app/?tgWebAppStartParam=board_de_20261110")
