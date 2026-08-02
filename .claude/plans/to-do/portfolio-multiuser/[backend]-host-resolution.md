# [backend] Host-based owner resolution + handle + per-user slugs + BASE_DOMAIN

**Branch:** `backend/host-resolution` (off `main`).
**Phase:** portfolio-multiuser (guide 1 of 4). Guides 2–4: `[infra]-subdomain-hosting`,
`[frontend]-host-aware-routing`, `[fullstack]-open-signup`.

> This phase supersedes the single-owner direction of `to-do/portfolio-rework/`. That
> rework's **backend already landed** (commit `a9639a9`: `owner_domains`, `section_order`,
> `build_intro`, `is_default`, the `username__iexact` fix). This guide **transforms** that
> single-owner backend into a multi-user one. Don't re-implement the pieces that exist —
> only the deltas below.

## Context / goal

`luke-hirsch.de` must host a **working** portfolio for every user, not just Lukas, so a
recruiter can watch it work and then build their own — live. The data model is already
per-user; the two things pinned to one owner are (a) `get_owner()` reading a fixed setting
and (b) the public URL grammar. This guide moves owner resolution to the **request host**
(`<handle>.<BASE_DOMAIN>`), makes the base domain a **single env knob** so a later move to a
neutral domain is config-only, gives each user an editable **`handle`**, and makes portfolio
slugs **per-user unique + descriptive/editable** (the host disambiguates users, so global
uniqueness and entropy suffixes are no longer needed).

Roadmap: this is the active reshaping of roadmap item #1 (portfolio generator).

## Design in one screen

- **Hosts** (prod): apex `luke-hirsch.de` = the configured owner (Lukas, the SEO landing);
  `app.luke-hirsch.de` = the authed tool; `<handle>.luke-hirsch.de` = that user's public
  portfolio. Resolution is a pure function of `request.get_host()`.
- **One knob:** `BASE_DOMAIN` (resolve side) + `PORTFOLIO_ORIGIN_TEMPLATE` (URL-build side).
  Moving to `jac.app` later = change these two env vars (+ DNS/TLS in guide 2). No code.
