import django.db.models.deletion
from django.db import migrations, models

CONSTRAINT = "home_homelisting_home_area_id_fk"


def add_constraint(apps, schema_editor):
    """NOT VALID takes a brief lock and checks only new rows. VALIDATE then scans without blocking reads or writes."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_constraint WHERE conname = %s", [CONSTRAINT])
        if not cursor.fetchone():
            cursor.execute(
                f"ALTER TABLE home_homelisting ADD CONSTRAINT {CONSTRAINT} FOREIGN KEY (home_area_id) "
                "REFERENCES home_homearea (id) DEFERRABLE INITIALLY DEFERRED NOT VALID"
            )
        cursor.execute(f"ALTER TABLE home_homelisting VALIDATE CONSTRAINT {CONSTRAINT}")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [('home', '0028_listing_area_index')]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='homelisting',
                    name='home_area',
                    field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='listings', to='home.homearea'),
                ),
            ],
            database_operations=[migrations.RunPython(add_constraint, migrations.RunPython.noop)],
        ),
    ]
