"""Django settings for the lukehirsch project.

Section order, top to bottom:
    Core · Security · Application · Database · Redis · Django misc ·
    LLM · API · Auth · Email · Portfolio · Production guard
"""

import os
from pathlib import Path

from lukehirsch.prod import (
    DEV_ENCRYPTION_KEY,
    DEV_SECRET_KEY,
    env_bool,
    env_int,
    env_list,
    verify_production_secrets,
)

# ─────────────────────────────────────────────────────────────────────────────
# Core: paths & environment
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
BASE_DOMAIN = os.getenv("BASE_DOMAIN", "localhost")

# ─────────────────────────────────────────────────────────────────────────────
# Security & secrets
# ─────────────────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", DEV_SECRET_KEY)

DEBUG = env_bool("DEBUG", True)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", [f".{BASE_DOMAIN}"])

SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "Lax"

# ─────────────────────────────────────────────────────────────────────────────
# Application definition
# ─────────────────────────────────────────────────────────────────────────────
SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

INSTALLED_APPS = [
    # asgi server for WebSockets
    "daphne",
    # django default apps
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # allauth
    "allauth",
    "allauth.usersessions",
    "allauth.account",
    "allauth.mfa",
    "allauth.headless",
    # third-party apps
    "rest_framework",
    "corsheaders",
    "channels",
    "django_filters",
    "drf_spectacular",
    # local apps
    "llm_connector",
    "vector_store",
    "jac",
    "spa",
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
    "allauth.account.middleware.AccountMiddleware",
    "allauth.usersessions.middleware.UserSessionsMiddleware",
    "lukehirsch.middleware.AdminRequireMfaMiddleware",
]

ROOT_URLCONF = "lukehirsch.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "lukehirsch.wsgi.application"
ASGI_APPLICATION = "lukehirsch.asgi.application"

# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "co2mmute"),
        "USER": os.environ.get("POSTGRES_USER", "co2mmute"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "co2mmute"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", "60")),
    }
    if not DEBUG
    else {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Redis: Channels, cache, Celery
# ─────────────────────────────────────────────────────────────────────────────
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

CHANNEL_LAYERS = {
    "default": {
        # MUST stay the pub/sub layer: the classic core.RedisChannelLayer block-polls
        # with bzpopmin and redis-py >= 5.1 raises TimeoutError on that read after 5s
        # idle — killing the socket of every generation run that thinks for a while.
        "BACKEND": "channels_redis.pubsub.RedisPubSubChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    }
}

CACHES = {
    "default": (
        {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
        if DEBUG
        else {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    )
}

# Celery Configuration
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60

# ─────────────────────────────────────────────────────────────────────────────
# Django misc: passwords, i18n, static & media
# ─────────────────────────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

STATIC_URL = "static/"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ─────────────────────────────────────────────────────────────────────────────
# LLM: HirschAI & connector
# ─────────────────────────────────────────────────────────────────────────────
HIRSCHAI = {
    "url": os.getenv("HIRSCHAI_URL", "http://localhost:11434"),
    "model": os.getenv("HIRSCHAI_MODEL", "qwen3.5:9b"),
    "embed_model": os.getenv("HIRSCHAI_EMBED_MODEL", "qwen3-embedding:0.6b"),
    "timeout": env_int("LLM_TIMEOUT", 300),
    "think": env_bool("LLM_THINKING", False),
}

LLM_LOGGING = True

LLM_URL_ALLOWLIST = env_list(
    "LLM_URL_ALLOWLIST", ["localhost", "127.0.0.1", "::1"] if DEBUG else []
)
LLM_URL_ALLOW_PRIVATE = env_bool("LLM_URL_ALLOW_PRIVATE", False)

LLM_ENCRYPTION_KEY = os.getenv("LLM_ENCRYPTION_KEY", DEV_ENCRYPTION_KEY)

VECTOR_STORE = os.getenv("VECTOR_STORE", "")

# Owner of the system-owned rows (HirschAI LLMConfig, etc.).
SYSTEM_USER_USERNAME = "system"

# ─────────────────────────────────────────────────────────────────────────────
# API: frontend origin, CORS/CSRF, DRF, schema
# ─────────────────────────────────────────────────────────────────────────────
FRONTEND_URL = os.getenv(
    "FRONTEND_URL",
    "http://localhost:5173" if DEBUG else f"https://app.{BASE_DOMAIN}",
)

# CORS — allow the React dev server to send credentialed requests
CORS_ALLOWED_ORIGINS = [FRONTEND_URL]
CORS_ALLOWED_ORIGIN_REGEXES = [r"^http://[a-z0-9-]+\.localhost:5173$"]
CORS_ALLOW_CREDENTIALS = True

# CSRF — trust the frontend origin so the CSRF middleware accepts its requests
CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS",
    [FRONTEND_URL, f"https://*.{BASE_DOMAIN}"],
)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_RATES": {
        "llm-chat": os.getenv("LLM_CHAT_RATE", "20/min"),
        "portfolio": os.getenv("PORTFOLIO_RATE", "60/hour"),
        "portfolio-rank": os.getenv("PORTFOLIO_RANK_RATE", "6/hour"),
        "portfolio-intro": os.getenv("PORTFOLIO_INTRO_RATE", "6/hour"),
        "generation": os.getenv("GENERATION_RATE", "20/day"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "lukehirsch API",
    "DESCRIPTION": "JAC career DB + LLM connector + portfolio API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ─────────────────────────────────────────────────────────────────────────────
# Auth: allauth & MFA
# ─────────────────────────────────────────────────────────────────────────────
# allauth account settings
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True
ACCOUNT_RATE_LIMITS = {
    "reset_password": "20/m/ip,5/m/key,3/h/key,5/d/key",
}
ACCOUNT_ALLOW_SIGNUPS = env_bool("ACCOUNT_ALLOW_SIGNUPS", True)

# Custom adapter dedupes 'account already exists' mails per address;
ACCOUNT_ADAPTER = "lukehirsch.adapter.HarassmentResistantAccountAdapter"

# allauth headless
HEADLESS_ONLY = True
HEADLESS_FRONTEND_URLS = {
    "account_reset_password_from_key": FRONTEND_URL + "/auth/reset-password/{key}",
    "account_signup": FRONTEND_URL + "/auth/signup",
    "account_reset_password": FRONTEND_URL + "/auth/request-reset",
}

# MFA: TOTP (authenticator app) + WebAuthn passkeys
MFA_SUPPORTED_TYPES = ["recovery_codes", "totp", "webauthn"]
MFA_PASSKEY_LOGIN_ENABLED = True
MFA_PASSKEY_SIGNUP_ENABLED = True
# fido2 ≤1.1.3 does not treat localhost as a secure origin; set True for local dev only
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = DEBUG

# ─────────────────────────────────────────────────────────────────────────────
# Email
# ─────────────────────────────────────────────────────────────────────────────
EMAIL_BACKEND = (
    "lukehirsch.debug_email_backends.ReadableConsoleEmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.strato.de")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-reply@luke-hirsch.de")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# ─────────────────────────────────────────────────────────────────────────────
# SPA / portfolio
# ─────────────────────────────────────────────────────────────────────────────
PORTFOLIO_OWNER_USERNAME = os.getenv("PORTFOLIO_OWNER_USERNAME", "lukas")

RESERVED_SUBDOMAINS = set(
    env_list(
        "RESERVED_SUBDOMAINS",
        ["app", "api", "www", "admin", "static", "media", "mail", "ns", "ns1", "ns2"],
    )
)
PORTFOLIO_ORIGIN_TEMPLATE = os.getenv(
    "PORTFOLIO_ORIGIN_TEMPLATE",
    "http://{handle}.localhost:5173" if DEBUG else f"https://{{handle}}.{BASE_DOMAIN}",
)

# ─────────────────────────────────────────────────────────────────────────────
# Production guard — must run last
# ─────────────────────────────────────────────────────────────────────────────
verify_production_secrets(
    debug=DEBUG,
    secret_key=SECRET_KEY,
    encryption_key=LLM_ENCRYPTION_KEY,
)
