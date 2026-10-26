from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("home", "0021_rental_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="homealert",
            name="search",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Saved from the Mini App feed: its filters (types, min, max, areas, term, furnished, bills, warm, reg, nowbs, gender, move, ...). Empty for bot alerts.",
            ),
        ),
        migrations.AddField(
            model_name="homealert",
            name="viewed_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="When the user last opened this alert's listings in the Mini App («n جدید» counts from here).",
            ),
        ),
    ]
