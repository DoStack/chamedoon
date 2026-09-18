import os
from pathlib import Path

import dj_database_url
from django.urls import reverse
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent

load_dotenv(ROOT_DIR / ".env")
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("SECRET_KEY") or os.environ.get("DJANGO_SECRET_KEY", "insecure-dev-key-do-not-use-in-production")
DEBUG = os.environ.get("DEBUG", "True").lower() in {"1", "true", "yes"}
_ON_VERCEL = bool(os.environ.get("VERCEL"))

def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


ALLOWED_HOSTS = _split_csv(os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1"))
if DEBUG:
    for host in ("testserver", "localhost", "127.0.0.1"):
        if host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(host)
if _ON_VERCEL:
    for host in (".vercel.app", os.environ.get("VERCEL_URL", "").split(":")[0]):
        if host and host not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(host)

INSTALLED_APPS = [
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "users.apps.UsersConfig",
    "item_requests.apps.RequestsConfig",
    "matching.apps.MatchingConfig",
    "notifications.apps.NotificationsConfig",
    "api.apps.ApiConfig",
    "miniapp.apps.MiniappConfig",
    "market.apps.MarketConfig",
    "support.apps.SupportConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "miniapp.middleware.MiniAppFrameMiddleware",
]
if not DEBUG or _ON_VERCEL:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
    STORAGES = {
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
        }
    }

if _ON_VERCEL or os.environ.get("TRUST_PROXY", "").lower() in {"1", "true", "yes"}:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


def _first_env(*names: str) -> str:
    for name in names:
        for candidate in (name, f"koolbar_{name}"):
            value = (os.environ.get(candidate) or "").strip()
            if value:
                return value
        suffix = f"_{name}"
        for key, raw in os.environ.items():
            if key.endswith(suffix) and (raw or "").strip():
                return raw.strip()
    return ""


def _with_ssl(url: str) -> str:
    if not _ON_VERCEL or "sslmode=" in url.lower():
        return url
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}sslmode=require"


def _database_url() -> tuple[str, str]:
    url = _first_env(
        "POSTGRES_URL_NON_POOLING",
        "DATABASE_URL_UNPOOLED",
        "DATABASE_URL",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
    )
    if url:
        return _with_ssl(url), "url"
    host = _first_env("POSTGRES_HOST", "PGHOST")
    name = _first_env("POSTGRES_DB", "POSTGRES_DATABASE", "PGDATABASE")
    user = _first_env("POSTGRES_USER", "PGUSER")
    if host and name and user:
        from urllib.parse import quote

        password = _first_env("POSTGRES_PASSWORD", "PGPASSWORD")
        port = _first_env("POSTGRES_PORT", "PGPORT") or "5432"
        sslmode = _first_env("POSTGRES_SSLMODE") or ("require" if _ON_VERCEL else "prefer")
        return (
            f"postgres://{quote(user, safe='')}:{quote(password, safe='')}"
            f"@{host}:{port}/{name}?sslmode={sslmode}",
            "env",
        )
    return "postgres://koolbar:koolbar@localhost:5432/koolbar", "local-default"


_DATABASE_URL, DATABASE_SOURCE = _database_url()

DATABASES = {
    "default": dj_database_url.parse(
        _DATABASE_URL,
        conn_max_age=0 if _ON_VERCEL else 60,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "api.authentication.TelegramJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
}

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000",
    ).split(",")
    if origin.strip()
]

CSRF_TRUSTED_ORIGINS = _split_csv(
    os.environ.get(
        "CSRF_TRUSTED_ORIGINS",
        "http://localhost:3000,http://localhost:8000",
    )
)
if _ON_VERCEL:
    for origin in ("https://*.vercel.app",):
        if origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(origin)
    vercel_url = os.environ.get("VERCEL_URL", "").strip()
    if vercel_url:
        origin = vercel_url if vercel_url.startswith("http") else f"https://{vercel_url}"
        if origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(origin)

