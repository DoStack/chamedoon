import os
from unittest import mock

from django.test import SimpleTestCase, override_settings

from spa import views


@override_settings(ALLOWED_HOSTS=["testserver"])
class SpaShellTests(SimpleTestCase):
    def _get(self, env, path="/app/v2/", tmp_index="<!doctype html>shell"):
        index = mock.Mock()
        index.exists.return_value = tmp_index is not None
        index.read_text.return_value = tmp_index
        with mock.patch.dict("os.environ", env, clear=False), mock.patch.object(views, "SHELL_INDEX", index):
            return self.client.get(path)

    def _production(self, **flags):
        row = mock.Mock(**{"miniapp_v2": False, "miniapp_v2_allowlist": "", **flags})
        with mock.patch.object(views.conf, "row", return_value=row):
            return self._get({"VERCEL": "1", "VERCEL_ENV": "production"})

    def test_production_with_flag_and_allowlist_off_is_404(self):
        self.assertEqual(self._production().status_code, 404)
        self.assertEqual(self._production(miniapp_v2_allowlist="  ").status_code, 404)

    def test_production_with_allowlist_returns_shell(self):
        self.assertEqual(self._production(miniapp_v2_allowlist="770001").status_code, 200)

    def test_production_with_flag_returns_shell(self):
        self.assertEqual(self._production(miniapp_v2=True).status_code, 200)

    def test_production_is_404_when_the_flag_cannot_be_read(self):
        with mock.patch.object(views.conf, "row", side_effect=RuntimeError("database down")):
            self.assertEqual(self._get({"VERCEL": "1", "VERCEL_ENV": "production"}).status_code, 404)

    def test_unknown_vercel_env_is_404(self):
        self.assertEqual(self._get({"VERCEL": "1", "VERCEL_ENV": "other"}).status_code, 404)

    def test_vercel_without_env_name_is_404(self):
        with mock.patch.dict("os.environ", {"VERCEL": "1"}):
            os.environ.pop("VERCEL_ENV", None)
            self.assertEqual(self.client.get("/app/v2/").status_code, 404)

    def test_preview_returns_shell_for_deep_links(self):
        for path in ("/app/v2/", "/app/v2/home/12?city=toronto"):
            response = self._get({"VERCEL": "1", "VERCEL_ENV": "preview"}, path=path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"shell", response.content)
            self.assertEqual(response["Cache-Control"], "no-cache")

    def test_shell_can_be_framed_by_telegram_web(self):
        response = self._get({"VERCEL": "1", "VERCEL_ENV": "preview"})
        self.assertNotIn("X-Frame-Options", response)
        self.assertIn("web.telegram.org", response["Content-Security-Policy"])

    def test_no_session_cookie(self):
        self.assertFalse(self._get({"VERCEL": "1", "VERCEL_ENV": "preview"}).cookies)

    def test_missing_build_is_503(self):
        self.assertEqual(self._get({"VERCEL": "1", "VERCEL_ENV": "preview"}, tmp_index=None).status_code, 503)

    def test_post_is_refused(self):
        with mock.patch.dict("os.environ", {"VERCEL": "1", "VERCEL_ENV": "preview"}):
            self.assertEqual(self.client.post("/app/v2/").status_code, 405)
