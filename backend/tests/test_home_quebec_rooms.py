from decimal import Decimal

from django.test import SimpleTestCase

from home.review import quebec_rooms, quebec_size

# Post 113722 (listing 7234, #355): a Montreal lease transfer, stored as rooms 2 and «آپارتمان 2.5 خوابه».
P113722 = (
    "#انتقال_اجاره #LeaseTransfer #Montreal\n\n🏠 انتقال اجاره آپارتمان ۲½ روشن و تمیز در مرکز مونترال\n\n"
    "📍 آدرس: 1950 Avenue Lincoln – Château Lincoln\n\nیک واحد ۲½ روشن، تمیز و خوش‌نقشه در موقعیت بسیار خوب، مناسب برای یک نفر یا زوج.\n"
    "💰 اجاره ماهانه: ۱٬۳۴۵ دلار"
)


class QuebecRoomsTests(SimpleTestCase):
    """Audit 2026-11-11 13:17, rule 67: a Quebec «N½» is N - 2 bedrooms, never N."""

    def fields(self, rooms, summary="", country="CA"):
        return {"country": country, "rooms": rooms, "summary_fa": summary}

    def test_post_113722_has_no_bedroom_count(self) -> None:
        fields = self.fields(Decimal("2"), "آپارتمان 2.5 خوابه در مرکز مونترال با اجاره 1,345 دلار.")
        quebec_rooms(fields, P113722)
        self.assertIsNone(fields["rooms"])
        self.assertEqual(fields["summary_fa"], "آپارتمان 2½ در مرکز مونترال با اجاره 1,345 دلار.")
        self.assertNotIn("خوابه", fields["summary_fa"])

    def test_sizes_map_to_bedrooms(self) -> None:
        for text, beds in (("4½ à louer", Decimal("2")), ("آپارتمان ۳ و نیم", Decimal("1")), ("5 1/2 Rosemont", Decimal("3")), ("1½ studio", None)):
            fields = self.fields(Decimal("9"))
            quebec_rooms(fields, text)
            self.assertEqual(fields["rooms"], beds, text)

    def test_a_written_bedroom_count_or_another_country_stands(self) -> None:
        fields = self.fields(Decimal("2"))
        quebec_rooms(fields, "4½ with 2 bedrooms")
        self.assertEqual(fields["rooms"], Decimal("2"))
        fields = self.fields(Decimal("2"), country="DE")
        quebec_rooms(fields, "2½ Zimmer")
        self.assertEqual(fields["rooms"], Decimal("2"))

    def test_other_halves_are_not_sizes(self) -> None:
        self.assertIsNone(quebec_size("۲ و نیم ماه اجاره"))
        self.assertIsNone(quebec_size("فقط ۳ و نیم دقیقه تا مترو"))
        self.assertIsNone(quebec_size("1,345 دلار"))
