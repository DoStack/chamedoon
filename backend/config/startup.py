import logging
import os
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)
STARTUP_LOCK_TIMEOUT = "5s"
MIGRATE_LOCK_KEY = 7_204_911
MIGRATE_WAIT_SECONDS = 25
MIGRATE_WAIT_STEP_SECONDS = 0.5


def _auto_migrate_enabled() -> bool:
    # A Preview shares the Neon store with Production, so it must never migrate, whatever the variable says.
    if os.getenv("VERCEL_ENV", "").strip().lower() == "preview":
        return False
    return os.getenv("AUTO_MIGRATE_ON_STARTUP", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _serverless_runtime() -> bool:
    return bool(os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"))


def run_startup() -> None:
    # A failed migrate rolls back and leaves its plan pending, so the next cold start tries again by itself.
    try:
        run_startup_migrations()
    except Exception:
        logger.exception("startup_migrate_failed: the app runs on the old schema until a later cold start migrates")
    ensure_superuser()


@contextmanager
def _lock_timeout(connection):
    """A cold start must not queue behind a busy table and hold every query that queues behind it."""
    if connection.vendor != "postgresql":
        yield
        return
    with connection.cursor() as cursor:
        cursor.execute(f"SET lock_timeout = '{STARTUP_LOCK_TIMEOUT}'")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET lock_timeout")


@contextmanager
def _migrate_lock(connection):
    """One instance migrates at a time. Others wait a little, since they will read the new columns as soon as they serve."""
    if connection.vendor != "postgresql":
        yield True
        return
    deadline = time.monotonic() + MIGRATE_WAIT_SECONDS
    held = False
    while True:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [MIGRATE_LOCK_KEY])
            held = cursor.fetchone()[0]
        if held or time.monotonic() >= deadline:
            break
        time.sleep(MIGRATE_WAIT_STEP_SECONDS)
    try:
        yield held
    finally:
        if held:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [MIGRATE_LOCK_KEY])


def run_startup_migrations() -> None:
    if not _serverless_runtime() or not _auto_migrate_enabled():
        return

    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    def pending() -> list:
        executor = MigrationExecutor(connection)
        return executor.migration_plan(executor.loader.graph.leaf_nodes())

    if not pending():
        return
    with _migrate_lock(connection) as held:
        if not held:
            logger.warning("startup_migrate_busy: another instance is still migrating")
            return
        # It may have finished while this instance waited.
        plan = pending()
        if not plan:
            return
        logger.warning("startup_migrate_begin pending=%d", len(plan))
        with _lock_timeout(connection):
            call_command("migrate", interactive=False, verbosity=0)
        logger.warning("startup_migrate_done pending=%d", len(plan))


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
