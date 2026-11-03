from django.db import migrations

INDEX = "home_homelisting_home_area_id_idx"


def build_index(apps, schema_editor):
    """CONCURRENTLY: writes and reads go on. A failed try leaves an invalid index, so a retry drops it first."""
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid WHERE c.relname = %s AND NOT i.indisvalid",
            [INDEX],
        )
        if cursor.fetchone():
            cursor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX}")
        cursor.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX} ON home_homelisting (home_area_id)")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [('home', '0027_listing_area_columns')]

    operations = [migrations.RunPython(build_index, migrations.RunPython.noop)]
