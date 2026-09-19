from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from config import mini_app_https_url, telegram_app_link

BTN_SEND = "📦 Send Package"
BTN_CARRY = "✈️ Can Carry"
BTN_BROWSE = "🔍 Browse requests"
BTN_REQUESTS = "📋 My Requests"
BTN_MATCHES = "🤝 My Matches"


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    send = KeyboardButton(text=BTN_SEND)
    carry = KeyboardButton(text=BTN_CARRY)
    browse = KeyboardButton(text=BTN_BROWSE)
    requests = KeyboardButton(text=BTN_REQUESTS)
    matches = KeyboardButton(text=BTN_MATCHES)

    send_https = mini_app_https_url("demand")
    carry_https = mini_app_https_url("supply")
    browse_https = mini_app_https_url("explore")
    requests_https = mini_app_https_url("requests")
    matches_https = mini_app_https_url("matches")
    if send_https and carry_https and browse_https and requests_https and matches_https:
        send = KeyboardButton(text=BTN_SEND, web_app=WebAppInfo(url=send_https))
        carry = KeyboardButton(text=BTN_CARRY, web_app=WebAppInfo(url=carry_https))
        browse = KeyboardButton(text=BTN_BROWSE, web_app=WebAppInfo(url=browse_https))
        requests = KeyboardButton(text=BTN_REQUESTS, web_app=WebAppInfo(url=requests_https))
        matches = KeyboardButton(text=BTN_MATCHES, web_app=WebAppInfo(url=matches_https))

    return ReplyKeyboardMarkup(
        keyboard=[
            [send, carry],
            [browse],
            [requests, matches],
        ],
        resize_keyboard=True,
    )


def open_mini_app_inline(startapp: str, label: str = "Open Chamedoon") -> InlineKeyboardMarkup:
    https_url = mini_app_https_url(startapp)
    if https_url:
        button = InlineKeyboardButton(text=label, web_app=WebAppInfo(url=https_url))
    else:
        button = InlineKeyboardButton(text=label, url=telegram_app_link(startapp))
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Cancel")]],
        resize_keyboard=True,
    )


def choices_keyboard(labels: list[str], *, columns: int = 2) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    row: list[KeyboardButton] = []
    for label in labels:
        row.append(KeyboardButton(text=label))
        if len(row) >= columns:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton(text="Cancel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def confirm_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Submit")], [KeyboardButton(text="Cancel")]],
        resize_keyboard=True,
    )
