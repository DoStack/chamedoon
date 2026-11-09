"""Phone numbers a poster types or shares, in E.164 form (+ and 8 to 15 digits).

The same rules run in the Mini App (frontend/src/lib/phone.ts), so the number shown
before publishing is the one stored.
"""

from __future__ import annotations

import re

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ALLOWED = re.compile(r"^\+?[\d\s\-().]+$")


def to_e164(raw: object, country: str = "") -> str:
    """`+<digits>`, or "" when the text is not a phone number.

    00 is the international prefix (00989356706868 -> +989356706868). Without a + or 00,
    a number reads in the listing's country: ten digits in Canada or the US get +1, a
    leading 0 in Germany becomes +49. An Iranian mobile written as 09xx... gets +98.
    """
    text = str(raw or "").translate(_DIGITS).strip()
    if not text or not _ALLOWED.match(text):
        return ""
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif country in {"CA", "US"} and len(digits) == 10 and digits[0] not in "01":
        digits = "1" + digits
    elif len(digits) == 11 and digits.startswith("09"):
        digits = "98" + digits[1:]
    elif country == "DE" and digits.startswith("0"):
        digits = "49" + digits[1:]
    if not 8 <= len(digits) <= 15 or digits.startswith("0"):
        return ""
    return "+" + digits
