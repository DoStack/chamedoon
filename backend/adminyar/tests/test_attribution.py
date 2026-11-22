from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from adminyar import attribution, report
from adminyar import bot as ybot
from adminyar.models import AdminInvite, AdminTouch, AdminUser, AuditLog, TouchVia
from home.models import HomeChannel, HomeListing, HomePost, HomeSource
from outreach.models import OutreachAccount, OutreachMessage, OutreachStatus, SentBy

MAHTAB = 83952027
PERSON = 1001
OTHER = 1002


class AttributionTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.mahtab = AdminUser.objects.create(telegram_user_id=MAHTAB, name="مهتاب", team_account="mahtab")
        self.account = OutreachAccount.objects.create(name="mahtab", enabled=False)
        self.channel, _ = HomeChannel.objects.update_or_create(country="CA", city="Toronto", defaults={"username": "chamedoon_toronto", "chat_id": -100123})
        self.source = HomeSource.objects.create(username="toronto_homes", country="CA")

    # codes and links
    def test_code_comes_from_team_account_and_is_kept(self):
        self.assertEqual(attribution.code_for(self.mahtab), "mahtab")
        self.mahtab.refresh_from_db()
        self.assertEqual(self.mahtab.code, "mahtab")
        self.assertTrue(attribution.bot_link(self.mahtab).endswith("?start=a_mahtab"))

    def test_code_for_a_persian_name_falls_back_to_digits(self):
        owner = AdminUser.objects.create(telegram_user_id=77830141, name="محمد")
        self.assertEqual(attribution.code_for(owner), "u830141")

    def test_listing_link_carries_the_admin_src(self):
        post = HomePost.objects.create(source=self.source, telegram_message_id=1, posted_at=self.now, text="t")
        listing = HomeListing.objects.create(post=post, kind="offer", country="CA", city="Toronto", first_seen=self.now, last_seen=self.now)
        link = attribution.listing_link(self.mahtab, listing)
        self.assertIn(f"startapp=home_ca_toronto_l{listing.pk}_src-a-mahtab", link)

    def test_invite_link_made_once_named_for_the_admin(self):
        calls = []

        def api(method, payload):
            calls.append((method, payload))
            return {"invite_link": "https://t.me/+abc"}

        self.assertEqual(attribution.invite_link(self.mahtab, self.channel, api=api), "https://t.me/+abc")
        self.assertEqual(attribution.invite_link(self.mahtab, self.channel, api=api), "https://t.me/+abc")
        self.assertEqual(calls, [("createChatInviteLink", {"chat_id": -100123, "name": "a:mahtab"})])

    def test_invite_link_falls_back_to_public_link_without_rights(self):
        def api(method, payload):
            raise RuntimeError("not enough rights")

        self.assertEqual(attribution.invite_link(self.mahtab, self.channel, api=api), "https://t.me/chamedoon_toronto")
        self.assertFalse(AdminInvite.objects.exists())

    # capture
    def test_bot_start_with_admin_code_is_a_touch_once(self):
        attribution.code_for(self.mahtab)
        self.assertTrue(attribution.note_start(PERSON, "a_mahtab"))
        self.assertFalse(attribution.note_start(PERSON, "a_mahtab"))
        touch = AdminTouch.objects.get()
        self.assertEqual((touch.via, touch.first, touch.new_user), (TouchVia.BOT, True, True))

    def test_other_payloads_and_unknown_codes_are_ignored(self):
        attribution.code_for(self.mahtab)
        self.assertFalse(attribution.note_start(PERSON, "alert_ca_toronto"))
        self.assertFalse(attribution.note_start(PERSON, "a_nobody"))
        self.assertFalse(attribution.note_start(MAHTAB, "a_mahtab"))  # the admin's own test tap
        self.assertFalse(AdminTouch.objects.exists())

    def test_second_admin_is_not_first(self):
        attribution.code_for(self.mahtab)
        other = AdminUser.objects.create(telegram_user_id=6020401674, name="فاطمه", team_account="fateme")
        attribution.code_for(other)
        attribution.note_start(PERSON, "a_mahtab")
        attribution.note_app(PERSON, "a-fateme")
        self.assertEqual(list(AdminTouch.objects.order_by("id").values_list("first", flat=True)), [True, False])

    def test_join_through_admin_invite(self):
        AdminInvite.objects.create(admin_user=self.mahtab, channel=self.channel, link="https://t.me/+abc", name="a:mahtab")
        update = {
            "chat": {"id": -100123, "type": "channel"},
            "old_chat_member": {"status": "left", "user": {"id": PERSON}},
            "new_chat_member": {"status": "member", "user": {"id": PERSON, "is_bot": False}},
            "invite_link": {"invite_link": "https://t.me/+abc", "name": "a:mahtab"},
        }
        self.assertTrue(attribution.note_join(update))
        self.assertEqual(AdminTouch.objects.get().channel, self.channel)
        leave = {**update, "old_chat_member": {"status": "member"}, "new_chat_member": {"status": "left", "user": {"id": PERSON}}}
        self.assertFalse(attribution.note_join(leave))
        plain = {**update, "invite_link": {"invite_link": "https://t.me/+xyz", "name": "ads"}}
        self.assertFalse(attribution.note_join(plain))

    def test_housing_webhook_hooks(self):
        from home import botchat

        attribution.code_for(self.mahtab)
        AdminInvite.objects.create(admin_user=self.mahtab, channel=self.channel, link="https://t.me/+abc", name="a:mahtab")
        botchat.handle_update({"update_id": 5, "chat_member": {
            "chat": {"id": -100123}, "old_chat_member": {"status": "left"},
            "new_chat_member": {"status": "member", "user": {"id": OTHER}}, "invite_link": {"invite_link": "https://t.me/+abc", "name": "a:mahtab"},
        }})
        self.assertTrue(AdminTouch.objects.filter(telegram_user_id=OTHER, via=TouchVia.JOIN).exists())
        with mock.patch("outreach.housing.mark_bot_start"), mock.patch("home.botchat.conf.bot_v2_for", return_value=False), mock.patch("home.botchat.handle_message"):
            botchat.handle_update({"update_id": 6, "message": {"chat": {"id": PERSON, "type": "private"}, "from": {"id": PERSON}, "text": "/start a_mahtab"}})
        self.assertTrue(AdminTouch.objects.filter(telegram_user_id=PERSON, via=TouchVia.BOT).exists())

    # metrics
    def message(self, tid, **extra):
        return OutreachMessage.objects.create(
            recipient_username=f"u{tid}", recipient_telegram_id=tid, text="سلام", token=f"t{tid}", account=self.account,
            sent_by=SentBy.HUMAN, status=OutreachStatus.SENT, sent_at=self.now - timedelta(minutes=30), **extra
        )

    def test_stats_count_messages_results_listings_and_forwards(self):
        attribution.code_for(self.mahtab)
        self.message(PERSON, replied_at=self.now, bot_started_at=self.now - timedelta(minutes=5))
        self.message(OTHER)
        attribution.note_start(3003, "a_mahtab")
        AdminTouch.objects.create(telegram_user_id=4004, admin_user=self.mahtab, via=TouchVia.JOIN, channel=self.channel)
        post = HomePost.objects.create(source=self.source, telegram_message_id=9, posted_at=self.now - timedelta(minutes=1), text="t", author_id=PERSON)
        HomeListing.objects.create(post=post, kind="offer", country="CA", city="Toronto", first_seen=self.now, last_seen=self.now)
        old = HomePost.objects.create(source=self.source, telegram_message_id=10, posted_at=self.now - timedelta(hours=2), text="t", author_id=OTHER)
        HomeListing.objects.create(post=old, kind="offer", country="CA", city="Toronto", first_seen=self.now, last_seen=self.now)
        AuditLog.objects.create(telegram_user_id=MAHTAB, admin_user=self.mahtab, action="ready_forward", result="forwarded")

        s = attribution.stats(self.mahtab, self.now - timedelta(days=1), self.now + timedelta(seconds=1))
        self.assertEqual((s.messaged, s.replied, s.starts, s.new_users, s.joins, s.listings, s.forwards), (2, 1, 2, 1, 1, 1, 1))
        self.assertEqual(s.result, 3)

        card = report.owners_card("d", now=self.now + timedelta(seconds=1))
        self.assertIn("مهتاب", card)
        self.assertIn("پیام ۲", card)
        self.assertIn("a_mahtab", report.admin_card(self.mahtab, "d", now=self.now + timedelta(seconds=1)))


class BotLinkTests(TestCase):
    def test_listings_answer_uses_the_admin_tagged_link(self):
        now = timezone.now()
        source = HomeSource.objects.create(username="toronto_homes", country="CA")
        post = HomePost.objects.create(source=source, telegram_message_id=1, posted_at=now, text="t", author_username="owner1")
        HomeListing.objects.create(post=post, kind="offer", country="CA", city="Toronto", first_seen=now, last_seen=now)
        sent = []
        env = {"ADMINYAR_ALLOWED_IDS": str(MAHTAB), "ADMINYAR_OWNER_IDS": "77830141", "ADMINYAR_BOT_TOKEN": "x"}
        AdminUser.objects.create(telegram_user_id=MAHTAB, name="مهتاب", team_account="mahtab")
        with mock.patch.dict("os.environ", env):
            b = ybot.Bot("x", api=lambda m, p: sent.append(p) or {})
            chat = {"chat": {"id": MAHTAB, "type": "private"}, "from": {"id": MAHTAB}}
            b.handle({"message": {**chat, "text": ybot.LISTINGS}})
            b.handle({"message": {**chat, "text": "@owner1"}})
        self.assertIn("src-a-mahtab", sent[-1]["text"])
