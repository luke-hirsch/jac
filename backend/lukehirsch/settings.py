"""Django settings for the lukehirsch project.

Section order, top to bottom:
    Core · Security · Application · Database · Redis · Django misc ·
    LLM · API · Auth · Email · Portfolio · Logging · Production guard
"""

import os
from pathlib import Path

import dj_database_url
from celery.schedules import crontab

from lukehirsch.prod import (
    DEV_DATABASE_URL,
    DEV_ENCRYPTION_KEY,
    DEV_SECRET_KEY,
    env_bool,
    env_int,
    env_list,
    load_local_env,
    verify_production_secrets,
)

# ─────────────────────────────────────────────────────────────────────────────
# Core: paths & environment
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
# Laptops only (gitignored, kept out of the image): `DEBUG=1` lives here.
load_local_env(BASE_DIR / ".env.local")
BASE_DOMAIN = os.getenv("BASE_DOMAIN", "localhost")
# The commit this process runs: a build arg of the image (Dockerfile), "dev" locally.
RELEASE = os.getenv("RELEASE", "dev")

# ─────────────────────────────────────────────────────────────────────────────
# Security & secrets
# ─────────────────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", DEV_SECRET_KEY)

# Off unless something turns it on: a forgotten variable can't put a public site in debug mode.
DEBUG = env_bool("DEBUG", False)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", [f".{BASE_DOMAIN}"])

SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = "Lax"

# TLS ends at a proxy in front of nginx; nginx passes its X-Forwarded-Proto through. Without this,
# request.is_secure() is False behind the proxy: absolute URLs come out http:// (mixed content,
# blocked by the browser) and SECURE_SSL_REDIRECT would loop forever.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = not DEBUG
# Health checks speak plain http over the container network: /health/ and /health/queues.
SECURE_REDIRECT_EXEMPT = [r"^health/"]
# Start at an hour; raise to 31536000 once a week of prod has run clean. includeSubDomains is
# safe because the wildcard cert covers every host. No preload: that's a one-way door.
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 0 if DEBUG else 3600)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False
SILENCED_SYSTEM_CHECKS = [
    "security.W021",  # HSTS preload off on purpose (see SECURE_HSTS_PRELOAD)
]

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
# Postgres in dev, the suite, CI and prod: what the suite passes is what prod runs. `or`, not a
# getenv default: `.env.example` ships `DATABASE_URL=` empty, and empty has to count as unset.
DATABASE_URL = os.getenv("DATABASE_URL") or DEV_DATABASE_URL
# parse() leaves CONN_MAX_AGE at 0, which is what Django's docs ask for under ASGI.
DATABASES = {"default": dj_database_url.parse(DATABASE_URL)}

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

# Celery. The status rows carry every job's outcome, so there is no result backend.
CELERY_BROKER_URL = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_TIMEZONE = "UTC"
CELERY_TASK_IGNORE_RESULT = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
# The model server may go away under a running job: keep the job until it's done, let it run again.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
# Queues, by what's scarce. `jac-ai` is the model server's single lane; the worker that serves it
# stays light, because the model runs behind HIRSCHAI_URL.
QUEUE_AI = "jac-ai"
# Runs on a user's own commercial key need no model server, so they never wait behind one.
QUEUE_CLOUD = "jac-cloud"
# Short AI jobs somebody is waiting for: editor tools, dossiers, the portfolio intro. A generation
# run never holds these up.
QUEUE_QUICK = "jac-quick"
# What isn't AI at all: mail and housekeeping. Its own lane, so AI jobs asleep in their slots while
# the model server wakes can't make a verification code outlive its 15 minutes.
QUEUE_MAIL = "jac-mail"
CELERY_TASK_DEFAULT_QUEUE = QUEUE_AI
CELERY_TASK_ROUTES = {
    "jac.tasks.generate_run": {"queue": QUEUE_AI},  # jac-cloud is chosen at enqueue
    "jac.tasks.sync_user_vectors": {"queue": QUEUE_AI},
    "llm_connector.tasks.run_ai_job": {"queue": QUEUE_QUICK},
    "jac.tasks.notify_run_results": {"queue": QUEUE_MAIL},
    "jac.tasks.reap_stuck_runs": {"queue": QUEUE_MAIL},
    "spa.tasks.send_email": {"queue": QUEUE_MAIL},
    "spa.tasks.housekeeping": {"queue": QUEUE_MAIL},
}
# Periodic jobs. One scheduler only: the worker that serves jac-mail runs it (`-B`).
CELERY_BEAT_SCHEDULE = {
    "reap-stuck-runs": {
        "task": "jac.tasks.reap_stuck_runs",
        "schedule": 15 * 60,
    },
    "housekeeping": {
        "task": "spa.tasks.housekeeping",
        "schedule": crontab(hour=3, minute=30),
    },
}
# The order that keeps a job from being lost or started twice, shortest first (TimingTests):
#   compute wait < soft limit < hard limit < visibility timeout < expiry
# A job at the hard limit is gone before the broker would hand it out again, and a job the broker
# hands out again after a crash hasn't expired by then.
LLM_COMPUTE_WAIT_S = env_int("LLM_COMPUTE_WAIT_S", 15 * 60)
CELERY_TASK_SOFT_TIME_LIMIT = 40 * 60
CELERY_TASK_TIME_LIMIT = 45 * 60
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": 60 * 60,
    "socket_connect_timeout": 5,
}
GENERATION_EXPIRES_S = 3 * 60 * 60
# A short AI job has somebody polling for it: it gets a shorter leash on every count. Its wait still
# outlasts a cold start (about 3 minutes).
AI_JOB_WAIT_S = 5 * 60
PORTFOLIO_INTRO_WAIT_S = 3 * 60
AI_JOB_SOFT_LIMIT_S = 10 * 60
AI_JOB_EXPIRES_S = 15 * 60
# Mail about finished runs only for runs this long: whoever waits less has the tab open.
RUN_NOTIFY_AFTER_S = 2 * 60
# Batch generation: the most postings one request can queue. The daily cap counts each of them.
GENERATION_BATCH_MAX = 10
# On SIGQUIT (compose's stop_signal): this long to finish, then the job goes back on the queue for
# the next worker, instead of waiting out the visibility timeout. Shorter than compose's
# stop_grace_period (5 min). SIGTERM never reaches this: a warm shutdown just waits.
CELERY_WORKER_SOFT_SHUTDOWN_TIMEOUT = 240

