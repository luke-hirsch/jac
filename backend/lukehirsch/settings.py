import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv(
    "SECRET_KEY", "django-insecure-g4pj@dk!pf+e#+8^5t-ic(avl(ng9e=@3%ziwrls-!5sq%y6s5"
)

DEBUG = os.getenv("DEBUG", True)

ALLOWED_HOSTS = [os.getenv("ALLOWED_HOST", "localhost")]

SITE_ID = 1

SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True  # default, explicit is nice
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "Lax"

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
    # sites framework (required by allauth)
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


REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
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


LLM = {
    "default": {
        "provider": "custom",  # Ollama / any OpenAI-compat server
        "url": os.getenv("OLLAMA_URL", "http://localhost:11434/v1"),
        "model": "qwen3.5:0.8b",
        "timeout": 120,
        "think": False,
    },
}

LLM_LOGGING = True

LLM_ENCRYPTION_KEY = os.getenv(
    "LLM_ENCRYPTION_KEY",
    "ZGphbmdvLWluc2VjdXJlLWxsbS1kZXYta2V5LW9ubHk=",
)

# Sentinel user that owns read-only system defaults (e.g. `jac.Domain` rows
# shared across all users). Looked up by username so it survives pk drift.
SYSTEM_USER_USERNAME = "system"

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

# CORS — allow the React dev server to send credentialed requests
CORS_ALLOWED_ORIGINS = [FRONTEND_URL]
CORS_ALLOW_CREDENTIALS = True

# CSRF — trust the frontend origin so the CSRF middleware accepts its requests
CSRF_TRUSTED_ORIGINS = [FRONTEND_URL]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Default is AllowAny so the public spa (portfolio link) endpoints can be
    # reached without auth. Every other viewset must set permission_classes
    # explicitly — see e.g. jac.views.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "lukehirsch API",
    "DESCRIPTION": "JAC career DB + LLM connector + portfolio API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# allauth account settings
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True

# Per-address harassment cap on password-reset requests. allauth's default
# (20/m/ip,5/m/key) only blocks bursts; we add hour + day windows on the
# email key so an attacker can't drip emails at a victim's address by
# rotating IPs. Legit "I forgot my password" usage stays untouched —
# nobody requests >5 resets per day for themselves.
ACCOUNT_RATE_LIMITS = {
    "reset_password": "20/m/ip,5/m/key,3/h/key,5/d/key",
}

# Custom adapter dedupes 'account already exists' mails per address; the default
# signup rate limit is IP-only and lets an attacker spam a victim by rotating IPs.
ACCOUNT_ADAPTER = "lukehirsch.adapter.HarassmentResistantAccountAdapter"

# allauth headless — JSON API only, no template views
HEADLESS_ONLY = True
HEADLESS_FRONTEND_URLS = {
    # password-reset email link points at React; React calls POST /_allauth/browser/v1/auth/password/reset
    "account_reset_password_from_key": FRONTEND_URL + "/auth/reset-password/{key}",
    # used in the "no account found, sign up here" email allauth sends when a
    # password-reset is requested for an unknown address (enumeration prevention)
    "account_signup": FRONTEND_URL + "/auth/signup",
    # used in the "account already exists" email allauth sends when signup
    # collides with an existing address
    "account_reset_password": FRONTEND_URL + "/auth/request-reset",
}

# MFA: TOTP (authenticator app) + WebAuthn passkeys
MFA_SUPPORTED_TYPES = ["recovery_codes", "totp", "webauthn"]
MFA_PASSKEY_LOGIN_ENABLED = True
MFA_PASSKEY_SIGNUP_ENABLED = True
# fido2 ≤1.1.3 does not treat localhost as a secure origin; set True for local dev only
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = DEBUG


EMAIL_BACKEND = (
    "lukehirsch.debug_email_backends.ReadableConsoleEmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.strato.de")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() == "true"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-reply@luke-hirsch.de")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