- **`handle`** lives on `UserProfile` (slug, globally unique — it's a subdomain). Auto-set
  from username on profile creation; editable later (guide 4 wires the claim UI).
- **Slugs** become per-user-unique among active links. `application_slug` becomes
  `<company>` / `<company>-<role>`, deduped per user, editable before the sent-freeze.
- **Legacy `/portfolio/<slug>`** (old QR target on the apex) → a Django 301 to the canonical
  `<handle>` origin. Insurance only — there are no real external QR codes yet.

## Affected files

| File                                  | Change                                                                                                                                                                  |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/lukehirsch/settings.py`      | Add `BASE_DOMAIN`, `RESERVED_SUBDOMAINS`, `PORTFOLIO_ORIGIN_TEMPLATE`. Keep `PORTFOLIO_OWNER_USERNAME` as the apex owner.                                               |
| `backend/spa/models.py`               | `UserProfile.handle` field; `PortfolioLink` slug → per-user-unique constraint (drop `unique=True`).                                                                     |
| `backend/spa/signals.py`              | Set `handle` when the profile is auto-created.                                                                                                                          |
| `backend/spa/migrations/0006_*.py`    | Schema + data migration: add `handle`, backfill existing profiles, swap the slug constraint.                                                                            |
| `backend/spa/portfolio.py`            | `owner_for_host` / `resolve_owner`; keep `_configured_owner`; `public_portfolio_url`; rewrite `application_slug` + a `_dedupe_slug` helper; `landing_context(request)`. |
| `backend/spa/views.py`                | Native/rank/meta/intro + resolve views resolve owner from `request`; resolve view scopes the slug to that owner.                                                        |
| `backend/spa/serializers.py`          | `get_url` → `public_portfolio_url`; per-user slug-uniqueness check in `validate`.                                                                                       |
| `backend/lukehirsch/urls.py`          | `landing(request)` passes the request; add the legacy `portfolio/<slug>/` 301 view.                                                                                     |
| `backend/spa/tests/test_portfolio.py` | (AI-written, red) new host-resolution + slug tests; existing endpoint classes get `BASE_DOMAIN="testserver"`.                                                           |

---

## The code

### 1. `settings.py` — the knobs

Add below `PORTFOLIO_OWNER_USERNAME` (currently the last line, `:295`):

```python
PORTFOLIO_OWNER_USERNAME = os.getenv("PORTFOLIO_OWNER_USERNAME", "lukas")

# Multi-user portfolio hosting. Owner is resolved from the request host:
#   <handle>.<BASE_DOMAIN>  -> that user's public portfolio
#   <BASE_DOMAIN> (apex)    -> PORTFOLIO_OWNER_USERNAME (the SEO landing owner)
# Moving to a neutral domain later = set these two env vars (+ DNS/TLS). No code change.
BASE_DOMAIN = os.getenv("BASE_DOMAIN", "localhost")

# Subdomains that are infrastructure, never a user handle. Guide 4 also blocks these at
# signup so a handle can never shadow a real host.
RESERVED_SUBDOMAINS = set(
    env_list(
        "RESERVED_SUBDOMAINS",
        ["app", "api", "www", "admin", "static", "media", "mail", "ns", "ns1", "ns2"],
    )
)

# How an owner-facing public URL (the QR target) is built from a handle. Dev points at the
# Vite host; prod at the wildcard origin. `{handle}` is the only placeholder.
PORTFOLIO_ORIGIN_TEMPLATE = os.getenv(
    "PORTFOLIO_ORIGIN_TEMPLATE", "http://{handle}.localhost:5173"
)
```

`env_list` is already imported from `lukehirsch.prod` (`:4-11`). Also extend `ALLOWED_HOSTS`
(currently `:19`) — but that's guide 2's job; leave it here for now.

### 2. `models.py` — the handle + per-user slug

On `UserProfile`, add under the identity block (after `bio`/`signature`, ~`:58`):

```python
    # Public portfolio handle — the subdomain (`<handle>.<BASE_DOMAIN>`). Globally unique
    # (it's a hostname). Auto-set from username on creation (spa/signals.py); editable in
    # the account UI (guide 4). Slug-shaped so it's always a valid DNS label.
    handle = models.SlugField(max_length=40, unique=True, blank=True)
```

On `PortfolioLink`, change the slug field (`:284`) from globally-unique to a plain field, and
swap the constraint set. Replace:

```python
    slug = models.SlugField(max_length=80, unique=True)
```

with:

```python
    slug = models.SlugField(max_length=80)  # unique PER USER (constraint below), not global
```

and add a third constraint to `Meta.constraints` (`:306`), so per-user active slugs are
unique while revoked history can repeat:

```python
        constraints = [
            models.UniqueConstraint(
                fields=["application"],
                condition=Q(revoked_at__isnull=True, application__isnull=False),
                name="one_active_link_per_application",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(is_default=True, revoked_at__isnull=True),
                name="one_default_link_per_user",
            ),
            models.UniqueConstraint(
                fields=["user", "slug"],
                condition=Q(revoked_at__isnull=True),
                name="unique_active_slug_per_user",
            ),
        ]
```

### 3. `signals.py` — set the handle at creation

Replace `_create_profile_on_user_creation` (`:16-20`):

```python
@receiver(post_save, sender=get_user_model())
def _create_profile_on_user_creation(sender, instance, created, **kwargs):
    if created:
        from spa.portfolio import mint_handle

        UserProfile.objects.create(user=instance, handle=mint_handle(instance.username))
        PersonalityProfile.objects.create(user=instance)
```

`mint_handle` lives in `portfolio.py` (below) — a slugified, reserved-aware, collision-safe
handle. Import inside the function to avoid an import cycle (signals load early).

### 4. `portfolio.py` — resolution, URL building, handle minting, slug dedup

Replace `get_owner()` (`:109-117`) with the host-aware trio, and keep a `_configured_owner`
for the apex:

```python
def _configured_owner() -> User | None:
    """The apex owner — the single configured user behind `BASE_DOMAIN` itself (the SEO
    landing, and the fallback for request-less callers). Case-insensitive; never the system
    sentinel; None when unset/unknown/inactive."""
    username = (settings.PORTFOLIO_OWNER_USERNAME or "").strip()
    if not username or username.lower() == settings.SYSTEM_USER_USERNAME.lower():
        return None
    return User.objects.filter(username__iexact=username, is_active=True).first()


def owner_for_host(host: str) -> User | None:
    """Resolve the portfolio owner from a raw Host header. `<handle>.<BASE_DOMAIN>` -> that
    user; the bare `BASE_DOMAIN` (or `www.`) -> the configured apex owner; a reserved or
    unknown subdomain / foreign host -> None (callers 404). Pure + unit-testable."""
    host = (host or "").split(":")[0].lower().rstrip(".")
    base = settings.BASE_DOMAIN.lower()
    if host in (base, f"www.{base}"):
        return _configured_owner()
    suffix = f".{base}"
    if not host.endswith(suffix):
        return None
    handle = host[: -len(suffix)]
    if not handle or "." in handle or handle in settings.RESERVED_SUBDOMAINS:
        return None
    return User.objects.filter(
        profile__handle__iexact=handle, is_active=True
    ).first()


def resolve_owner(request) -> User | None:
    """The public portfolio's owner for this request, from its Host header."""
    return owner_for_host(request.get_host())


def mint_handle(username: str) -> str:
    """A DNS-safe, reserved-aware, unique handle seeded from a username."""
    base = slugify(username)[:40].strip("-") or "user"
    if base in settings.RESERVED_SUBDOMAINS:
        base = f"{base}-1"
    candidate, n = base, 2
    while UserProfile.objects.filter(handle__iexact=candidate).exists():
        suffix = f"-{n}"
        candidate = f"{base[: 40 - len(suffix)]}{suffix}"
        n += 1
    return candidate


def public_portfolio_url(link) -> str:
    """The absolute public URL a QR encodes: the owner's origin + the link slug. Built from
    `PORTFOLIO_ORIGIN_TEMPLATE` so the domain lives in exactly one env var."""
    origin = settings.PORTFOLIO_ORIGIN_TEMPLATE.format(handle=link.user.profile.handle)
    return f"{origin.rstrip('/')}/{link.slug}"
```

Add the `UserProfile` import at the top of `portfolio.py` (`:24` currently imports
`PortfolioBlock, PortfolioLink, PortfolioVisit`):

```python
from spa.models import PortfolioBlock, PortfolioLink, PortfolioVisit, UserProfile
```

**`application_slug` — descriptive + per-user unique.** Replace `:36-52`:

```python
def _application_company(application) -> str:
    """Company preference: corrected letter recipient -> extracted posting address ->
    posting title -> fallback (unchanged from the entropy-slug era)."""
    address = getattr(application.posting, "address", None)
    return (
        ((application.letter_meta or {}).get("recipient") or {}).get("company")
        or (address.company if address else "")
        or application.posting.title
        or "application"
    )


def _dedupe_slug(user, base: str, extra: str = "") -> str:
    """First free slug among the user's ACTIVE links: `base`, then `base-extra`, then a
    numeric tail. Per-user now — the subdomain carries the user, so no cross-user entropy."""
    taken = set(
        PortfolioLink.objects.filter(user=user, revoked_at__isnull=True).values_list(
            "slug", flat=True
        )
    )
    candidates = [base]
    if extra and extra != base:
        candidates.append(f"{base}-{extra}")
    for c in candidates:
        if c and c not in taken:
            return c
    stem = candidates[-1]
    n = 2
    while f"{stem}-{n}" in taken:
        n += 1
    return f"{stem}-{n}"


def application_slug(application) -> str:
    """`<company>` or `<company>-<role>` — readable and owner-editable (per-user unique).

    A second application to the same company falls to `<company>-<role>` (e.g.
    `acme-intern` vs `acme-lead`), then a numeric tail. The owner can rename the link
    before the sent-freeze via the manage UI.
    """
    base = slugify(_application_company(application))[:40].strip("-") or "application"
    role = slugify(application.posting.title or "")[:20].strip("-")
    return _dedupe_slug(application.user, base, role)
```

`link_for_application` (`:55-77`) keeps its `IntegrityError` re-roll loop as-is — it's still
the safety net for the (now per-user) `unique_active_slug_per_user` race.

**`landing_context` takes the request** (`:565`):

```python
def landing_context(request) -> dict:
    """Context for the Django-rendered landing at the apex/handle host."""
    owner = resolve_owner(request)
    return {
        "owner": _owner_block(owner) if owner else None,
        "domains": owner_domains(owner) if owner else [],
        "explore_url": settings.FRONTEND_URL,  # guide 3: the handle-root questionnaire
        "signup_url": f"{settings.FRONTEND_URL}/auth/signup",
    }
```

### 5. `views.py` — resolve owner from the request

Swap the `get_owner` import (`:21-28`) to `resolve_owner`, and update the four native views +
the resolve view to pass `request`. The three native views (`PortfolioNativeView`,
`PortfolioRankView`, `PortfolioMetaView`, `PortfolioIntroView`) currently call bare
`get_owner()`; change each to `resolve_owner(request)`. Example for `PortfolioNativeView`
(`:236`):

```python
        owner = resolve_owner(request)
        if owner is None:
            raise Http404
```

**`PortfolioResolveView`** (`:221-227`) — scope the slug to the host owner (per-user slugs):

```python
    def get(self, request, slug):
        owner = resolve_owner(request)
        if owner is None:
            raise Http404
        link = get_object_or_404(
            PortfolioLink.objects.filter(revoked_at__isnull=True, user=owner), slug=slug
        )
        if request.user.pk != link.user_id:
            bump_visit(link)
        return Response(build_payload(link.user, link=link))
```

### 6. `serializers.py` — URL + per-user slug validation

`PortfolioLinkSerializer.get_url` (`:301-302`):

```python
    def get_url(self, obj) -> str:
        from spa.portfolio import public_portfolio_url

        return public_portfolio_url(obj)
```

Add a per-user active-slug uniqueness check (the partial DB constraint isn't enforced by
DRF's auto-validators). Add a `validate` to `PortfolioLinkSerializer`:

```python
    def validate(self, attrs):
        slug = attrs.get("slug")
        if slug:
            user = self.instance.user if self.instance else self.context["request"].user
            clash = PortfolioLink.objects.filter(
                user=user, slug=slug, revoked_at__isnull=True
            )
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    {"slug": "You already have an active portfolio with this slug."}
                )
        return attrs
