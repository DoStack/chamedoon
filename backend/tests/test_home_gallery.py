"""The listing page opens its photos in an in-app full screen gallery, never a new tab."""

from __future__ import annotations

import re
import tempfile

from django.test import override_settings

from home.models import HomeMedia, MediaStatus
from miniapp.home_views import gallery_photos, local_digits
from miniapp.i18n import messages_for
from tests.test_home_miniapp import MiniAppCase


@override_settings(HOME_MEDIA_BACKEND="local", HOME_MEDIA_LOCAL_BASE="https://m.test")
class GalleryTests(MiniAppCase):
    def setUp(self) -> None:
        super().setUp()
        self._media_root = tempfile.TemporaryDirectory()
        self.addCleanup(self._media_root.cleanup)
        media = override_settings(HOME_MEDIA_LOCAL_DIR=self._media_root.name)
        media.enable()
        self.addCleanup(media.disable)

    def add_photos(self, listing, count: int) -> None:
        # Stored out of order on purpose: the page follows `position`, not creation order.
        for position in reversed(range(count)):
            HomeMedia.objects.create(
                source=self.ca,
                post=listing.post,
                telegram_message_id=700 + position,
                position=position,
                status=MediaStatus.STORED,
                storage_key=f"home/ca/{position}-f.jpg",
                thumb_key=f"home/ca/{position}-t.jpg",
            )

    def page(self, listing) -> str:
        return self.client.get(f"/app/home/{listing.pk}/").content.decode()

    def test_detail_renders_the_gallery_with_every_photo_in_order(self) -> None:
        listing = self.ca_listing(1)
        self.add_photos(listing, 6)
        page = self.page(listing)
        self.assertIn('id="house-gallery-viewer"', page)
        self.assertIn('role="dialog"', page)
        self.assertIn('aria-modal="true"', page)
        self.assertIn('aria-label="عکس‌های آگهی"', page)
        slides = re.findall(r'data-src="([^"]+)"', page)
        self.assertEqual(slides, [f"https://m.test/home/ca/{n}-f.jpg" for n in range(6)])
        openers = re.findall(r'data-gallery-open="(\d+)"', page)
        self.assertEqual(openers, [str(n) for n in range(6)])
        # Counter and badge in Persian digits, the first photo shown first.
        self.assertIn(">۱/۶</span>", page)
        self.assertIn('data-counter-format="{i}/{n}"', page)
        self.assertIn(">۱ از ۶</span>", page)
        self.assertIn('alt="عکس ۲ از ۶: ', page)
        self.assertIn('aria-label="بزرگ کردن عکس ۳ از ۶"', page)
        self.assertIn('aria-label="بستن عکس‌ها"', page)
        self.assertIn('aria-label="عکس بعدی"', page)
        self.assertIn('aria-label="عکس قبلی"', page)
        self.assertIn("miniapp/gallery.js?v=", page)
        # Photos never open in a new tab.
        self.assertNotIn('target="_blank"', page)

    def test_english_page_uses_latin_digits(self) -> None:
        self.login(self.user, locale="en")
        listing = self.ca_listing(1)
        self.add_photos(listing, 3)
        page = self.page(listing)
        self.assertIn(">1/3</span>", page)
        self.assertIn(">1 of 3</span>", page)
        self.assertIn('aria-label="Next photo"', page)

    def test_single_photo_has_no_arrows_or_badge(self) -> None:
        listing = self.ca_listing(1)
        self.add_photos(listing, 1)
        page = self.page(listing)
        self.assertIn('id="house-gallery-viewer"', page)
        self.assertIn(">۱/۱</span>", page)
        self.assertNotIn("data-gallery-next", page)
        self.assertNotIn("data-gallery-badge", page)

    def test_no_overlay_without_stored_photos(self) -> None:
        listing = self.ca_listing(1)
        HomeMedia.objects.create(
            source=self.ca, post=listing.post, telegram_message_id=799, position=0, status=MediaStatus.PENDING
        )
        page = self.page(listing)
        self.assertNotIn("house-gallery-viewer", page)
        self.assertNotIn("data-gallery", page)
        self.assertNotIn("gallery.js", page)

    def test_feed_cards_still_open_the_detail_page(self) -> None:
        listing = self.ca_listing(1)
        self.add_photos(listing, 2)
        fragment = self.feed("?country=CA")
        self.assertIn(f'href="/app/home/{listing.pk}/"', fragment)
        self.assertNotIn("data-gallery-open", fragment)


class GalleryHelperTests(MiniAppCase):
    def test_local_digits(self) -> None:
        self.assertEqual(local_digits(26, "fa"), "۲۶")
        self.assertEqual(local_digits(26, "en"), "26")

    def test_gallery_photos_labels(self) -> None:
        photos = [{"full": "a", "thumb": "a"}, {"full": "b", "thumb": "b"}]
        out = gallery_photos(photos, "en", messages_for("en"), "Room")
        self.assertEqual([p["index"] for p in out], [0, 1])
        self.assertEqual(out[1]["alt"], "Photo 2 of 2: Room")
        self.assertEqual(out[0]["badge"], "1 of 2")
        self.assertEqual(out[0]["full"], "a")
        self.assertEqual(gallery_photos([], "fa", messages_for("fa"), "x"), [])
