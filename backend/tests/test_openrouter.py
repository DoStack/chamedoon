from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils.encoding import force_bytes

from ai.openrouter import chat, complete, openrouter_enabled, openrouter_model
from tests.helpers import TEST_SECRET


def _ok_response(payload: dict):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    return _Response()


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False, OPENROUTER_API_KEY="")
class OpenRouterDisabledTests(TestCase):
    def test_missing_key_does_not_call_network(self) -> None:
        self.assertFalse(openrouter_enabled())
        with patch("ai.openrouter.urllib.request.urlopen") as mocked:
            result = complete("hello")
        mocked.assert_not_called()
        self.assertFalse(result.ok)
        self.assertIn("not configured", result.error)


@override_settings(
    SECRET_KEY=TEST_SECRET,
    DEBUG=False,
    OPENROUTER_API_KEY="sk-or-test",
    OPENROUTER_MODEL="openrouter/free",
    OPENROUTER_MODEL_FALLBACKS="google/gemma-4-31b-it:free",
)
class OpenRouterClientTests(TestCase):
    def test_complete_reads_message_content(self) -> None:
        payload = {
            "id": "gen-1",
            "model": "google/gemma-4-31b-it:free",
            "choices": [{"message": {"role": "assistant", "content": "pong"}}],
        }
        with patch("ai.openrouter.urllib.request.urlopen", return_value=_ok_response(payload)) as mocked:
            result = complete("Reply with pong.")
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "pong")
        self.assertEqual(result.model, "google/gemma-4-31b-it:free")
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "openrouter/free")
        self.assertEqual(body["models"], ["openrouter/free", "google/gemma-4-31b-it:free"])
        self.assertEqual(body["messages"][-1]["content"], "Reply with pong.")
        self.assertTrue(request.headers["Authorization"].endswith("sk-or-test"))
        self.assertEqual(request.headers["X-title"], "Koolbar")

    def test_http_error_is_returned(self) -> None:
        error = HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            429,
            "Too Many Requests",
            hdrs={},
            fp=BytesIO(force_bytes('{"error":{"message":"Rate limit"}}')),
        )
        with patch("ai.openrouter.urllib.request.urlopen", side_effect=error):
            result = chat([{"role": "user", "content": "hi"}])
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "Rate limit")

    def test_ping_command_does_not_call_live_api(self) -> None:
        with patch("ai.openrouter.urllib.request.urlopen") as mocked:
            call_command("openrouter_ping")
        mocked.assert_not_called()
        self.assertEqual(openrouter_model(), "openrouter/free")
        self.assertTrue(openrouter_enabled())
