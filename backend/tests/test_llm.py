from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings

from ai.llm import complete, free_models, llm_status
from ai.openrouter import ChatResult
from tests.helpers import TEST_SECRET


def _fail(model: str, error: str = "Empty model response.") -> ChatResult:
    return ChatResult(ok=False, model=model, error=error)


def _ok(model: str, text: str = "pong") -> ChatResult:
    return ChatResult(ok=True, model=model, text=text)


@override_settings(
    SECRET_KEY=TEST_SECRET,
    DEBUG=False,
    OPENROUTER_API_KEY="sk-or-test",
    OPENROUTER_MODEL="openrouter/free",
    OPENROUTER_MODEL_FALLBACKS="google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it:free",
    OPENAI_API_KEY="sk-openai",
    OPENAI_MODEL="gpt-5-mini",
)
class LlmChainTests(TestCase):
    def test_free_chain_is_three_openrouter_models(self) -> None:
        self.assertEqual(
            free_models(),
            (
                "openrouter/free",
                "google/gemma-4-31b-it:free",
                "google/gemma-4-26b-a4b-it:free",
            ),
        )
        status = llm_status()
        self.assertTrue(status["openrouter"])
        self.assertTrue(status["openai"])
        self.assertEqual(status["paid_model"], "gpt-5-mini")
        self.assertIn("gpt-5-mini", status["model"])

    def test_uses_second_free_model_when_first_is_empty(self) -> None:
        responses = [
            _fail("openrouter/free"),
            _ok("google/gemma-4-31b-it:free"),
        ]
        with patch("ai.llm.openrouter_chat", side_effect=responses) as openrouter:
            with patch("ai.llm.openai_chat") as openai:
                result = complete("Reply with pong.")
        self.assertTrue(result.ok)
        self.assertEqual(result.model, "google/gemma-4-31b-it:free")
        self.assertEqual(openrouter.call_count, 2)
        openai.assert_not_called()

    def test_uses_openai_after_all_free_models_fail(self) -> None:
        with patch("ai.llm.openrouter_chat", return_value=_fail("openrouter/free")) as openrouter:
            with patch("ai.llm.openai_chat", return_value=_ok("gpt-5-mini")) as openai:
                result = complete("Reply with pong.")
        self.assertTrue(result.ok)
        self.assertEqual(result.model, "gpt-5-mini")
        self.assertEqual(openrouter.call_count, 3)
        openai.assert_called_once()
        body_messages = openai.call_args.args[0]
        self.assertEqual(body_messages[-1]["content"], "Reply with pong.")

    def test_rewrite_skips_paid_model(self) -> None:
        with patch("ai.llm.openrouter_chat", return_value=_fail("openrouter/free")) as openrouter:
            with patch("ai.llm.openai_chat") as openai:
                result = complete("Rewrite this.", allow_paid=False)
        self.assertFalse(result.ok)
        self.assertEqual(openrouter.call_count, 3)
        openai.assert_not_called()
        self.assertIn("openrouter/free", result.error)


@override_settings(SECRET_KEY=TEST_SECRET, DEBUG=False, OPENROUTER_API_KEY="", OPENAI_API_KEY="sk-openai")
class OpenAIOnlyTests(TestCase):
    def test_openai_runs_when_openrouter_is_missing(self) -> None:
        with patch("ai.llm.openrouter_chat") as openrouter:
            with patch("ai.llm.openai_chat", return_value=_ok("gpt-5-mini")) as openai:
                result = complete("Reply with pong.")
        self.assertTrue(result.ok)
        openrouter.assert_not_called()
        openai.assert_called_once()
