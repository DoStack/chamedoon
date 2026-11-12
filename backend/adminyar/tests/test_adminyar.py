import io
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from PIL import Image

from adminyar import bot as ybot
from adminyar.models import AdminRole, AdminUser, AuditLog
from home.models import HomeListing, HomeMedia, HomePost, HomeSource, MediaStatus
from home.storage import LocalStorage

SARA = 8854699370
OWNER = 77830141
STRANGER = 555

ENV = {"ADMINYAR_ALLOWED_IDS": f"{SARA},8785728624", "ADMINYAR_OWNER_IDS": f"{OWNER},94471822", "ADMINYAR_BOT_TOKEN": "x"}


def jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buffer, "JPEG")
    return buffer.getvalue()


def msg(user_id, text=None, photo=False):
    message = {"chat": {"id": user_id, "type": "private"}, "from": {"id": user_id, "first_name": "M"}}
    if text is not None:
        message["text"] = text
    if photo:
        message["photo"] = [{"file_id": "small"}, {"file_id": "big"}]
    return {"update_id": 1, "message": message}


@mock.patch.dict("os.environ", ENV)
class AdminyarTests(TestCase):
    def setUp(self):
        self.sent = []
        self.bot = ybot.Bot("x", api=self.api, fetch=lambda file_id: jpeg())
        self.source = HomeSource.objects.create(username="tehran_homes", country="DE")
        self.now = timezone.now()

    def api(self, method, payload):
        self.sent.append((method, payload))
        return {}

    def texts(self):
        return [payload["text"] for method, payload in self.sent if method == "sendMessage"]

    def listing(self, n, contact="", author="", city="Berlin"):
        post = HomePost.objects.create(source=self.source, telegram_message_id=n, posted_at=self.now, text="t", author_username=author)
        return HomeListing.objects.create(
            post=post, kind="offer", country="DE", city=city, price_amount=900, price_currency="EUR",
            contact_telegram=contact, summary_fa=f"آگهی {n}", first_seen=self.now, last_seen=self.now,
        )

    # whitelist
    def test_stranger_gets_no_reply_and_no_rows(self):
        self.bot.handle(msg(STRANGER, "/start"))
        self.assertEqual(self.sent, [])
        self.assertFalse(AdminUser.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_team_account_is_greeted_by_name_with_menu(self):
        self.bot.handle(msg(SARA, "/start"))
        method, payload = self.sent[-1]
        self.assertIn("سارا", payload["text"])
        labels = [b["text"] for row in payload["reply_markup"]["keyboard"] for b in row]
        self.assertEqual(len(labels), 8)
        admin = AdminUser.objects.get(telegram_user_id=SARA)
        self.assertEqual((admin.team_account, admin.role), ("sara", AdminRole.ADMIN))
        self.assertTrue(AuditLog.objects.filter(action="start", admin_user=admin).exists())

    def test_owner_is_allowed_and_inactive_user_is_silenced(self):
        self.bot.handle(msg(OWNER, "/start"))
        self.assertEqual(AdminUser.objects.get(telegram_user_id=OWNER).role, AdminRole.OWNER)
        AdminUser.objects.filter(telegram_user_id=OWNER).update(active=False)
        self.sent.clear()
        self.bot.handle(msg(OWNER, "/start"))
        self.assertEqual(self.sent, [])

    def test_stub_button_says_soon_and_logs(self):
        self.bot.handle(msg(SARA, ybot.GOT_REPLY))
        self.assertEqual(self.texts()[-1], ybot.T_SOON)
        self.assertTrue(AuditLog.objects.filter(action="got_reply", result="stub").exists())

    # listings
    def test_listings_by_contact_or_author(self):
        self.listing(1, contact="@Owner1")
        self.listing(2, author="owner1", city="Hamburg")
        self.listing(3, contact="someone_else")
        self.bot.handle(msg(SARA, ybot.LISTINGS))
        self.bot.handle(msg(SARA, "@owner1"))
        text = self.texts()[-1]
        self.assertIn("آگهی 1", text)
        self.assertIn("Hamburg", text)
        self.assertIn("900 EUR", text)
        self.assertIn("startapp=home_", text)
        self.assertNotIn("آگهی 3", text)
        self.assertEqual(AuditLog.objects.get(action="listings").result, "2 found")

    def test_listings_none_and_bad_username(self):
        self.bot.handle(msg(SARA, ybot.LISTINGS))
        self.bot.handle(msg(SARA, "not a username!"))
        self.assertEqual(self.texts()[-1], ybot.T_BAD_USERNAME)
        self.bot.handle(msg(SARA, "@nobody_here"))
        self.assertIn("پیدا نشد", self.texts()[-1])

    # photos
    def _storage(self):
        return LocalStorage(self.tmp)

    def setup_storage(self):
        import tempfile

        self.tmp = tempfile.mkdtemp()
        patcher = mock.patch("adminyar.listings.get_storage", side_effect=self._storage)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_listing_skips_picker_and_photo_attaches(self):
        self.setup_storage()
        item = self.listing(1, contact="owner1")
        self.bot.handle(msg(SARA, ybot.PHOTOS))
        self.bot.handle(msg(SARA, "owner1"))
        self.assertIn("آگهی 1", self.texts()[-1])
        self.bot.handle(msg(SARA, photo=True))
        media = HomeMedia.objects.get(post=item.post)
        self.assertEqual(media.status, MediaStatus.STORED)
        item.post.refresh_from_db()
        self.assertEqual(item.post.photo_count, 1)
        self.assertEqual(AuditLog.objects.get(action="photo_attach").result, "ok")
        self.bot.handle(msg(SARA, photo=True))
        self.assertEqual(HomeMedia.objects.filter(post=item.post, status=MediaStatus.STORED).count(), 2)

    def test_many_listings_show_picker_then_callback_targets_one(self):
        self.setup_storage()
        self.listing(1, contact="owner1")
        second = self.listing(2, contact="owner1")
        self.bot.handle(msg(SARA, ybot.PHOTOS))
        self.bot.handle(msg(SARA, "owner1"))
        markup = self.sent[-1][1]["reply_markup"]
        self.assertEqual(len(markup["inline_keyboard"]), 2)
        callback = {"id": "c", "data": f"ph:{second.pk}", "from": {"id": SARA}, "message": {"chat": {"id": SARA}}}
        self.bot.handle({"update_id": 2, "callback_query": callback})
        self.bot.handle(msg(SARA, photo=True))
        self.assertEqual(HomeMedia.objects.get().post_id, second.post_id)

    def test_none_found_asks_for_post_link(self):
        self.setup_storage()
        item = self.listing(42)
        self.bot.handle(msg(SARA, ybot.PHOTOS))
        self.bot.handle(msg(SARA, "ghost_user"))
        self.assertIn("لینک پست", self.texts()[-1])
        self.bot.handle(msg(SARA, "https://t.me/tehran_homes/42"))
        self.bot.handle(msg(SARA, photo=True))
        self.assertEqual(HomeMedia.objects.get().post_id, item.post_id)

    def test_photo_without_target_is_refused(self):
        self.bot.handle(msg(SARA, photo=True))
        self.assertEqual(self.texts()[-1], ybot.T_PHOTO_FIRST)
        self.assertFalse(HomeMedia.objects.exists())
