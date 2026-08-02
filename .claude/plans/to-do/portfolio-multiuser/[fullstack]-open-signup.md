# [fullstack] Open signup + handle claim + disclosure + soft run cap

**Branch:** `fullstack/open-signup` (off `main`).
**Phase:** portfolio-multiuser (guide 4 of 4). Depends on guides 1–3.

## Context / goal

The live demo only works if a recruiter can actually **sign up and get a working portfolio**.
This guide flips the launch toggle, lets a user **claim/customize their handle** (their public
subdomain) with reserved-name + uniqueness guards, adds the "you're building on Lukas's
domain — self-host any time" disclosure, and puts a **soft, tunable cap** on generations so a
stranger can't hammer HirschAI's (Lukas's laptop) ollama. Per Lukas: abuse protection is
deliberately light — a generous per-user daily cap, not a fortress ("if the power bill gets
too big, I shut it down").

Public-site posture ([[public-site-posture]]): opening signup makes every authed surface
internet-facing by intent — the deny-by-default DRF permission + mandatory email verification
already hold; this guide only opens the front door and adds the handle guard.

## Affected files

| File | Change |
| --- | --- |
| env / `settings.py` | `ACCOUNT_ALLOW_SIGNUPS=True` (prod env); add a `generation` throttle rate. |
| `backend/spa/serializers.py` | `UserProfileSerializer` exposes a writable `handle` + `validate_handle`. |
| `backend/jac/views.py` | `GenerationRunViewSet` throttles `create` with the `generation` scope. |
| `frontend/src/routes/account/profile.tsx` | Handle field + `<handle>.<domain>` live preview + disclosure. |
| `frontend/src/routes/auth/signup.tsx` | Disclosure copy ("you're building on Lukas's domain; open source; self-host any time"). |
| `backend/spa/tests/test_portfolio.py` | (AI-written, red) `HandleClaimTests`. |

---

## The code

### 1. Open signup

The gate already exists — `HarassmentResistantAccountAdapter.is_open_for_signup` reads
`ACCOUNT_ALLOW_SIGNUPS` (`adapter.py:22`), wired to `env_bool("ACCOUNT_ALLOW_SIGNUPS", False)`
(`settings.py:254`). **Opening signup is an env change**, no code: set
`ACCOUNT_ALLOW_SIGNUPS=true` in the prod/stage env. Keep it `False` in dev unless testing the
flow.

(Optional UX polish, not required: the frontend can pre-check allauth's headless config at
`/_allauth/browser/v1/config` — its `account.signup` fields tell you whether signup is open —
to hide the form when closed instead of failing the POST. Skip for now; a closed signup
already returns a clean 403 the signup page can show.)

### 2. Generation soft cap

Add a rate (env-tunable so Lukas can loosen/tighten without a deploy) to
`DEFAULT_THROTTLE_RATES` (`settings.py:231-236`):

```python
    "DEFAULT_THROTTLE_RATES": {
        "llm-chat": "20/min",
        "portfolio": "60/hour",
        "portfolio-rank": "6/hour",
        "portfolio-intro": "6/hour",
        "generation": os.getenv("GENERATION_RATE", "20/day"),
    },
```

Throttle **only** the create action on `GenerationRunViewSet` (`jac/views.py:621`), leaving
list/retrieve/cancel unthrottled. Override `get_throttles`:

```python
    from rest_framework.throttling import ScopedRateThrottle

    def get_throttles(self):
        if self.action == "create":
            self.throttle_scope = "generation"
            return [ScopedRateThrottle()]
        return super().get_throttles()
```

> The viewset already carries a base `throttle_scope = None` (`:374`) so scoped throttling is
> legal here. Per-user (session-authed) throttling keys on the user, so the cap is per
> account, exactly the stranger-on-ollama lever. `20/day` is generous; drop `GENERATION_RATE`
> to taste.

### 3. Handle claim + validation

Expose `handle` on `UserProfileSerializer` (`serializers.py:36`). Add `"handle"` to `fields`
(NOT read-only), and a validator:

```python
    def validate_handle(self, value):
        from django.conf import settings
        from django.utils.text import slugify

        handle = slugify(value)[:40].strip("-")
        if len(handle) < 2:
            raise serializers.ValidationError("Handle must be at least 2 characters.")
        if handle in settings.RESERVED_SUBDOMAINS:
            raise serializers.ValidationError("That handle is reserved.")
        clash = UserProfile.objects.filter(handle__iexact=handle)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("That handle is already taken.")
        return handle
```

`UserProfile` is already imported in `serializers.py` (`:8-14`). The handle is slug-shaped, so
the returned normalized value is what's stored — the user types `Jane Doe`, gets `jane-doe`.

### 4. Frontend — the handle field + disclosure

In `routes/account/profile.tsx`, add a handle input bound to the profile mutation, with a live
preview and the disclosure. Sketch (adapt to the page's existing form/field helpers):

```tsx
import { currentHandle } from "@/lib/host";

// inside the form:
<label className="block space-y-1">
  <span className="text-sm font-medium">Portfolio handle</span>
  <input
    value={form.handle}
    onChange={(e) => setForm({ ...form, handle: e.target.value })}
    className="…"
  />
  <p className="text-xs text-muted-foreground">
    Your portfolio: <code>{form.handle || "your-handle"}.{import.meta.env.VITE_BASE_DOMAIN}</code>
  </p>
  <p className="text-xs text-muted-foreground">
    You're building on Lukas's domain. jac is open source — you can self-host or move to
    your own domain any time.
  </p>
</label>
```

Show the server's `validate_handle` error inline (the profile mutation already surfaces field
errors). `import.meta.env.VITE_BASE_DOMAIN` comes from guide 2.

In `routes/auth/signup.tsx`, add the same one-line disclosure near the submit button so the
recruiter knows what they're getting:

```tsx
<p className="text-xs text-muted-foreground">
  Signing up creates a portfolio at <code>you.{import.meta.env.VITE_BASE_DOMAIN}</code>,
  hosted on Lukas's domain. Open source — self-host any time.
</p>
```

---

## Tests

Written to disk (red until this guide lands): **`backend/spa/tests/test_portfolio.py`** gains
`HandleClaimTests` (auth'd PATCH `/api/spa/portfolio/... profile` — actually the profile
endpoint `/api/spa/profile/`):

- claiming a free handle normalizes + saves it (`Jane Doe` → `jane-doe`);
- a reserved handle (`app`) is rejected 400;
- a handle already held by another user is rejected 400;
- a too-short handle is rejected 400.

The generation throttle is integration-shaped (needs Celery mocked + repeated POSTs); given
Lukas's "don't care much" stance, a test isn't written for it — flagged per the skill. Verify
it by hand (step 4 below).

Run: `cd backend && python manage.py test spa.tests.test_portfolio`

## Verification

1. Set `ACCOUNT_ALLOW_SIGNUPS=true`; sign up a fresh account at `app.<domain>/auth/signup`;
   verify email (dev console backend). The new user has an auto-minted handle
   (`spa.signals` → `mint_handle`).
2. In `app.<domain>/account/profile`, change the handle to something free → the preview
   updates; visit `<new-handle>.<domain>/` → the new user's (empty) questionnaire. Add a
   career entry, revisit → it shows. **A recruiter can now build a live portfolio.**
3. Try to claim `app` or an existing user's handle → inline "reserved" / "taken" error.
4. Fire `POST /api/jac/generations/` more than `GENERATION_RATE` times in a day (lower
   `GENERATION_RATE=2` temporarily) → the 21st (or 3rd) returns 429.
5. `python manage.py test spa` — green.

## Results

_(human fills after testing)_
