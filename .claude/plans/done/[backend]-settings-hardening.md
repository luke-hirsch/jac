# [backend] Settings hardening — env parsing, secret enforcement, safe DRF default

> **Branch:** `backend/bugfixes` (shared by all four hardening guides). Land the tests red, type the
> code, watch them go green. Merge the whole branch back once all four are done.

## Context / goal

Three latent production-security bugs live in `lukehirsch/settings.py`, all stemming from the same
root cause: **env vars are strings, but the code treats them as Python values.**

1. `DEBUG = os.getenv("DEBUG", True)` (line 10). Setting `DEBUG=False` in the environment yields the
   **string** `"False"`, which is truthy. So `DEBUG` can never be turned off from the environment.
   Everything keyed off it silently stays in dev mode in production: `SESSION_COOKIE_SECURE` /
   `CSRF_COOKIE_SECURE` off, the **SQLite branch** of `DATABASES` instead of Postgres, locmem cache
   instead of Redis, the console email backend, `MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN=True`, and
   Django's settings-dumping debug error pages.
2. `LLM_TIMEOUT` / `LLM_THINKING` (lines 180–181) have the same string bug: `urlopen(timeout="300")`
   is a `TypeError`, and `think="False"` is truthy.
3. `SECRET_KEY` and `LLM_ENCRYPTION_KEY` (lines 6, 189) fall back to **dev values checked into git**.
   Nothing forces production to override them — a missing env var means forgeable sessions and
   decryptable "encrypted" API keys, with no warning.

Plus one footgun: DRF's global default is `AllowAny` (line 211). Every viewset re-declares
`IsAuthenticated`, so nothing is exposed _today_, but the next endpoint that forgets the declaration
is public by default.

Goal: parse env booleans/ints correctly, **fail fast on boot** if production is running on the dev
secrets, and make the DRF default deny-by-default. This is pure hardening — no runtime behaviour
changes when the env is set correctly.

> **Posture (corrected 2026-07-08):** this project is a **public portfolio site** whose private
> section (jac) may itself open up as a public showcase later — it is *not* a private tool. That
> does **not** change the deny-by-default flip: on a public site the rule is that public endpoints
> **opt in explicitly** with `AllowAny` instead of being public by omission. What it *does* change:
> the flip has one real casualty this guide originally missed — `IndexView` in `lukehirsch/urls.py`
> is a DRF `APIView` with **no** `permission_classes` (the original note below claiming the public
> bits "set their own perms" was wrong for it), so anonymous `GET /` 403s after step 2g. Step 2i
> fixes it. `/api/schema/` + `/api/docs/` are unaffected — drf-spectacular serves them under its own
> `SERVE_PERMISSIONS` default (`AllowAny`), which we keep deliberately: public API docs are part of
> the showcase.

## Affected files

| path                                           | why                                                                                                                                                                                      |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/lukehirsch/prod.py`                   | **new** — `env_bool` / `env_int` parsers + `verify_production_secrets` guard + the dev-default constants *(landed as `prod.py`, not the guide's original `env.py`)*                      |
| `backend/lukehirsch/settings.py`               | use the parsers for `DEBUG` / `LLM_TIMEOUT` / `LLM_THINKING` / `EMAIL_USE_TLS`; reference the dev-default constants; call the guard at the bottom; flip DRF default to `IsAuthenticated` |
| `backend/lukehirsch/urls.py`                   | give the public `IndexView` an explicit `AllowAny` — the deny-by-default flip would otherwise 403 anonymous visitors on `GET /` (public portfolio!)                                      |
| `backend/spa/tests/test_settings_hardening.py` | **new (test)** — parser + guard unit tests + a live assertion that the DRF default is `IsAuthenticated` + anonymous `GET /` and `GET /api/schema/` stay public                           |

> Test home: `spa` is the closest thing to a site/core app (it already holds cross-cutting auth
> tests), and `lukehirsch` is the project package, not an installed app, so its own `tests/` would
> never be collected by `manage.py test`. Tests import `lukehirsch.env` directly — that's fine.

## The code

### 1. New file — `backend/lukehirsch/prod.py`

```python
"""Environment parsing + a boot-time production-secret guard.

Env vars are always strings; `os.getenv("DEBUG", True)` returns the string "False" for
`DEBUG=False`, which is truthy — the exact bug this module exists to prevent. Use `env_bool`
/ `env_int` for every boolean/int setting sourced from the environment.
"""

import os

from django.core.exceptions import ImproperlyConfigured

# The insecure placeholders shipped in settings.py for local dev. Production MUST override both;
# `verify_production_secrets` refuses to boot with DEBUG off while either is still in place.
DEV_SECRET_KEY = "django-insecure-g4pj@dk!pf+e#+8^5t-ic(avl(ng9e=@3%ziwrls-!5sq%y6s5"
DEV_ENCRYPTION_KEY = "ZGphbmdvLWluc2VjdXJlLWxsbS1kZXYta2V5LW9ubHk="

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var. Accepts 1/true/yes/on and 0/false/no/off (case-insensitive).

    Missing var -> `default`. An already-boolean default is returned as-is when unset, so
    `env_bool("DEBUG", True)` keeps working. An unrecognised value falls back to `default`.
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    val = raw.strip().lower()
    if val in _TRUE:
        return True
    if val in _FALSE:
        return False
    return default


