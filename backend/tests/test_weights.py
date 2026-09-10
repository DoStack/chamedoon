from decimal import Decimal

from django.test import SimpleTestCase

from item_requests.weights import format_kg, suggested_kg
from market.rules import cleaned_kg


class CategoryWeightTests(SimpleTestCase):
    def test_documents_suggest_point_one(self) -> None:
        self.assertEqual(suggested_kg(["DOCUMENTS"]), Decimal("0.10"))
        self.assertEqual(format_kg(suggested_kg(["DOCUMENTS"])), "0.1")

    def test_clothes_suggest_one(self) -> None:
        self.assertEqual(suggested_kg(["CLOTHES"]), Decimal("1.00"))

    def test_multiple_categories_sum(self) -> None:
        self.assertEqual(suggested_kg(["DOCUMENTS", "CLOTHES"]), Decimal("1.10"))
        self.assertEqual(format_kg(suggested_kg(["DOCUMENTS", "CLOTHES"])), "1.1")

    def test_cleaned_kg_uses_category_map_when_text_has_no_weight(self) -> None:
        self.assertEqual(cleaned_kg("مدارک به تورنتو", ["DOCUMENTS"]), Decimal("0.10"))
        self.assertEqual(cleaned_kg("لباس و مدارک", ["CLOTHES", "DOCUMENTS"]), Decimal("1.10"))

    def test_cleaned_kg_keeps_explicit_weight(self) -> None:
        self.assertEqual(cleaned_kg("5 کیلو لباس", ["CLOTHES"]), Decimal("5.00"))
