from django.db import migrations


def mark_imported_request_owners(apps, schema_editor):
    User = apps.get_model("users", "User")
    ItemRequest = apps.get_model("item_requests", "ItemRequest")
    imported_user_ids = (
        ItemRequest.objects.filter(imported=True).values_list("user_id", flat=True).distinct()
    )
    User.objects.filter(pk__in=imported_user_ids).update(from_market=True)


class Migration(migrations.Migration):
    dependencies = [
        ("item_requests", "0016_itemrequest_closed_status"),
        ("users", "0002_user_origin_and_botstart"),
    ]

    operations = [
        migrations.RunPython(mark_imported_request_owners, migrations.RunPython.noop),
    ]
