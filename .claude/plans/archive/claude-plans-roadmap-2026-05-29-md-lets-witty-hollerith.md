# Phase 2 — User auth + MFA (allauth headless)

## Context

The roadmap's Phase 2 was scoped as "2FA only" on the assumption auth flows would come later with DRF. Revising scope: this phase ships **all user-facing auth** (signup, login, logout, password reset, password change, email verification, profile email, MFA enroll + challenge + recovery codes) so the first thing a real user can do on the SPA is create an account and log in.

Visitor access to personalized portfolio views is explicitly **out of scope** for this phase — that's a separate token-lookup mechanism with no user account involved, and lands later alongside `backend/spa/` work.

Decisions locked with the user:

- **D1 (auth surface):** django-allauth in **headless mode** + React forms. Single SPA UI; sessions via cookie.
- **D2 (visitor auth):** Pure token-based via `PortfolioLink`. Not part of this phase.
- **D3 (MFA library):** django-allauth's built-in `mfa` app (TOTP + recovery codes; passkeys later).

Same-origin Vite dev proxy keeps cookies/CSRF simple — no token auth, no CORS gymnastics.

---

## Backend implementation

### Dependencies

Add to `requirements.txt`:

- `django-allauth[mfa]>=65.0` — auth + MFA. Pulls in `qrcode`, `pyotp`, `fido2` transitively.

