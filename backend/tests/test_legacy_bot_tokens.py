from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from users.telegram import mini_app_bot_tokens


@patch("home.conf.bot_token", return_value="home-token")
class LegacyBotTokenTests(SimpleTestCase):
    @override_settings(TELEGRAM_BOT_TOKEN="new-token", TELEGRAM_LEGACY_BOT_TOKENS=["old-token"])
    def test_former_courier_bot_still_signs_mini_app(self, _home):
        self.assertEqual(mini_app_bot_tokens(), ["new-token", "home-token", "old-token"])

    @override_settings(TELEGRAM_BOT_TOKEN="new-token", TELEGRAM_LEGACY_BOT_TOKENS=["new-token", ""])
    def test_duplicates_and_blanks_dropped(self, _home):
        self.assertEqual(mini_app_bot_tokens(), ["new-token", "home-token"])

    @override_settings(TELEGRAM_BOT_TOKEN="new-token")
    def test_no_legacy_setting(self, _home):
        self.assertEqual(mini_app_bot_tokens(), ["new-token", "home-token"])