# ─────────────────────────────────────────────────────────────────────────────
# Django misc: passwords, i18n, static & media
# ─────────────────────────────────────────────────────────────────────────────
# Argon2 first — the PHC winner and Django's own recommendation over the PBKDF2 default;
# memory-hard, so a stolen hash is expensive to attack with GPUs. Needs argon2-cffi
# (`Django[argon2]` in requirements.txt): without it EVERY set_password/check_password
# raises, so a server that skipped `pip install -r` locks everyone out. Guarded by
# ArgonProductionHasherTests.
#
# The rest of Django's default chain stays listed on purpose: hashes already written as
# pbkdf2_sha256 must keep verifying, and Django rewrites each one to argon2 on that
# user's next successful login. Never prune this list while old hashes exist in the DB.
#
# Tests do NOT run on this — see lukehirsch/test_settings.py.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

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
# collectstatic target, baked into the image; nginx serves it at /static/ (only the admin has
# static files).
STATIC_ROOT = Path(os.getenv("STATIC_ROOT", BASE_DIR / "staticfiles"))
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", BASE_DIR / "media"))
# Owner-only uploads (certificates, signatures). Deliberately OUTSIDE MEDIA_ROOT: nginx serves
# MEDIA_ROOT publicly at /media/, and nothing public points here. Served by owner-checked views
# (lukehirsch/private_media.py); PRIVATE_MEDIA_URL is nginx's `internal` location for them.
PRIVATE_MEDIA_ROOT = Path(os.getenv("PRIVATE_MEDIA_ROOT", BASE_DIR / "private_media"))
PRIVATE_MEDIA_URL = "/_private/"
# On: views answer with X-Accel-Redirect and nginx streams the file. Off (dev): Django streams it.
PRIVATE_MEDIA_ACCEL = env_bool("PRIVATE_MEDIA_ACCEL", not DEBUG)

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
# What this process's environment actually names. It wins over the system row on every call, so
# moving the model server means changing env, and web + worker can carry different keys. Anything
# unset leaves the row (and the admin) in charge.
HIRSCHAI_ENV = {
    key: value
    for key, value in {
        "url": os.getenv("HIRSCHAI_URL"),
        "api_key": os.getenv("HIRSCHAI_API_KEY"),
        "model": os.getenv("HIRSCHAI_MODEL"),
        "embed_model": os.getenv("HIRSCHAI_EMBED_MODEL"),
        "timeout": env_int("LLM_TIMEOUT", 0),
    }.items()
    if value
}

# A visitor is waiting: past this, the portfolio shows its standard selection instead.
PORTFOLIO_RANK_TIMEOUT_S = env_int("PORTFOLIO_RANK_TIMEOUT_S", 8)

LLM_LOGGING = True
# Prompt + response bodies carry career data, postings and the personality dossier. Kept in dev
# for debugging; in prod a log row is metadata only (provider, model, tokens, latency, error) —
# the privacy policy promises exactly that.
LLM_LOG_BODIES = env_bool("LLM_LOG_BODIES", DEBUG)
# Log rows are for cost and abuse monitoring; the housekeeping job drops them after this long.
LLM_LOG_RETENTION_DAYS = env_int("LLM_LOG_RETENTION_DAYS", 90)

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
CORS_ALLOWED_ORIGIN_REGEXES = [r"^http://[a-z0-9-]+\.localhost:5173$"] if DEBUG else []
CORS_ALLOW_CREDENTIALS = True

