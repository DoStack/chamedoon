from django.db import migrations


def forwards(apps, _schema_editor):
    from market.migrate import _normalize_first_names

    _normalize_first_names(apps.get_model("users", "User"))


class Migration(migrations.Migration):
    dependencies = [
        ("market", "0007_reassign_imported_owners"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
