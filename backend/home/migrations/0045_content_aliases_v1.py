"""Spec §4: suburbs count for their metro; and the hand-sent daily v1 of 2026-11-10 is recorded, so the tick never sends that day again."""

from datetime import datetime, timezone

from django.db import migrations

SUBURBS = {
    "Toronto": "North York, Scarborough, Etobicoke, Richmond Hill, Thornhill, Vaughan, Markham, Aurora, Newmarket, East Gwillimbury, Holland Landing, Mississauga, Brampton, Oakville",
    "Vancouver": "Burnaby, Coquitlam, Port Coquitlam, Port Moody, North Vancouver, West Vancouver, New Westminster, Richmond, Surrey, Langley, Maple Ridge",
    "Montreal": "Laval, Longueuil, Brossard",
    "Hamburg": "Pinneberg, Norderstedt, Wedel, Elmshorn, Geesthacht, Henstedt-Ulzburg, Tangstedt",
    "Cologne": "Hürth, Wesseling, Bergheim",
    "Berlin": "Potsdam",
}
# DAILY-V1-2026-11-10.md: message ids of the hand-sent posts.
V1 = {
    "chamedoon_toronto_khane": 725,
    "chamedoon_montreal_khane": 755,
    "chamedoon_vancouver_khane": 485,
    "chamedoon_berlin_khane": 309,
    "chamedoon_hamburg_khane": 183,
    "chamedoon_koln_khane": 155,
}


def forwards(apps, schema_editor):
    HomeChannel = apps.get_model("home", "HomeChannel")
    HomeContentPost = apps.get_model("home", "HomeContentPost")
    for channel in HomeChannel.objects.exclude(city=""):
        extra = SUBURBS.get(channel.city) or (SUBURBS["Cologne"] if channel.city.casefold() in {"köln", "koln"} else "")
        if extra:
            have = {part.strip().casefold() for part in (channel.aliases or "").replace("\n", ",").split(",") if part.strip()}
            add = [name.strip() for name in extra.split(",") if name.strip().casefold() not in have]
            if add:
                channel.aliases = ", ".join(part for part in [(channel.aliases or "").strip().strip(","), *add] if part)
                channel.save(update_fields=["aliases"])
        message = V1.get(channel.username)
        if message and channel.chat_id:
            HomeContentPost.objects.get_or_create(
                kind="daily",
                chat_id=channel.chat_id,
                period_key="20261110",
                defaults={"channel": channel, "message_id": message, "message_ids": [message], "sent_at": datetime(2026, 11, 10, 16, 25, tzinfo=timezone.utc), "payload": {"manual": "daily v1, sent by hand"}},
            )


class Migration(migrations.Migration):
    dependencies = [("home", "0044_content_posts")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
