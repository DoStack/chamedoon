from django.db import migrations


def forwards(_apps, _schema_editor):
    from market.migrate import normalize_user_first_names

    normalize_user_first_names()


class Migration(migrations.Migration):
    dependencies = [
        ("market", "0007_reassign_imported_owners"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
