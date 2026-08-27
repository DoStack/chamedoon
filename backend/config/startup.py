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


def run_startup() -> None:
    run_startup_migrations()
    ensure_superuser()


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


def ensure_superuser() -> None:
    if not _serverless_runtime():
        return
    create_superuser_from_env()


def create_superuser_from_env() -> None:
    username = os.getenv("DJANGO_SUPERUSER_USERNAME", "").strip()
    password = os.getenv("DJANGO_SUPERUSER_PASSWORD", "").strip()
    email = os.getenv("DJANGO_SUPERUSER_EMAIL", "").strip()
    if not username or not password:
        return

    from django.contrib.auth.models import User

    user, _created = User.objects.get_or_create(
        username=username,
        defaults={"email": email, "is_staff": True, "is_superuser": True},
    )
    user.email = email or user.email
    user.is_staff = True
    user.is_superuser = True
    user.is_active = True
    user.set_password(password)
    user.save()
