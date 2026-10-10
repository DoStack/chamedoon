from django.db import migrations


def forwards(_apps, _schema_editor):
    from market.migrate import reassign_imported_request_owners

    reassign_imported_request_owners()


class Migration(migrations.Migration):
    dependencies = [
        ("market", "0006_lookback_cursor"),
        ("item_requests", "0016_itemrequest_closed_status"),
        ("users", "0002_user_origin_and_botstart"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
