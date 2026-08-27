import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

try:
    from config.startup import run_startup

    run_startup()
except Exception:
    pass
