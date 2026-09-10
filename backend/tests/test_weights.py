from decimal import Decimal

from django.test import SimpleTestCase

from item_requests.weights import format_kg, suggested_kg
from market.rules import categories_for_request, cleaned_kg


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


class CategoryRefusalTests(SimpleTestCase):
    def test_full_cigarettes_and_no_pets_are_excluded(self) -> None:
        text = (
            "ونکوور مونترال اتاوا\n"
            "۲۴ سپتامبر\n"
            "مدارک\n"
            "لپ تاپ\n"
            "سیگار تکمیل هست\n"
            "پت هم نمیبره\n"
            "@mrsh1981"
        )
        carried, excluded = categories_for_request(text, is_supply=True)
        self.assertCountEqual(carried, ["DOCUMENTS", "ELECTRONICS"])
        self.assertCountEqual(excluded, ["CIGARETTES", "PET"])
        self.assertNotIn("CIGARETTES", carried)
        self.assertNotIn("PET", carried)

    def test_documents_and_other_small_items(self) -> None:
        text = "قبول مدارک و سیمکارت و سایر کوچک و کم وزن"
        carried, excluded = categories_for_request(text, is_supply=True)
        self.assertCountEqual(carried, ["DOCUMENTS", "OTHER"])
        self.assertEqual(excluded, [])

    def test_september_does_not_count_as_pet(self) -> None:
        text = "پرواز تهران به هانوفر بیستم سپتامبر مدارک"
        carried, excluded = categories_for_request(text, is_supply=True)
        self.assertIn("DOCUMENTS", carried)
        self.assertNotIn("PET", carried)
        self.assertNotIn("PET", excluded)
