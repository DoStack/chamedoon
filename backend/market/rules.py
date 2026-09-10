from __future__ import annotations

import re
from decimal import Decimal

from item_requests.weights import suggested_kg
from market.classify import extract_weight_kg

DEFAULT_KG = Decimal("10.00")
MAX_KG = Decimal("50.00")
MIN_KG = Decimal("0.01")

_CATEGORY_KEYS = [
    ("DOCUMENTS", ("مدرک", "مدارک", "پاسپورت", "شناسنامه", "گواهینامه", "سیمکارت")),
    ("CLOTHES", ("لباس", "کفش", "کتونی", "پوشاک")),
    ("ELECTRONICS", ("موبایل", "گوشی", "لپ تاپ", "لپتاپ", "ایفون", "اپل واچ")),
    ("MEDICINE", ("دارو", "قرص", "ویتامین")),
    ("CIGARETTES", ("سیگار",)),
    ("PET", ("پت", "حیوان خانگی")),
    ("FOOD", ("مواد غذایی", "غذا")),
    ("FRAGILE", ("شکستنی",)),
]

_REFUSAL = ("معذور", "نمی", "نمیکن", "قبول نمیکن", "پیغام ندید", "پیام ندید", "نپذیر", "نمیپذیر")


def cleaned_kg(text: str, category_codes: list[str] | None = None) -> Decimal:
    raw = extract_weight_kg(text)
    if raw is None:
        return suggested_kg(category_codes) or DEFAULT_KG
    amount = Decimal(str(raw))
    if amount < MIN_KG:
        return suggested_kg(category_codes) or DEFAULT_KG
    if amount > MAX_KG:
        return MAX_KG
    return amount.quantize(Decimal("0.01"))


def category_codes(text: str) -> list[str]:
    found = [code for code, keys in _CATEGORY_KEYS if any(_has_keyword(text, key) for key in keys)]
    return found or ["DOCUMENTS"]


def _has_keyword(text: str, key: str) -> bool:
    if len(key) <= 2:
        return re.search(rf"(?<![\w]){re.escape(key)}(?![\w])", text, re.UNICODE) is not None
    return key in text


def _keyword_refused(text: str, keys: tuple[str, ...]) -> bool:
    for key in keys:
        start = 0
        while True:
            index = text.find(key, start)
            if index < 0:
                break
            window = text[max(0, index - 48) : index + 48]
            if any(marker in window for marker in _REFUSAL):
                return True
            start = index + len(key)
    return False


def categories_for_request(text: str, *, is_supply: bool) -> tuple[list[str], list[str]]:
    carried = category_codes(text)
    excluded: list[str] = []
    if is_supply:
        for code, keys in (("PET", ("پت", "حیوان")), ("CIGARETTES", ("سیگار",)), ("MEDICINE", ("دارو",))):
            if _keyword_refused(text, keys):
                excluded.append(code)
                carried = [item for item in carried if item != code]
        if not carried:
            carried = ["DOCUMENTS"]
    return carried, excluded
