"""WSGI entry of the Pulse process. No startup migrations and no superuser seeding: this process cannot write."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pulse.settings")

application = get_wsgi_application()
