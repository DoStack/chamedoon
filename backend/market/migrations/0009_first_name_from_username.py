from django.db import migrations


def forwards(_apps, _schema_editor):
    from market.migrate import normalize_user_first_names

    normalize_user_first_names()


class Migration(migrations.Migration):
    dependencies = [
        ("market", "0008_normalize_user_first_names"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
