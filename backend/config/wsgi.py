import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

try:
    from django.core.management import call_command
    from config.startup import run_startup_migrations

    run_startup_migrations()
    if os.getenv("VERCEL"):
        call_command("collectstatic", interactive=False, verbosity=0)
except Exception:
    pass
