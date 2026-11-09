"""Rule 47: an offer for a place outside the source's country never becomes a listing or an outreach message (#314)."""

from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from home.abroad import abroad_reason, foreign_phone, foreign_places
from home.models import HomeListing, HomeSource, PostStatus
from home.review import apply_review
from tests.test_home import ACCEPT, make_post, make_source

DUBAI = (
    "📌جهت اجاره\n✅واحد یک خوابه\n✅در منطقه دبی کریک هاربور\n✅از بزرگ‌ترین سازنده دولتی اعمار \n"
    "✅متراژ 73 مترمربع\n📞جهت اطلاعات بیش‌تر با من در تماس باشید.\n+971 56 884 2000"
)
VANCOUVER = "آپارتمان یک خوابه در داونتاون ونکوور، ۲۴۰۰ دلار. تماس 604-555-0182"


class AbroadReasonTests(SimpleTestCase):
    def test_dubai_ad_in_a_canadian_group(self) -> None:
        self.assertTrue(abroad_reason(DUBAI, "CA", "CA"))

    def test_foreign_phone_alone(self) -> None:
        self.assertEqual(foreign_phone("1 bed apartment, 1800/month, +90 532 123 45 67", "CA"), "+905")
        self.assertEqual(foreign_phone("Zimmer frei, 500 warm. 0098 912 345 6789", "DE"), "+989")
        self.assertEqual(foreign_phone(DUBAI, "CA"), "+971")

    def test_foreign_phone_next_to_a_local_one_passes(self) -> None:
        self.assertEqual(foreign_phone("اتاق در نورث یورک، ۹۰۰ دلار. 416-555-0101 یا واتساپ +98 912 345 6789", "CA"), "")
        self.assertEqual(foreign_phone("Zimmer in Neukölln, 520 warm. 0176 1234567 oder +98 912 345 6789", "DE"), "")

    def test_home_phones_pass(self) -> None:
        self.assertEqual(abroad_reason(VANCOUVER, "CA", "CA") + foreign_phone(VANCOUVER, "CA"), "")
        self.assertEqual(foreign_phone("WG-Zimmer, 480 warm, +49 176 1234567", "DE", "DE"), "")
        self.assertEqual(foreign_phone("سوئیت ۱۸۰۰ دلار، قیمت 2,000 3 ماه", "CA"), "")

    def test_country_from_review(self) -> None:
        self.assertEqual(abroad_reason("hostel", "CA", "AM"), "country AM")

    def test_place_words_need_a_whole_word(self) -> None:
        self.assertEqual(foreign_places("نزدیک دبیرستان و مرکز ادبی، Oberbilk Düsseldorf"), [])
        self.assertEqual(foreign_places("آپارتمان در Esenyurt استانبول"), ["esenyurt", "استانبول"])


class AbroadReviewTests(TestCase):
    def setUp(self) -> None:
        HomeSource.objects.all().delete()
        self.source = make_source(username="ResidentIraniansOfVacouver", country="CA", default_city="Vancouver", default_currency="CAD")

    def offer(self, text: str, **data):
        post = make_post(self.source, text=text)
        values = {**ACCEPT, "country": "CA", "city": "Vancouver", "area": "", "price_currency": "CAD", "price_amount": 2400, "contact_telegram": "", **data}
        return post, apply_review(post, values, model="m")

    def test_dubai_offer_is_rejected_not_stored_as_vancouver(self) -> None:
        post, outcome = self.offer(DUBAI, area="Dubai Creek Harbour")
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.reject_reason, "abroad")
        post.refresh_from_db()
        self.assertEqual(post.status, PostStatus.REJECTED)
        self.assertFalse(HomeListing.objects.filter(post=post).exists())

    def test_model_reject_reason_abroad_is_kept(self) -> None:
        post = make_post(self.source, text=DUBAI)
        outcome = apply_review(post, {"accept": False, "reject_reason": "abroad"}, model="m")
        self.assertEqual(outcome.reject_reason, "abroad")

    def test_only_a_foreign_phone_holds_for_an_admin(self) -> None:
        post, outcome = self.offer("اتاق در گارشتدت هامبورگ، ۵۵۰ یورو گرم. +93797076371")
        self.assertTrue(outcome.accepted)
        listing = HomeListing.objects.get(post=post)
        self.assertTrue(listing.needs_review)
        self.assertIn("only phone is foreign +937", listing.review_note)

    def test_local_offer_still_accepted(self) -> None:
        _, outcome = self.offer(VANCOUVER)
        self.assertTrue(outcome.accepted)

    def test_wanted_from_abroad_is_kept(self) -> None:
        post, outcome = self.offer("از تهران میام، دنبال اتاق تو ونکوور هستم. +98 912 345 6789", kind="wanted")
        self.assertTrue(outcome.accepted)

    def test_listing_already_stored_is_caught_for_outreach(self) -> None:
        from home.abroad import listing_abroad

        post, outcome = self.offer(VANCOUVER)
        self.assertEqual(listing_abroad(outcome.listing), "")
        post.text = DUBAI
        post.save(update_fields=["text"])
        self.assertTrue(listing_abroad(HomeListing.objects.get(post=post)))
