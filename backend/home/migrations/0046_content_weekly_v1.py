"""The weekly of 2026-11-02..08 was sent by hand on 2026-11-10 (drafts/weekly-v1/sent.json): record it so the tick never posts that week."""

from datetime import datetime, timezone

from django.db import migrations

V1 = {
    "chamedoon_toronto_khane": [726, 727],
    "chamedoon_montreal_khane": [758, 759],
    "chamedoon_vancouver_khane": [486, 487],
    "chamedoon_berlin_khane": [315, 316],
    "chamedoon_hamburg_khane": [184, 185],
    "chamedoon_koln_khane": [157, 158],
}


def forwards(apps, schema_editor):
    HomeChannel = apps.get_model("home", "HomeChannel")
    HomeContentPost = apps.get_model("home", "HomeContentPost")
    for channel in HomeChannel.objects.exclude(city="").exclude(chat_id__isnull=True):
        ids = V1.get(channel.username)
        if ids:
            HomeContentPost.objects.get_or_create(
                kind="weekly",
                chat_id=channel.chat_id,
                period_key="2026-W45",
                defaults={"channel": channel, "message_id": ids[0], "message_ids": ids, "sent_at": datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc),
                          "payload": {"manual": "weekly v1, sent by hand"}},
            )


class Migration(migrations.Migration):
    dependencies = [("home", "0045_content_aliases_v1")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