`rest_framework`, `corsheaders`, `channels`, `daphne` are already installed ([backend/lukehirsch/settings.py:25-28](backend/lukehirsch/settings.py#L25-L28)); no changes needed there.

### Settings — [backend/lukehirsch/settings.py](backend/lukehirsch/settings.py)

Add to `INSTALLED_APPS` (after existing third-party block):

```
"allauth",
"allauth.account",
"allauth.headless",
"allauth.mfa",
```

Add to `MIDDLEWARE` (after `AuthenticationMiddleware`):

```
"allauth.account.middleware.AccountMiddleware",
```

Append `AUTHENTICATION_BACKENDS`:

```python
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",      # admin login
    "allauth.account.auth_backends.AuthenticationBackend",
]
```

allauth config block:

```python
ACCOUNT_LOGIN_METHODS = {"email"}                # email-as-username; no separate username
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_RATE_LIMITS = {"login_failed": "5/5m"}   # default-ish; tune later

MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]   # passkeys deferred
MFA_TOTP_ISSUER = "lukehirsch"

HEADLESS_ONLY = True
HEADLESS_FRONTEND_URLS = {
    "account_confirm_email": f"http://localhost:5173/auth/verify-email/{key}",
    "account_reset_password": "http://localhost:5173/auth/reset-password",
    "account_reset_password_from_key": f"http://localhost:5173/auth/reset-password/{key}",
    "account_signup": "http://localhost:5173/auth/signup",
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "noreply@localhost")
# Real SMTP deferred to Phase 7 (deployment).

SITE_ID = 1   # allauth requires the sites framework
```

Also add `"django.contrib.sites"` to `INSTALLED_APPS`.

### URLs — [backend/lukehirsch/urls.py](backend/lukehirsch/urls.py)

```python
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("_allauth/", include("allauth.headless.urls")),
]
```

The headless URL set is namespaced and stable. Key endpoints land at:

- `/_allauth/browser/v1/auth/signup`
- `/_allauth/browser/v1/auth/login`
- `/_allauth/browser/v1/auth/session` (whoami + logout)
- `/_allauth/browser/v1/auth/password/request` + `.../password/reset`
- `/_allauth/browser/v1/account/password/change`
- `/_allauth/browser/v1/account/email` (manage email addresses, resend verify)
- `/_allauth/browser/v1/auth/2fa/authenticate` (challenge step)
- `/_allauth/browser/v1/account/authenticators/totp` (enroll)
- `/_allauth/browser/v1/account/authenticators/recovery-codes`

### Admin MFA gate

`allauth.mfa` does **not** automatically force MFA on Django's admin login (admin uses its own LoginView). Add a small middleware so that any `is_staff` user who has TOTP enrolled must have completed MFA in this session before hitting `/admin/`:

New file `backend/lukehirsch/middleware.py`:

```python
from allauth.mfa import app_settings as mfa_settings
from allauth.mfa.utils import is_mfa_enabled
from django.http import HttpResponseRedirect

class AdminRequireMfaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.path.startswith("/admin/")
            and request.user.is_authenticated
            and request.user.is_staff
            and is_mfa_enabled(request.user)
            and not request.session.get("mfa_authenticated")
        ):
            return HttpResponseRedirect("/auth/mfa-challenge?next=" + request.path)
        return self.get_response(request)
```

Register it in `MIDDLEWARE` after `AccountMiddleware`. Set `request.session['mfa_authenticated'] = True` on successful MFA via an allauth signal handler in `backend/lukehirsch/signals.py`. (Cleaner alternative for later: switch admin's login to allauth's flow entirely; not worth the complexity now.)

### Migrations + smoke

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
# manual curl: GET /_allauth/browser/v1/auth/session → 401 with CSRF token cookie set
```

### Tests — `backend/lukehirsch/tests/test_auth.py` (new)

Use Django's test client against the headless endpoints. Cover:

- Signup with email → email-verification email in `mail.outbox` → confirm via API → login succeeds
- Login with wrong password → 400; rate limit after 5
- Password reset request → email → confirm → login with new password
- Password change while authenticated
- TOTP enroll: returns secret + QR data; verify with `pyotp.TOTP(secret).now()` → enrolled
- Login after enrollment: returns "mfa challenge required" → POST TOTP code → session fully authenticated
- Recovery code usage: consume one → remaining count decremented
- Admin MFA gate: staff user with MFA enrolled, hit `/admin/` after login but before MFA → redirected

Existing tests (130) should keep passing; add ~15 here.

---

## Frontend implementation

### Scaffolding (new for this phase — pulls forward Phase 4 plumbing)

Add to [frontend/package.json](frontend/package.json):

- `react-router-dom@^7`
- `@tanstack/react-query@^5`

Vite proxy in [frontend/vite.config.ts](frontend/vite.config.ts) so `/api` and `/_allauth` go to Django on `:8000`. Same-origin means session cookies and CSRF Just Work:

```ts
server: {
  proxy: {
    "/_allauth": "http://localhost:8000",
    "/admin":    "http://localhost:8000",
  },
},
```

### Fetch wrapper — `frontend/src/lib/api.ts`

One small helper that:

- Reads `csrftoken` cookie, sets `X-CSRFToken` header on unsafe methods
- Sets `credentials: "same-origin"` always
- Throws on non-2xx with parsed JSON error body
- No tokens, no headers other than CSRF + content-type

### Auth state — `frontend/src/lib/auth.tsx`

A small React context backed by a TanStack Query for `GET /_allauth/browser/v1/auth/session`. Exposes `useAuth()` returning `{ user, status, login, logout, ... }`. `status` is one of `anonymous | authenticated | mfa_required`. Mutations invalidate the session query.

### Routing — `frontend/src/App.tsx`

```
/                       → placeholder home (logged-in dashboard stub OR landing)
/auth/signup            → SignupForm
/auth/login             → LoginForm
/auth/verify-email/:key → VerifyEmail (auto-submits key)
/auth/reset-password    → RequestReset
/auth/reset-password/:key → ConfirmReset
/auth/mfa-challenge     → MfaChallengeForm
/account                → ProfilePage (email mgmt, change password, MFA setup)
```

A `<RequireAuth>` wrapper redirects to `/auth/login` when status is `anonymous`, and to `/auth/mfa-challenge` when `mfa_required`.

### Forms — `frontend/src/pages/auth/*.tsx`

One file per form. Minimal styling — these are functional pages, not the portfolio UI. Errors come from allauth's structured error responses; render inline per field. For MFA setup: render the QR code from the `totp_url` allauth returns (use `qrcode.react`).

### Out of scope for Phase 2 frontend

- Career-model CRUD pages (Phase 4)
- LLMConfig management UI (Phase 4)
- Visual design / branding (Phase 4 / later)
- Public landing + personalized views (later, with `spa` app)

---

## Files touched

**Modified:**

- [backend/lukehirsch/settings.py](backend/lukehirsch/settings.py) — INSTALLED_APPS, MIDDLEWARE, allauth config, email backend
- [backend/lukehirsch/urls.py](backend/lukehirsch/urls.py) — mount `_allauth/`
- [frontend/package.json](frontend/package.json) — router + query client
- [frontend/vite.config.ts](frontend/vite.config.ts) — dev proxy
- [frontend/src/App.tsx](frontend/src/App.tsx) — router + auth provider
- [requirements.txt](requirements.txt) — `django-allauth[mfa]`

**New:**

- `backend/lukehirsch/middleware.py` — `AdminRequireMfaMiddleware`
- `backend/lukehirsch/signals.py` — mark session as MFA-authenticated
- `backend/lukehirsch/tests/test_auth.py`
- `frontend/src/lib/api.ts`
- `frontend/src/lib/auth.tsx`
- `frontend/src/pages/auth/{SignupForm,LoginForm,VerifyEmail,RequestReset,ConfirmReset,MfaChallenge}.tsx`
- `frontend/src/pages/account/Profile.tsx`
- `frontend/src/components/RequireAuth.tsx`

---

## Verification

End-to-end manual flow, on a fresh DB:

1. `python manage.py migrate && python manage.py runserver`
2. `npm run dev` (frontend)
3. Visit `http://localhost:5173/auth/signup` → submit email + password → see verification email in Django console
4. Click link from console → land on `/auth/verify-email/:key` → confirmed
5. `/auth/login` → land on home (logged in)
6. `/account` → enroll TOTP → scan QR with phone authenticator → confirm code → recovery codes displayed (download/print)
7. Log out → log in again → MFA challenge appears → enter TOTP code → home
8. As a `is_staff` user with MFA enrolled, hit `/admin/` directly after login-without-MFA → redirected to MFA challenge → after challenge, admin loads
9. `/auth/reset-password` → email arrives → confirm via link → log in with new password

Automated:

```bash
cd backend && python manage.py test
```

Expect existing 130 to still pass + ~15 new auth tests.

---

## Known follow-ups (not blocking phase completion)

- **Passkeys / WebAuthn** — add `"webauthn"` to `MFA_SUPPORTED_TYPES`, surface enrollment in profile page. Defer until base flow is stable.
- **Real SMTP** — `EMAIL_BACKEND` flip to `smtp` + creds in `.env`; lives in Phase 7 (deployment).
- **Trusted devices / "remember this browser"** — `MFA_TRUSTED_DEVICES`; add later if MFA prompts become annoying.
- **Switch admin login to allauth entirely** — cleaner than the middleware shim but invasive; revisit if a second admin-style portal appears.
- **Rate limit tuning** — `ACCOUNT_RATE_LIMITS` defaults are conservative; revisit after some real usage.