def env_int(name: str, default: int) -> int:
    """Parse an int env var. Missing or non-integer -> `default`."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def verify_production_secrets(*, debug: bool, secret_key: str, encryption_key: str) -> None:
    """Raise ImproperlyConfigured if running with DEBUG off on a checked-in dev secret.

    No-op in DEBUG (local dev is allowed to use the placeholders). Called once at the end of
    settings.py so a misconfigured production deploy fails fast on boot instead of silently
    serving forgeable sessions / decryptable API keys.
    """
    if debug:
        return
    offenders = []
    if secret_key == DEV_SECRET_KEY:
        offenders.append("SECRET_KEY")
    if encryption_key == DEV_ENCRYPTION_KEY:
        offenders.append("LLM_ENCRYPTION_KEY")
    if offenders:
        raise ImproperlyConfigured(
            "Refusing to start with DEBUG off while these secrets are still the "
            f"checked-in dev defaults: {', '.join(offenders)}. Set them from the "
            "environment (SECRET_KEY, LLM_ENCRYPTION_KEY)."
        )
```

### 2. Edit `backend/lukehirsch/settings.py`

**a. Import the helpers** — add near the top, under the existing `import os` (line 1):

```python
from lukehirsch.prod import (
    DEV_ENCRYPTION_KEY,
    DEV_SECRET_KEY,
    env_bool,
    env_int,
    verify_production_secrets,
)
```

**b. `SECRET_KEY`** (lines 6–8) — reference the constant so there's a single source of truth:

```python
SECRET_KEY = os.getenv("SECRET_KEY", DEV_SECRET_KEY)
```

**c. `DEBUG`** (line 10):

```python
DEBUG = env_bool("DEBUG", True)
```

**d. LLM block** (lines 180–181) — parse the int and bool:

```python
        "timeout": env_int("LLM_TIMEOUT", 300),
        "think": env_bool("LLM_THINKING", False),
```

**e. `LLM_ENCRYPTION_KEY`** (lines 189–192) — reference the constant:

```python
LLM_ENCRYPTION_KEY = os.getenv("LLM_ENCRYPTION_KEY", DEV_ENCRYPTION_KEY)
```

**f. `EMAIL_USE_TLS`** (line 267) — fold onto the same helper for consistency:

```python
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
```

**g. DRF default permission** (lines 210–212) — deny by default:

```python
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
```

> Every existing viewset already sets `permission_classes` explicitly, so this changes nothing for
> them. ~~The public bits (`IndexView`, the allauth/schema URLs) are DRF-external or set their own
> perms~~ — **correction:** `IndexView` is a DRF `APIView` with *no* `permission_classes`; it rode on
> the `AllowAny` default and 403s after this flip. Step 2i fixes it. The allauth URLs are
> DRF-external and the schema/docs views carry drf-spectacular's own `SERVE_PERMISSIONS` default
> (`AllowAny`), so those two really are unaffected.

**h. The guard** — add at the very **bottom** of the file, after every setting it inspects exists:

```python
# Fail fast if a production deploy is still running on the checked-in dev secrets.
verify_production_secrets(
    debug=DEBUG,
    secret_key=SECRET_KEY,
    encryption_key=LLM_ENCRYPTION_KEY,
)
```

**i. `backend/lukehirsch/urls.py` — keep the public root public.** The portfolio is a public site;
`GET /` is hit by anonymous visitors. With the default now `IsAuthenticated`, the index must opt in
explicitly:

```python
from rest_framework.permissions import AllowAny
```

```python
class IndexView(APIView):
    # The site root is public (portfolio visitors land here anonymously); the global DRF
    # default is IsAuthenticated, so public endpoints must opt in explicitly.
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"message": "I am alive!"})
```

> This is the pattern for every future public endpoint (per-visitor portfolio rendering, the
> eventual public CV-tool showcase): explicit `AllowAny`, never public-by-omission.

## Tests

`backend/spa/tests/test_settings_hardening.py` (written to disk on this branch, starts **red**):

- `EnvBoolTests` — `"False"`/`"0"`/`"off"` → `False`; `"true"`/`"1"` → `True`; unset → default;
  unrecognised → default. This is the core regression for the `DEBUG` bug.
- `EnvIntTests` — `"300"` → `300`; unset → default; non-integer → default.
- `VerifyProductionSecretsTests` — raises when `debug=False` on a dev default; silent in `debug=True`;
  silent when both secrets are overridden.
- `DrfDefaultPermissionTests` — `settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]` is exactly
  `["rest_framework.permissions.IsAuthenticated"]` (red until step 2g); anonymous `GET /` returns
  `200` with the alive message (red between 2g and 2i — guards the public root against the flip);
  anonymous `GET /api/schema/` returns `200` (documents that the schema is deliberately public).

Run:

```bash
cd backend && python manage.py test spa.tests.test_settings_hardening
```

## Verification

```bash
cd backend
python manage.py test spa.tests.test_settings_hardening      # all green
python manage.py test                                        # full suite still green

# DEBUG now actually parses:
DEBUG=False python -c "import os,django; os.environ.setdefault('DJANGO_SETTINGS_MODULE','lukehirsch.settings'); \
  SECRET_KEY='x' LLM_ENCRYPTION_KEY='y' django.setup()" 2>&1 | head
```

- With `DEBUG=False` and the dev secrets still in place, boot must **fail** with the
  `ImproperlyConfigured` message naming `SECRET_KEY, LLM_ENCRYPTION_KEY`.
- With `DEBUG=False` **and** both secrets set from env, boot succeeds and (confirm) `settings.DEBUG`
  is the real boolean `False`, `settings.DATABASES["default"]["ENGINE"]` is postgres.
- `python manage.py runserver` in normal dev (DEBUG unset → True) still starts; `GET /` returns
  `{"message": "I am alive!"}` and the login flow works (DRF default flip didn't lock anything out).

**Done looks like:** `DEBUG=False` genuinely disables debug mode, `LLM_TIMEOUT`/`LLM_THINKING` are
real int/bool, a production boot on dev secrets is impossible, and forgetting `permission_classes`
on a future viewset fails closed.