```

### 7. `urls.py` — landing request + the legacy 301

In `lukehirsch/urls.py`, `landing` already receives `request` (`:12`) — just pass it:

```python
def landing(request):
    return render(request, "spa/landing.html", landing_context(request))
```

Add a legacy redirect (insurance for any `apex/portfolio/<slug>` QR minted in the
single-owner era) above the `path("", landing, ...)` line:

```python
from django.shortcuts import redirect
from spa.models import PortfolioLink
from spa.portfolio import public_portfolio_url


def legacy_portfolio_redirect(request, slug):
    """Old QR target `<apex>/portfolio/<slug>` -> the canonical `<handle>` origin. Global
    first-match: pre-rework slugs were globally unique, so this is unambiguous for them."""
    link = PortfolioLink.objects.filter(revoked_at__isnull=True, slug=slug).first()
    if link is None:
        return redirect(settings.FRONTEND_URL)
    return redirect(public_portfolio_url(link), permanent=True)
```

and register it:

```python
    path("portfolio/<slug:slug>/", legacy_portfolio_redirect, name="legacy-portfolio"),
    path("", landing, name="index"),
```

### 8. Migration `0006`

`python manage.py makemigrations spa` produces the field + constraint ops; then add a
`RunPython` to backfill handles for existing profiles. Hand-written skeleton:

```python
from django.db import migrations, models
from django.utils.text import slugify


