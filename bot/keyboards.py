"""Persian labels of the courier bot and conversion of the backend's markup into aiogram types.

The backend (courier/flow.py) decides every screen; these labels must stay the same as there
(courier/texts.py) so a tap on the persistent keyboard is recognised.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

BTN_DEMAND = "📦 بسته دارم"
BTN_SUPPLY = "🧳 مسافرم"
BTN_ALERTS = "🔔 گوش به زنگ مسیر"
BTN_MINE = "📋 درخواست‌ها و سفرهای من"
BTN_HELP = "❓ راهنما و ممنوعه‌ها"
MENU_BUTTON = "باربری"

COMMANDS = [
    ("start", "شروع: بسته دارم یا مسافرم"),
    ("send", "بسته دارم، دنبال مسافرم"),
    ("carry", "مسافرم، جا دارم"),
    ("alerts", "گوش به زنگ مسیر"),
    ("mine", "درخواست‌ها و سفرهای من"),
    ("help", "راهنما و ممنوعه‌ها"),
    ("cancel", "لغو"),
]


def to_markup(markup: dict | None) -> InlineKeyboardMarkup | ReplyKeyboardMarkup | None:
    if not markup:
        return None
    if "inline_keyboard" in markup:
        return InlineKeyboardMarkup.model_validate(markup)
    if "keyboard" in markup:
        return ReplyKeyboardMarkup.model_validate(markup)
    return None
