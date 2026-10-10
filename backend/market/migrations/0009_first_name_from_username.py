from django.db import migrations


def forwards(apps, _schema_editor):
    from market.migrate import _normalize_first_names

    _normalize_first_names(apps.get_model("users", "User"))


class Migration(migrations.Migration):
    dependencies = [
        ("market", "0008_normalize_user_first_names"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
