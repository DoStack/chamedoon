"""Settings of the Pulse process on the Chamedoon VM. The main settings, then a read-only database and its own URLs.

Env (from /etc/chamedoon-pulse/*.env): PULSE_DB_URL (role pulse_ro), DJANGO_SECRET_KEY (the production key, so the
admin session cookie from the main site is valid here), PULSE_ALLOWED_HOSTS, PULSE_ADMIN_ORIGIN, POSTHOG_PERSONAL_KEY,
POSTHOG_PROJECT_ID, HOME_BOT_TOKEN, HOME_BOT_USERNAME, PULSE_TEAM_USERNAMES.
"""

import os

import dj_database_url

from config.settings import *
from config.settings import INSTALLED_APPS, MIDDLEWARE

DEBUG = os.environ.get("PULSE_DEBUG", "").lower() in {"1", "true", "yes"}
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("PULSE_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

_url = os.environ.get("PULSE_DB_URL", "")
if _url:
    DATABASES = {"default": dj_database_url.parse(_url, conn_max_age=60)}
    # PgBouncer in transaction mode: a named cursor could move to another server connection between fetches.
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

ROOT_URLCONF = "pulse.urls"
WSGI_APPLICATION = "pulse.wsgi.application"
INSTALLED_APPS = [*INSTALLED_APPS, *(a for a in ("django.contrib.humanize", "pulse") if a not in INSTALLED_APPS)]
TIME_ZONE = "America/Toronto"  # what the pages show; the database stays in UTC

# No request telemetry and no Mini App frame rules here; sessions are read, never written.
MIDDLEWARE = [m for m in MIDDLEWARE if m not in {"telemetry.middleware.ApiCaptureMiddleware", "miniapp.middleware.MiniAppFrameMiddleware"}]
SESSION_SAVE_EVERY_REQUEST = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = False

# Vercel forwards only /admin/pulse/, so the static files live under it too.
STATIC_URL = "/admin/pulse/static/"
STATIC_ROOT = os.environ.get("PULSE_STATIC_ROOT", str(BASE_DIR / "pulse_static"))
STORAGES = {"staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"}}
if "whitenoise.middleware.WhiteNoiseMiddleware" not in MIDDLEWARE:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "pulse"}}

PULSE_ADMIN_ORIGIN = os.environ.get("PULSE_ADMIN_ORIGIN", "https://chamed0on.vercel.app").rstrip("/")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
