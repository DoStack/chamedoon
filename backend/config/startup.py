import os


def _auto_migrate_enabled() -> bool:
    return os.getenv("AUTO_MIGRATE_ON_STARTUP", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _serverless_runtime() -> bool:
    return bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"))


def run_startup_migrations() -> None:
    if not _serverless_runtime() or not _auto_migrate_enabled():
        return

    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if not plan:
        return

    call_command("migrate", interactive=False, verbosity=0)