if _ON_VERCEL:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = "None"
    CSRF_COOKIE_SAMESITE = "None"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
BOT_SERVICE_SECRET = os.environ.get("BOT_SERVICE_SECRET", "")
TELEGRAM_MINI_APP_URL = os.environ.get("TELEGRAM_MINI_APP_URL", "").rstrip("/")
TELEGRAM_MINI_APP_SHORT_NAME = os.environ.get("TELEGRAM_MINI_APP_SHORT_NAME", "app")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
TELEGRAM_CHANNEL_USERNAME = os.environ.get("TELEGRAM_CHANNEL_USERNAME", "").strip().lstrip("@")
DEFAULT_TELEGRAM_CHANNEL_URL = "https://t.me/+26pUh8_5u0w1MTVk"
TELEGRAM_CHANNEL_URL = (os.environ.get("TELEGRAM_CHANNEL_URL") or DEFAULT_TELEGRAM_CHANNEL_URL).strip() or DEFAULT_TELEGRAM_CHANNEL_URL
TELEGRAM_GROUP_USERNAME = os.environ.get("TELEGRAM_GROUP_USERNAME", "").strip()
TELEGRAM_CHANNEL_ENABLED = os.environ.get("TELEGRAM_CHANNEL_ENABLED", "").lower() in {"1", "true", "yes"}
TELEGRAM_AUTH_MAX_AGE_SECONDS = int(os.environ.get("TELEGRAM_AUTH_MAX_AGE_SECONDS", "86400"))
CRON_SECRET = os.environ.get("CRON_SECRET", "")
MARKET_CHANNEL_USERNAME = os.environ.get("MARKET_CHANNEL_USERNAME", "koolbar_international").strip().lstrip("@")
MARKET_CHANNEL_USERNAMES = os.environ.get(
    "MARKET_CHANNEL_USERNAMES",
    "koolbar_international,koolbarcanada,CoolbarEUIRAN,CoolbarUKIRAN,bahsazadkolbar,HamrahbarUSA",
)
MARKET_INGEST_ENABLED = os.environ.get("MARKET_INGEST_ENABLED", "true").lower() in {"1", "true", "yes"}
MARKET_INGEST_TELEGRAM_USER_ID = int(os.environ.get("MARKET_INGEST_TELEGRAM_USER_ID", "1") or "1")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip().rstrip("/")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free").strip() or "openrouter/free"
OPENROUTER_MODEL_FALLBACKS = os.environ.get(
    "OPENROUTER_MODEL_FALLBACKS",
    "google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it:free",
).strip()
OPENROUTER_HTTP_REFERER = os.environ.get("OPENROUTER_HTTP_REFERER", "").strip()
OPENROUTER_APP_TITLE = os.environ.get("OPENROUTER_APP_TITLE", "Koolbar").strip() or "Koolbar"
OPENROUTER_TIMEOUT_SECONDS = int(os.environ.get("OPENROUTER_TIMEOUT_SECONDS", "15") or "15")
OPENROUTER_MAX_TOKENS = int(os.environ.get("OPENROUTER_MAX_TOKENS", "800") or "800")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
OPENAI_TIMEOUT_SECONDS = int(os.environ.get("OPENAI_TIMEOUT_SECONDS", "20") or "20")
OPENAI_MAX_TOKENS = int(os.environ.get("OPENAI_MAX_TOKENS", "400") or "400")
MARKET_LLM_REVIEW_LIMIT = int(os.environ.get("MARKET_LLM_REVIEW_LIMIT", "12") or "12")
MARKET_LLM_RETRY_HOURS = int(os.environ.get("MARKET_LLM_RETRY_HOURS", "6") or "6")
JWT_ACCESS_TOKEN_HOURS = int(os.environ.get("JWT_ACCESS_TOKEN_HOURS", str(24 * 30)))


def _admin_link(name: str):
    # Callables only: Vercel inspects UNFOLD at import, before apps/urls are ready.
    return lambda request, url_name=name: reverse(url_name)