def backfill_handles(apps, schema_editor):
    UserProfile = apps.get_model("spa", "UserProfile")
    taken = set()
    for p in UserProfile.objects.select_related("user").all():
        base = slugify(p.user.username)[:40].strip("-") or "user"
        cand, n = base, 2
        while cand in taken or UserProfile.objects.filter(handle__iexact=cand).exclude(pk=p.pk).exists():
            suffix = f"-{n}"
            cand = f"{base[: 40 - len(suffix)]}{suffix}"
            n += 1
        p.handle = cand
        p.save(update_fields=["handle"])
        taken.add(cand)


class Migration(migrations.Migration):
    dependencies = [("spa", "0005_portfoliolink_is_default_and_more")]
    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="handle",
            field=models.SlugField(blank=True, max_length=40, null=True),
        ),
        migrations.RunPython(backfill_handles, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="userprofile",
            name="handle",
            field=models.SlugField(blank=True, max_length=40, unique=True),
        ),
        migrations.RemoveConstraint(model_name="portfoliolink", name=None)  # see note
        if False else migrations.AlterField(
            model_name="portfoliolink",
            name="slug",
            field=models.SlugField(max_length=80),
        ),
        migrations.AddConstraint(
            model_name="portfoliolink",
            constraint=models.UniqueConstraint(
                fields=["user", "slug"],
                condition=models.Q(revoked_at__isnull=True),
                name="unique_active_slug_per_user",
            ),
        ),
    ]
