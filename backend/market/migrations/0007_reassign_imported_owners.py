from django.db import migrations


def forwards(apps, _schema_editor):
    from market.migrate import reassign_imported_request_owners

    # This calls live code on the live model, which has columns this migration's state has not. Every database with rows
    # ran it long ago, so only an empty database comes here, and that has nothing to reassign.
    if not apps.get_model("market", "MarketPost").objects.filter(item_request__isnull=False).exists():
        return
    reassign_imported_request_owners()


class Migration(migrations.Migration):
    dependencies = [
        ("market", "0006_lookback_cursor"),
        ("item_requests", "0016_itemrequest_closed_status"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
