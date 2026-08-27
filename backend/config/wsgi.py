import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

try:
    from config.startup import run_startup_migrations

    run_startup_migrations()
except Exception:
    pass