```

> Note: let `makemigrations` generate the real ops (it will emit the `AlterField` that drops
> `unique=True` and the `AddConstraint`), then splice in the `AddField(null=True)` →
> `RunPython` → `AlterField(unique=True)` handle sequence so the backfill runs before
> uniqueness is enforced. The `if False else` line above is just a placeholder reminder —
> delete it. Keep `handle` `null=True` only transiently inside the migration; the model
> declares it `blank=True` (not null), which is fine because the signal always sets it.

---

## Tests

Written to disk (red until this guide lands): **`backend/spa/tests/test_portfolio.py`** gains

- `OwnerForHostTests` — `owner_for_host` maps `jane.localhost`→jane, apex→configured owner,
  `www.`→configured owner, reserved (`app.localhost`)→None, unknown handle→None, foreign
  host→None, port-stripping, inactive user→None.
- `MintHandleTests` — slugified, reserved-avoiding, collision-incrementing.
- `PerUserSlugTests` — two users can both hold `acme`; one user can't hold two active
  `acme`; a revoked `acme` frees the slug.
- `ApplicationSlugTests` — first app → `<company>`; second same-company app → `<company>-<role>`.
- `PublicPortfolioUrlTests` — `public_portfolio_url` uses the handle + template.
- The existing endpoint classes (`PublicPortfolioEndpointTests` and any that hit
  `/api/spa/portfolio/native/*`) get `@override_settings(BASE_DOMAIN="testserver")` so the
  default test host (`testserver`) resolves as the apex → configured owner. New per-user
  endpoint hits use `HTTP_HOST="jane.testserver"`.

Run: `cd backend && python manage.py test spa.tests.test_portfolio`

## Verification

1. `python manage.py migrate` — 0006 applies; `UserProfile.objects.get(user__username="lukas").handle == "lukas"`.
2. `python manage.py test spa` — green.
3. Shell: `from spa.portfolio import owner_for_host; owner_for_host("lukas.localhost")` → the lukas user; `owner_for_host("app.localhost")` → None; `owner_for_host("localhost")` → lukas (apex).
4. `curl -H 'Host: lukas.localhost' localhost:8000/api/spa/portfolio/native/meta/` → 200 with domains; `curl -H 'Host: nobody.localhost' …` → 404; `curl -H 'Host: app.localhost' …` → 404.
5. Create a second user in the shell; give them a `Job`; `curl -H 'Host: <their-handle>.localhost' …/native/meta/` returns THEIR domains, not Lukas's. **This is the multi-user proof.**
6. Owner-side: `POST /api/jac/applications/<pk>/portfolio-link/` twice for two apps at the same company → slugs `acme` and `acme-<role>`; the `url` field is `http://lukas.localhost:5173/acme`.

## Results

_(human fills after testing: raw test output, observed issues, what works)_
question regarding step 6

> Add a per-user active-slug uniqueness check (the partial DB constraint isn't enforced by DRF's auto-validators). Add a validate to PortfolioLinkSerializer:
>
> ```
>    def validate(self, attrs):
>        slug = attrs.get("slug")
>        if slug:
>            user = self.instance.user if self.instance else self.context["request"].user
>            clash = PortfolioLink.objects.filter(
>                user=user, slug=slug, revoked_at__isnull=True
>            )
>            if self.instance:
>                clash = clash.exclude(pk=self.instance.pk)
>            if clash.exists():
>                raise serializers.ValidationError(
>                    {"slug": "You already have an active portfolio with this slug."}
>                )
>        return attrs
> ```

there are two validate methods already defined in the calss. one of them is vlaidate slug. should they be removed? since they are doing different things, maybe not, but maybe they can be merged for readability?

migration failed.

```
lukas@localhost backend % ./manage.py makemigrations
Migrations for 'spa':
  spa/migrations/0006_userprofile_handle_alter_portfoliolink_slug_and_more.py
    + Add field handle to userprofile
    ~ Alter field slug on portfoliolink
    + Create constraint unique_active_slug_per_user on model portfoliolink
lukas@localhost backend % ./manage.py migrate
Operations to perform:
  Apply all migrations: account, admin, auth, contenttypes, jac, llm_connector, mfa, sessions, sites, spa, usersessions
Running migrations:
  Applying spa.0006_userprofile_handle_alter_portfoliolink_slug_and_more...Traceback (most recent call last):
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/backends/utils.py", line 105, in _execute
    return self.cursor.execute(sql, params)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/backends/sqlite3/base.py", line 359, in execute
    return super().execute(query, params)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
sqlite3.IntegrityError: UNIQUE constraint failed: new__spa_userprofile.handle

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/Users/lukas/Projects/jac/backend/./manage.py", line 22, in <module>
    main()
  File "/Users/lukas/Projects/jac/backend/./manage.py", line 18, in main
    execute_from_command_line(sys.argv)
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/core/management/__init__.py", line 443, in execute_from_command_line
    utility.execute()
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/core/management/__init__.py", line 437, in execute
    self.fetch_command(subcommand).run_from_argv(self.argv)
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/core/management/base.py", line 420, in run_from_argv
    self.execute(*args, **cmd_options)
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/core/management/base.py", line 464, in execute
    output = self.handle(*args, **options)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/core/management/base.py", line 111, in wrapper
    res = handle_func(*args, **kwargs)
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/core/management/commands/migrate.py", line 354, in handle
    post_migrate_state = executor.migrate(
                         ^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/migrations/executor.py", line 137, in migrate
    state = self._migrate_all_forwards(
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/migrations/executor.py", line 169, in _migrate_all_forwards
    state = self.apply_migration(
            ^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/migrations/executor.py", line 257, in apply_migration
    state = migration.apply(state, schema_editor)
            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/migrations/migration.py", line 132, in apply
    operation.database_forwards(
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/migrations/operations/fields.py", line 118, in database_forwards
    schema_editor.add_field(
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/backends/sqlite3/schema.py", line 325, in add_field
    self._remake_table(model, create_field=field)
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/backends/sqlite3/schema.py", line 252, in _remake_table
    self.execute(
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/backends/base/schema.py", line 205, in execute
    cursor.execute(sql, params)
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/backends/utils.py", line 122, in execute
    return super().execute(sql, params)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/backends/utils.py", line 79, in execute
    return self._execute_with_wrappers(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/backends/utils.py", line 92, in _execute_with_wrappers
    return executor(sql, params, many, context)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/backends/utils.py", line 100, in _execute
    with self.db.wrap_database_errors:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/utils.py", line 94, in __exit__
    raise dj_exc_value.with_traceback(traceback) from exc_value
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/backends/utils.py", line 105, in _execute
    return self.cursor.execute(sql, params)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/backends/sqlite3/base.py", line 359, in execute
    return super().execute(query, params)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
django.db.utils.IntegrityError: UNIQUE constraint failed: new__spa_userprofile.handle
```

test still import the replaced get_owner function

```
lukas@localhost backend % ./manage.py test
Found 327 test(s).
Creating test database for alias 'default'...
System check identified no issues (0 silenced).
E......................................................................................................................................................................................................................................................................................................................................
======================================================================
ERROR: spa.tests.test_portfolio (unittest.loader._FailedTest.spa.tests.test_portfolio)
----------------------------------------------------------------------
ImportError: Failed to import test module: spa.tests.test_portfolio
Traceback (most recent call last):
  File "/Users/lukas/.pyenv/versions/3.12.10/lib/python3.12/unittest/loader.py", line 396, in _find_test_path
    module = self._get_module_from_name(name)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/3.12.10/lib/python3.12/unittest/loader.py", line 339, in _get_module_from_name
    __import__(name)
  File "/Users/lukas/Projects/jac/backend/spa/tests/test_portfolio.py", line 20, in <module>
    from spa.portfolio import (
ImportError: cannot import name 'get_owner' from 'spa.portfolio' (/Users/lukas/Projects/jac/backend/spa/portfolio.py)


----------------------------------------------------------------------
Ran 327 tests in 199.226s

FAILED (errors=1)
Destroying test database for alias 'default'...
lukas@localhost backend %
```
