from django.db import migrations, models


def copy_source_urls(apps, schema_editor):
    ItemRequest = apps.get_model("item_requests", "ItemRequest")
    MarketPost = apps.get_model("market", "MarketPost")
    for post in MarketPost.objects.exclude(item_request_id=None).only(
        "item_request_id",
        "source_url",
        "channel_username",
        "telegram_message_id",
    ):
        url = (post.source_url or "").strip()
        if not url:
            channel = (post.channel_username or "").strip().lstrip("@")
            if channel and post.telegram_message_id:
                url = f"https://t.me/{channel}/{post.telegram_message_id}"
        if not url:
            continue
        ItemRequest.objects.filter(pk=post.item_request_id, source_url="").update(source_url=url[:255])


class Migration(migrations.Migration):
    dependencies = [
        ("item_requests", "0009_seed_italy_cities"),
        ("market", "0002_marketpost_item_request"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemrequest",
            name="source_url",
            field=models.URLField(blank=True, default="", max_length=255),
        ),
        migrations.RunPython(copy_source_urls, migrations.RunPython.noop),
    ]