# CSRF — trust the frontend origin so the CSRF middleware accepts its requests
CSRF_TRUSTED_ORIGINS = env_list(
    "CSRF_TRUSTED_ORIGINS",
    [FRONTEND_URL, f"https://{BASE_DOMAIN}", f"https://*.{BASE_DOMAIN}"],
)

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "lukehirsch.parsers.ObjectJSONParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Throttle identity = REMOTE_ADDR, never X-Forwarded-For. In prod daphne sets REMOTE_ADDR from
    # nginx's X-Real-IP, which a client can't forge. DRF's default (NUM_PROXIES=None) keys the
    # bucket on the raw XFF string instead, so a visitor could dodge the portfolio throttles by
    # sending their own header. Not the 1 a bare Django behind one proxy would use: allauth keys
    # on REMOTE_ADDR too, so that's the one place the real address has to land.
    "NUM_PROXIES": 0,
    "DEFAULT_THROTTLE_RATES": {
        "llm-chat": os.getenv("LLM_CHAT_RATE", "20/min"),
        "llm-chat-daily": os.getenv("LLM_CHAT_DAILY_RATE", "100/day"),
        "portfolio": os.getenv("PORTFOLIO_RATE", "60/hour"),
        "portfolio-rank": os.getenv("PORTFOLIO_RANK_RATE", "6/hour"),
        "portfolio-intro": os.getenv("PORTFOLIO_INTRO_RATE", "6/hour"),
        # A page polling for its intro: one request every few seconds, for a few minutes.
        "portfolio-poll": os.getenv("PORTFOLIO_POLL_RATE", "600/hour"),
        "generation": os.getenv("GENERATION_RATE", "20/day"),
        # The editor's AI tools + dossier rebuilds: free compute on an open-signup site.
        "ai-job": os.getenv("AI_JOB_RATE", "60/hour"),
        # Only a user who keeps emptying their career can get past the 409 to the writes.
        "sample-career": "20/hour",
        # Anything that puts a file on disk, per account.
        "uploads": os.getenv("UPLOAD_RATE", "100/day"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "lukehirsch API",
    "DESCRIPTION": "JAC career DB + LLM connector + portfolio API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    # Off: its check turns every APIView without a serializer into a `check --deploy` warning.
    # Schema polish, not deploy health, and the schema still generates at /api/schema/.
    "ENABLE_DJANGO_DEPLOY_CHECK": False,
}

# ─────────────────────────────────────────────────────────────────────────────
# Auth: allauth & MFA
# ─────────────────────────────────────────────────────────────────────────────
# allauth account settings
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True
# Two new codes per signup. Past that the verify page's way out is "Start over".
ACCOUNT_EMAIL_VERIFICATION_SUPPORTS_RESEND = True
# Each of these sends mail to an address the caller names, through the site's SMTP. Allauth keeps
# one history per action + ip/user/key, whatever the window: a second rate with the same `per` prunes
# the first one's timestamps away, so "5/m/key,5/d/key" is no daily limit. One rate per `per`.
ACCOUNT_RATE_LIMITS = {
    "reset_password": "20/m/ip,5/d/key",
    # Verification codes per address, signup and add-email alike. Default: 1/10s/key, 8,640 a day.
    "confirm_email": "5/d/key",
    # Add, resend, remove, make primary. Default: 10/m/user, 14,400 a day.
    "manage_email": "20/d/user",
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
# In prod every mail goes through jac-mail: a slow or down SMTP server must not hold up a signup,
# and the task retries. The worker then sends over QUEUED_EMAIL_BACKEND.
EMAIL_BACKEND = (
    "lukehirsch.debug_email_backends.ReadableConsoleEmailBackend"
    if DEBUG
    else "lukehirsch.mail.QueuedEmailBackend"
)
QUEUED_EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
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
# Imprint / privacy contact. Blank = the operator's (PORTFOLIO_OWNER_USERNAME) login email.
LEGAL_CONTACT_EMAIL = os.getenv("LEGAL_CONTACT_EMAIL", "")

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
# Logging
# ─────────────────────────────────────────────────────────────────────────────
# stdout only; docker collects it. Without this, Django's default config sends request errors
# (every 500 traceback) to mail_admins — and ADMINS is empty, so they vanished.
LOG_LEVEL = os.getenv("LOG_LEVEL", "WARNING" if DEBUG else "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "plain",
        },
    },
    "root": {"handlers": ["stdout"], "level": LOG_LEVEL},
    "loggers": {
        # Replaces Django's default "django" logger (console only under DEBUG + mail_admins).
        # ERROR, not WARNING: django.request logs every 4xx as a warning — noise in prod (the
        # proxy has the access log) and a wall of "Not Found" lines across the test dots.
        "django": {"handlers": ["stdout"], "level": "ERROR", "propagate": False},
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────
# Adds `--skip <group>` to `manage.py test`; see lukehirsch/test_runner.py.
TEST_RUNNER = "lukehirsch.test_runner.SkipGroupRunner"

# ─────────────────────────────────────────────────────────────────────────────
# Production guard — must run last
# ─────────────────────────────────────────────────────────────────────────────
verify_production_secrets(
    debug=DEBUG,
    secret_key=SECRET_KEY,
    encryption_key=LLM_ENCRYPTION_KEY,
    base_domain=BASE_DOMAIN,
    database_url=DATABASE_URL,
)
