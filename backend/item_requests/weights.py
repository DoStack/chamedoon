from __future__ import annotations

from decimal import Decimal

MAX_KG = Decimal("50.00")
MIN_KG = Decimal("0.01")

# Typical weight / capacity suggestion per category (kg). Multi-select sums these.
CATEGORY_KG: dict[str, Decimal] = {
    "DOCUMENTS": Decimal("0.10"),
    "CLOTHES": Decimal("1.00"),
    "PERSONAL_ITEMS": Decimal("1.00"),
    "ELECTRONICS": Decimal("0.50"),
    "FOOD": Decimal("1.00"),
    "MEDICINE": Decimal("0.20"),
    "CIGARETTES": Decimal("0.50"),
    "FRAGILE": Decimal("1.00"),
    "PET": Decimal("5.00"),
    "OTHER": Decimal("1.00"),
}


def suggested_kg(category_codes: list[str] | None) -> Decimal | None:
    codes = [code for code in (category_codes or []) if code in CATEGORY_KG]
    if not codes:
        return None
    total = sum((CATEGORY_KG[code] for code in codes), Decimal("0"))
    if total < MIN_KG:
        return MIN_KG
    if total > MAX_KG:
        return MAX_KG
    return total.quantize(Decimal("0.01"))


def format_kg(amount: Decimal | None) -> str:
    if amount is None:
        return ""
    text = f"{amount:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def category_kg_map() -> dict[str, float]:
    return {code: float(amount) for code, amount in CATEGORY_KG.items()}