def _admin_active(name: str):
    def is_active(request, url_name=name):
        path = reverse(url_name)
        if url_name == "admin:index":
            return request.path.rstrip("/") == path.rstrip("/")
        return request.path.startswith(path)

    return is_active


UNFOLD = {
    "SITE_TITLE": "Koolbar Admin",
    "SITE_HEADER": "Koolbar Back Office",
    "SITE_SUBHEADER": "Operations",
    "SITE_SYMBOL": "local_shipping",
    "SHOW_HISTORY": True,
    "DASHBOARD_CALLBACK": "config.dashboard.dashboard_callback",
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Overview",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Dashboard",
                        "icon": "dashboard",
                        "link": _admin_link("admin:index"),
                        "active": _admin_active("admin:index"),
                    },
                ],
            },
            {
                "title": "Marketplace",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Requests",
                        "icon": "inventory_2",
                        "link": _admin_link("admin:item_requests_itemrequest_changelist"),
                        "active": _admin_active("admin:item_requests_itemrequest_changelist"),
                    },
                    {
                        "title": "Matches",
                        "icon": "handshake",
                        "link": _admin_link("admin:matching_match_changelist"),
                        "active": _admin_active("admin:matching_match_changelist"),
                    },
                    {
                        "title": "Ratings",
                        "icon": "star",
                        "link": _admin_link("admin:matching_matchrating_changelist"),
                        "active": _admin_active("admin:matching_matchrating_changelist"),
                    },
                    {
                        "title": "Users",
                        "icon": "group",
                        "link": _admin_link("admin:users_user_changelist"),
                        "active": _admin_active("admin:users_user_changelist"),
                    },
                    {
                        "title": "Support",
                        "icon": "support_agent",
                        "link": _admin_link("admin:support_supportticket_changelist"),
                        "active": _admin_active("admin:support_supportticket_changelist"),
                    },
                    {
                        "title": "Support messages",
                        "icon": "chat",
                        "link": _admin_link("admin:support_supportmessage_changelist"),
                        "active": _admin_active("admin:support_supportmessage_changelist"),
                    },
                ],
            },
            {
                "title": "Catalog",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Categories",
                        "icon": "category",
                        "link": _admin_link("admin:item_requests_category_changelist"),
                        "active": _admin_active("admin:item_requests_category_changelist"),
                    },
                    {
                        "title": "Countries",
                        "icon": "public",
                        "link": _admin_link("admin:item_requests_country_changelist"),
                        "active": _admin_active("admin:item_requests_country_changelist"),
                    },
                    {
                        "title": "Cities",
                        "icon": "location_city",
                        "link": _admin_link("admin:item_requests_city_changelist"),
                        "active": _admin_active("admin:item_requests_city_changelist"),
                    },
                ],
            },
            {
                "title": "Market crawl",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Market posts",
                        "icon": "campaign",
                        "link": _admin_link("admin:market_marketpost_changelist"),
                        "active": _admin_active("admin:market_marketpost_changelist"),
                    },
                    {
                        "title": "Ingest state",
                        "icon": "sync",
                        "link": _admin_link("admin:market_marketingeststate_changelist"),
                        "active": _admin_active("admin:market_marketingeststate_changelist"),
                    },
                    {
                        "title": "Manual extract",
                        "icon": "download",
                        "link": _admin_link("admin:market_extract"),
                        "active": _admin_active("admin:market_extract"),
                    },
                ],
            },
            {
                "title": "Staff",
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": "Staff users",
                        "icon": "manage_accounts",
                        "link": _admin_link("admin:auth_user_changelist"),
                        "active": _admin_active("admin:auth_user_changelist"),
                    },
                    {
                        "title": "Groups",
                        "icon": "admin_panel_settings",
                        "link": _admin_link("admin:auth_group_changelist"),
                        "active": _admin_active("admin:auth_group_changelist"),
                    },
                ],
            },
        ],
    },
}
