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
    ("OTHER", ("سایر", "خرده ریز", "خردهریز", "چیزای دیگه", "وسایل دیگه")),
]

_REFUSAL = (
    "معذور",
    "نمی",
    "نمي",
    "نمیکن",
    "قبول نمیکن",
    "پیغام ندید",
    "پیام ندید",
    "نپذیر",
    "نمیپذیر",
    "نمیبره",
    "نمی بره",
    "نمیبرم",
    "نمی برم",
    "نمیگیره",
    "نمی گیره",
    "تکمیل",
    "تکمیل است",
    "تکمیل هست",
    "ظرفیت تکمیل",
    "ممنوع",
    "قبول ندارم",
    "قبول نیست",
)


def _normalize(text: str) -> str:
    return (
        (text or "")
        .replace("ي", "ی")
        .replace("ك", "ک")
        .replace("‌", "")
        .replace("ـ", "")
    )


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
    haystack = _normalize(text)
    found = [code for code, keys in _CATEGORY_KEYS if any(_has_keyword(haystack, key) for key in keys)]
    return found


def _has_keyword(text: str, key: str) -> bool:
    haystack = _normalize(text)
    needle = _normalize(key)
    if len(needle) <= 2:
        return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack) is not None
    return needle in haystack


def _keyword_refused(text: str, keys: tuple[str, ...]) -> bool:
    haystack = _normalize(text)
    for key in keys:
        needle = _normalize(key)
        start = 0
        while True:
            if len(needle) <= 2:
                match = re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", haystack[start:])
                if not match:
                    break
                index = start + match.start()
            else:
                index = haystack.find(needle, start)
                if index < 0:
                    break
            window = haystack[max(0, index - 48) : index + len(needle) + 48]
            if any(_normalize(marker) in window for marker in _REFUSAL):
                return True
            start = index + max(len(needle), 1)
    return False


def categories_for_request(text: str, *, is_supply: bool) -> tuple[list[str], list[str]]:
    carried = category_codes(text)
    excluded: list[str] = []
    if is_supply:
        for code, keys in (("PET", ("پت", "حیوان")), ("CIGARETTES", ("سیگار",)), ("MEDICINE", ("دارو",))):
            if _keyword_refused(text, keys):
                excluded.append(code)
                carried = [item for item in carried if item != code]
    return carried, excluded
