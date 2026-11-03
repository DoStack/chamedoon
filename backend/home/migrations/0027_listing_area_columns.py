import django.db.models.deletion
from django.db import migrations, models

AREA_CHECKED = models.DateTimeField(blank=True, help_text='When `home_area` was last worked out. Empty: the backfill still has to.', null=True)
HOME_AREA = models.ForeignKey(blank=True, db_constraint=False, db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='listings', to='home.homearea')


def add_columns(apps, schema_editor):
    """One statement holding the table's strictest lock for an instant: both columns are nullable with no default."""
    if schema_editor.connection.vendor != "postgresql":
        listing = apps.get_model("home", "HomeListing")
        schema_editor.add_field(listing, listing._meta.get_field("area_checked_at"))
        schema_editor.add_field(listing, listing._meta.get_field("home_area"))
        return
    with schema_editor.connection.cursor() as cursor:
        # Reads queue behind this lock while it waits, so it gives up early and a later cold start tries again.
        cursor.execute("SET LOCAL lock_timeout = '2s'")
        cursor.execute(
            "ALTER TABLE home_homelisting "
            "ADD COLUMN IF NOT EXISTS area_checked_at timestamp with time zone NULL, "
            "ADD COLUMN IF NOT EXISTS home_area_id bigint NULL"
        )


class Migration(migrations.Migration):
    dependencies = [('home', '0026_home_area_and_saved')]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(model_name='homelisting', name='area_checked_at', field=AREA_CHECKED),
                migrations.AddField(model_name='homelisting', name='home_area', field=HOME_AREA),
            ],
            database_operations=[migrations.RunPython(add_columns, migrations.RunPython.noop)],
        ),
    ]
