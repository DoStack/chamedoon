"""Audit 2026-11-17 14:16 UTC: #492 «خونه اي واسه ملد» is a registration ask, not a search; listing 8109's «۱ تا ۱۵
دسامبر (۲ هفته) / ۳۰۰ یورو» was stored as 300 a month."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from home.models import HomeListing, HomePost
from home.review import short_stay_price
from outreach import housing, kindcheck
from outreach.models import OutreachKind, OutreachMessage
from tests.test_outreach_agent import AgentTestCase

P132685 = "سلام دوستان وقت بخير \n\n\n\n خونه اي واسه  ملد ميخواستيم در برلين \nاگه كسي جايي و داشت لطفا پيام بده"
P132754 = (
    "اجاره اتاق (کوتاه‌مدت / Untermiete)\nاتاق تک‌نفره مبله در خوابگاه Studentendorf Schlachtensee (برلین)\n\n"
    "📅 تاریخ: ۱ تا ۱۵ دسامبر (۲ هفته)\n💶 هزینه: ۳۰۰ یورو (شامل تمام هزینه‌ها)\n📍 دسترسی: کنار ایستگاه اتوبوس و سوپرمارکت ALDI (منطقه Zehlendorf)\n"
    "ℹ️ شرایط: بدون امکان Anmeldung، آشپزخانه و سرویس مشترک"
)


class Audit20261117RegistrationKindCheckTests(AgentTestCase):
    def wanted(self, text: str, **fields) -> OutreachMessage:
        HomePost.objects.filter(pk=self.ad.post_id).update(text=text)
        values = {"kind": "wanted", "property_type": "other", "price_amount": None, "price_max": None, "area": "", "rooms": None, "size_m2": None, "available_from": None, "flags": {}}
        HomeListing.objects.filter(pk=self.ad.pk).update(**{**values, **fields})
        return OutreachMessage(kind=OutreachKind.S1B, listing=HomeListing.objects.select_related("post").get(pk=self.ad.pk), text="x")

    def test_492_a_home_for_registration_is_not_housing(self) -> None:
        self.assertTrue(kindcheck.registration_purpose(P132685))
        message = self.wanted(P132685)
        self.assertTrue(kindcheck.not_housing(message))

    def test_a_room_with_registration_and_a_budget_stays_housing(self) -> None:
        text = "دنبال اتاق با امکان ملد تا ۶۰۰ یورو در برلین"
        self.assertFalse(kindcheck.registration_purpose(text))
        self.assertFalse(kindcheck.not_housing(self.wanted(text, price_max=600)))

    def test_a_purpose_phrase_with_search_fields_stays_housing(self) -> None:
        text = "یه اتاق برای ملد کردن و زندگی میخوام، تا ۵۰۰ یورو، نویکلن"
        self.assertFalse(kindcheck.not_housing(self.wanted(text, price_max=500, area="Neukölln")))

    def test_the_purpose_forms(self) -> None:
        for text in ("جایی برای ملد میخوام", "آدرس جهت Anmeldung لازم دارم", "کسی خونه واسه ملده داره؟", "برای ملد کردن جا میخوام"):
            self.assertTrue(kindcheck.registration_purpose(text), text)
        for text in ("خونه با امکان ملد", "اتاق مبله با ملد", "دنبال آپارتمان هستم، برای ملد هم مهمه که امکانش باشه"):
            self.assertFalse(kindcheck.registration_purpose(text), text)


class Audit20261117ShortStayPriceTests(AgentTestCase):
    def fields(self, **extra) -> dict:
        return {"kind": "offer", "price_amount": Decimal("300"), "price_period": "month", "available_from": date(2026, 12, 1), "available_to": date(2026, 12, 15), "flags": {}, **extra}

    def test_8109_two_weeks_for_300_is_never_300_a_month(self) -> None:
        fields = self.fields()
        short_stay_price(fields, P132754)
        self.assertEqual(fields["price_period"], "week")
        self.assertEqual(fields["flags"], {"stay_total": 300, "stay_days": 15})
        self.assertLess(fields["price_amount"], 300)
        HomeListing.objects.filter(pk=self.ad.pk).update(
            price_amount=fields["price_amount"], price_period="week", price_currency="EUR", flags=fields["flags"]
        )
        self.ad.refresh_from_db()
        self.assertEqual(housing.listing_price(self.ad), "۳۰۰ یورو برای دو هفته")

    def test_a_monthly_price_for_a_short_sublet_stays_monthly(self) -> None:
        fields = self.fields()
        short_stay_price(fields, "اتاق از ۱ تا ۱۵ دسامبر، اجاره ماهانه ۳۰۰ یورو")
        self.assertEqual((fields["price_period"], fields["price_amount"]), ("month", Decimal("300")))

    def test_a_stay_of_a_month_or_more_is_left_alone(self) -> None:
        fields = self.fields(available_to=date(2027, 1, 31))
        short_stay_price(fields, P132754)
        self.assertEqual((fields["price_period"], fields["price_amount"]), ("month", Decimal("300")))
