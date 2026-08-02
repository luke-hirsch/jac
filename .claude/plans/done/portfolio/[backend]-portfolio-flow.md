# [backend] portfolio flow rework — owner fix, dynamic questionnaire, AI intro, Django landing

> **Portfolio-rework phase, guide 1 of 3** (branch **`portfolio-flow-rework`**, cut off
> `portfolio-all` — the shipped portfolio backend lives there, `main` is 6 commits behind and has
> none of it). Reworks the shipped portfolio backend (`done/portfolio` guides 1–2). The frontend
> flow guide (`[frontend]-portfolio-flow`, guide 2) and the UI guide (guide 3) build on the
> payload/API shape this guide sets.

## Context / goal

Clicking through the portfolio dead-ends on a 404 and the "questionnaire about me" flow never
personalises anything. Root causes + the agreed rework (see the discussion that produced this):

1. **The 404 is a one-char data bug.** `get_owner()` matches `username=` **exactly**;
   `PORTFOLIO_OWNER_USERNAME` defaults to `"Lukas"` but the DB user is `"lukas"`, so `get_owner()`
   returns `None` and every native / lucky / rank / intro call 404s. Fix: **case-insensitive**
   resolution (`username__iexact`).
2. **The questionnaire domains are hardcoded** (`music`/`fashion`/`AI` in `questionnaire.ts`) and
   silently widen to the whole portfolio when they don't match real `Domain` tags. The rework
   sources the shortlist from the owner's **real** domains (a public `meta` endpoint), so no branch
   dead-ends.
3. **Build engine = Hybrid.** Embeddings still do all selection (deterministic, HirschAI, free); the
   only generative call in the anonymous path is **one short AI intro paragraph**, on its own
   throttled endpoint, HirschAI-only by construction, degrading to `""` (no intro) on any failure.
   The **style axis** (technical↔soft `focus`, personal↔formal `tone` — reusing the existing
   `PersonalityProfile.Tone`/`.Focus` vocab) reorders career sections and block placement.
4. **Fallback / standard portfolio.** The owner flags one manual link `is_default`; when a native
   result is empty (the "Nothing to show" dead-end) the flow falls back to that link's content.
5. **Static `/` landing.** `/` becomes a **Django-rendered** SEO front door (link-tree + owner
   identity), served by Django; `/health/` keeps the JSON liveness check. The SPA moves its
   questionnaire to `/me` in guide 2; the prod `/`-vs-SPA routing split is nginx (documented in
   guide 2).

Security posture (CLAUDE.md, `[[public-site-posture]]`, `[[cover-letter-grounding-metric]]`): the
intro is the **first and only** generative call in the anonymous path — a deliberate, conscious
narrowing of guide 2's "embeddings-only" guarantee. It stays **structurally HirschAI-only**
(`Executor(HIRSCHAI_PROVIDER)` resolves config via `hirschai_row()`, never a user key — same
mechanism as `embed()`), so "never commercial" holds; abuse is capped by a tight `portfolio-intro`
throttle (6/hour, mirroring `portfolio-rank`). Everything public stays explicit `AllowAny` on the
`PublicPortfolioAPIView` base.

## Affected files

| file                                           | why                                                                                                                                                                                    |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/lukehirsch/settings.py`               | `portfolio-intro` throttle rate (owner default stays — `iexact` fixes it)                                                                                                              |
| `backend/spa/models.py`                        | `PortfolioLink.is_default` + partial-unique constraint + `save()` exclusivity                                                                                                          |
| `backend/spa/migrations/000X_*.py`             | generated — `makemigrations spa`                                                                                                                                                       |
| `backend/spa/portfolio.py`                     | `get_owner` iexact; `owner_domains`, `section_order`, `default_link`; `_entries` `order=`; `build_payload` style + fallback; `PortfolioIntroWriter` + `build_intro`; `landing_context` |
| `backend/spa/serializers.py`                   | `is_default` on `PortfolioLinkSerializer`; new `PortfolioIntroSerializer`                                                                                                              |
| `backend/spa/views.py`                         | native `focus`/`tone` passthrough; `PortfolioMetaView`, `PortfolioIntroView`                                                                                                           |
| `backend/spa/urls.py`                          | `portfolio/native/meta/`, `portfolio/native/intro/` paths                                                                                                                              |
| `backend/lukehirsch/urls.py`                   | `/` → `landing` (HTML), `/health/` → JSON; drop the DRF `IndexView`                                                                                                                    |
| `backend/spa/templates/spa/landing.html`       | **new** — the server-rendered landing template                                                                                                                                         |
| `backend/spa/tests/test_portfolio.py`          | **new** — the red topic tests (AI-written, below)                                                                                                                                      |
| `backend/spa/tests/test_settings_hardening.py` | `test_index_stays_public` now expects HTML + `/health/`                                                                                                                                |

Import direction stays clean: `spa.portfolio` already imports `jac.llm_prompts.Embed` at module top
and calls `jac` models via runtime-only imports; it now also imports `llm_connector`
(`complete`/`Executor`/`HIRSCHAI_PROVIDER`/`hirschai_reachable`) at module top — `spa.views` already
imports `llm_connector.conf`, so this is an established direction.

## The code

Type in this order (settings + model first so the migration is generatable, then the logic, then
the views/urls/template).

### 1. `backend/lukehirsch/settings.py`

In `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` (L231-235) add the intro scope — tight, like rank,
because it is the one generative anonymous call:

```python
    "DEFAULT_THROTTLE_RATES": {
        "llm-chat": "20/min",
        "portfolio": "60/hour",
        "portfolio-rank": "6/hour",
        "portfolio-intro": "6/hour",
    },
```

`PORTFOLIO_OWNER_USERNAME` (L294) is left as-is — `get_owner`'s new `iexact` match makes the
`"Lukas"` default resolve the `"lukas"` row. (Set `PORTFOLIO_OWNER_USERNAME=lukas` in `.env` if you
prefer an exact value; not required.)

### 2. `backend/spa/models.py` — `is_default`

On `PortfolioLink`, add the field (after `content`, before `revoked_at`, L298):

```python
    # The owner's "standard portfolio": the graceful fallback a native visit degrades
    # to when its personalised result is empty (kills the "Nothing to show" dead-end).
    # Exactly one active default per user (partial-unique below + save() exclusivity).
    is_default = models.BooleanField(default=False)
```

Extend `Meta.constraints` (L304-310) with a second partial-unique:

```python
    class Meta:
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
        ]
```

Add a `save()` override (the constraint is the belt; `save()` is the enforcement — the
`LLMConfig.save()` default-exclusivity pattern). Put it above `revoke()`:

```python
    def save(self, *args, **kwargs):
        """Enforce one active default link per user: promoting this row demotes the
        others FIRST (before super().save()), so the partial-unique constraint never
        trips mid-flight. A revoked link never holds defaulthood."""
        if self.is_default and self.revoked_at is None:
            others = PortfolioLink.objects.filter(
                user=self.user, is_default=True, revoked_at__isnull=True
            )
            if self.pk:
                others = others.exclude(pk=self.pk)
            others.update(is_default=False)
        super().save(*args, **kwargs)
```

Then generate + apply the migration:

```bash
cd backend && python manage.py makemigrations spa && python manage.py migrate
```

### 3. `backend/spa/portfolio.py`

**a. Module imports** — add below the existing `from jac.llm_prompts import Embed` (L17):

```python
import logging

from llm_connector import complete
from llm_connector.conf import HIRSCHAI_PROVIDER
from llm_connector.executor import Executor
from llm_connector.probe import hirschai_reachable
```

and, at the top with the other `import` lines:

```python
logger = logging.getLogger(__name__)
```

**b. `get_owner`** — case-insensitive (replace L102-109):

```python
def get_owner() -> User | None:
    """The public portfolio's owner — an explicit setting, never request-derived and
    never the system sentinel. Case-insensitive: the `"Lukas"` default resolves the
    `"lukas"` row (the shipped exact-match bug that 404'd every native call). None
    (unset / sentinel / unknown / inactive) disables the native flow: callers 404."""
    username = (settings.PORTFOLIO_OWNER_USERNAME or "").strip()
    if not username or username.lower() == settings.SYSTEM_USER_USERNAME.lower():
        return None
    return User.objects.filter(username__iexact=username, is_active=True).first()
```

**c. `owner_domains`** — the questionnaire's real shortlist. Add after `get_owner`:

```python
def owner_domains(owner) -> list[str]:
    """Domain names the owner actually has content under (career entries or active
    blocks), sorted. Only non-empty domains — so no questionnaire branch dead-ends (the
    hardcoded-name bug). Language has no domains M2M and is excluded by construction."""
    names: set[str] = set()
    for t in ("job", "project", "skill", "education", "certification"):
        for obj in (
            _career_models()[t].objects.filter(user=owner).prefetch_related("domains")
        ):
            names.update(d.name for d in obj.domains.all())
    for b in PortfolioBlock.objects.filter(
        user=owner, is_active=True
    ).prefetch_related("domains"):
        names.update(d.name for d in b.domains.all())
    return sorted(names)


def section_order(focus: str = "balanced") -> list[str]:
    """Career section order for the style axis' `focus` (soft↔technical). `technical`
    leads with skills/projects; `soft_skill` leads with roles/education; anything else
    keeps the default. Language always trails (no domains, thin content)."""
    if focus == "technical":
        return ["skill", "project", "job", "certification", "education", "language"]
    if focus == "soft_skill":
        return ["job", "education", "language", "project", "certification", "skill"]
    return list(_SECTION_ORDER)


def default_link(owner):
    """The owner's active `is_default` manual link, or None. The native fallback target."""
    return owner.portfolio_links.filter(
        is_default=True, revoked_at__isnull=True
    ).first()
```

**d. `_entries`** — accept an explicit section order (replace the signature + loop head, L231-237):

```python
def _entries(
    owner, *, domains=None, favourite=False, exclude_ids=frozenset(), order=None
) -> list[dict]:
    """Career items in section order; `order` overrides `_SECTION_ORDER` (the style
    axis). `domains` (Domain rows) scopes, `favourite` restricts. Language has no
    domains M2M — it drops out of any domain-scoped view."""
    out = []
    for t in order or _SECTION_ORDER:
        model = _career_models()[t]
        qs = model.objects.filter(user=owner)
```

(the rest of the loop body — `if favourite`, `if domains is not None`, prefetch, `out +=` — is
unchanged.)

**e. `build_payload`** — thread `focus`/`tone`, apply the style axis, add the fallback. Replace the
signature and the whole non-link tail (L283-351) with:

```python
def build_payload(
    owner, *, link=None, domains=None, lucky=False, seed=None,
    focus="balanced", tone="neutral",
) -> dict:
    """The whole public page in one dict: `{owner, kind, title, intro, featured, more}`.

    Link mode (unchanged): frozen ids, application links preview live `cv_content`.
    Native mode: `domains` (names) scope; `focus` reorders career sections; `tone`
    ("personal") floats blocks ahead of career entries. `lucky` = favourites + a seeded
    random tasting menu. When a native result is EMPTY the flow degrades to the owner's
    default link (the standard portfolio) — no dead-end. Position IS the featured signal;
    the favourite flag never leaks.
    """
    if link is not None:
        content = link.content or {}
        featured_ids = list(content.get("featured") or [])
        if (
            not featured_ids
            and link.kind == PortfolioLink.Kind.application
            and link.application
        ):
            featured_ids = [
                row["id"]
                for rows in (link.application.cv_content or {}).values()
                for row in rows
                if row.get("id") and not row.get("deselected")
            ]
        featured = resolve_items(owner, featured_ids)
        more: list[dict] = []
        if not content.get("hide_explore"):
            exclude = {i["id"] for i in featured}
            matched = _matched_domains(owner, content.get("domains") or [])
            if matched:
                more = _entries(owner, domains=matched, exclude_ids=exclude)
                more += _blocks(owner, domains=matched, exclude_ids=exclude)
            else:
                more = _entries(owner, favourite=True, exclude_ids=exclude)
                more += _blocks(owner, exclude_ids=exclude)
        return {
            "kind": link.kind,
            "title": link.title,
            "intro": link.intro,
            "owner": _owner_block(owner),
            "featured": featured,
            "more": more,
        }

    if lucky:
        featured = _entries(owner, favourite=True) + _blocks(owner, favourite=True)
        exclude = {i["id"] for i in featured}
        pool = _entries(owner, exclude_ids=exclude) + _blocks(
            owner, exclude_ids=exclude
        )
        rng = random.Random(seed)
        more = rng.sample(pool, min(10, len(pool)))
    else:
        matched = _matched_domains(owner, domains or [])
        scope = matched or None  # nothing matched → the full portfolio
        order = section_order(focus)
        blocks_first = tone == "personal"
        feat_e = _entries(owner, domains=scope, favourite=True, order=order)
        feat_b = _blocks(owner, domains=scope, favourite=True)
        featured = (feat_b + feat_e) if blocks_first else (feat_e + feat_b)
        exclude = {i["id"] for i in featured}
        more_e = _entries(owner, domains=scope, exclude_ids=exclude, order=order)
        more_b = _blocks(owner, domains=scope, exclude_ids=exclude)
        more = (more_b + more_e) if blocks_first else (more_e + more_b)

    if not featured and not more:
        default = default_link(owner)
        if default is not None:
            payload = build_payload(owner, link=default)
            payload["kind"] = "native"  # rendered via the native flow, not a shared link
            return payload

    return {
        "kind": "native",
        "title": "",
        "intro": "",
        "owner": _owner_block(owner),
        "featured": featured,
        "more": more,
    }
```

**f. The AI intro** — append after `rank_for_query` (end of file):

```python
# ── AI intro (the ONE generative call in the anonymous path — Hybrid engine) ────
# HirschAI-only by construction: Executor(HIRSCHAI_PROVIDER) resolves config via
# hirschai_row(), never a user key (same mechanism as embed()). Throttled at the view
# (portfolio-intro, 6/h). Any failure / unreachable tower -> "" -> the page renders the
# standard portfolio with no intro. It never asserts facts: it references only the
# visitor's stated interest and the actual featured highlights.

_INTRO_TONE = {
    "personal": "Warm, first-person and genuine, as if greeting the visitor directly.",
    "neutral": "Professional with measured warmth.",
    "formal": "Reserved and professional.",
}
_INTRO_FOCUS = {
    "technical": "Emphasise concrete technical work.",
    "soft_skill": "Emphasise motivation, values and working style.",
    "balanced": "Give craft and character roughly equal weight.",
}


class PortfolioIntroWriter:
    """One short personalised welcome paragraph for a native portfolio visit."""

    _TARGET_WORDS = (40, 80)
    _INSTRUCTION = (
        "Write ONE short welcome paragraph ({lo}-{hi} words) for a visitor to {name}'s "
        "personal portfolio. The visitor is interested in: {interest}. Write in the first "
        "person as {name}. Reference ONLY that interest and the HIGHLIGHTS listed below — "
        "invent no skills, employers, titles, numbers or dates. No header, no sign-off, no "
        "markdown, no lists — just the paragraph."
    )

    def __init__(
        self, name, interest, highlights, focus="balanced", tone="neutral"
    ):
        self.name = name or "the owner"
        self.interest = interest or "your work in general"
        self.highlights = highlights
        self.focus = focus
        self.tone = tone

    def write(self) -> str:
        """The paragraph, or '' (tower unreachable / any failure — the caller then
        renders the standard portfolio without an intro)."""
        if not hirschai_reachable():
            return ""
        try:
            raw = complete(prompt=self._prompt(), executor=Executor(HIRSCHAI_PROVIDER))
        except Exception:
            logger.exception("PortfolioIntroWriter: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        lo, hi = self._TARGET_WORDS
        flavour = " ".join(
            v
            for v in (_INTRO_TONE.get(self.tone), _INTRO_FOCUS.get(self.focus))
            if v
        )
        highlights = "\n".join(f"- {h}" for h in self.highlights[:12]) or "(none yet)"
        return (
            self._INSTRUCTION.format(
                lo=lo, hi=hi, name=self.name, interest=self.interest
            )
            + (f"\n{flavour}" if flavour else "")
            + f"\n\nHIGHLIGHTS:\n{highlights}\n\nWELCOME PARAGRAPH:"
        )


def build_intro(
    owner, *, domains=None, question="", focus="balanced", tone="neutral"
) -> str:
    """Assemble + run the intro for a native visit. Grounds the writer in the actual
    featured highlights for this (domains, style) selection."""
    payload = build_payload(owner, domains=domains, focus=focus, tone=tone)
    highlights = [
        i.get("title") or i.get("subtitle") or ""
        for i in payload["featured"]
    ]
    interest = ", ".join(domains or [])
    if question:
        interest = f"{interest}; specifically: {question}".strip(" ;")
    return PortfolioIntroWriter(
        name=payload["owner"]["display_name"],
        interest=interest,
        highlights=[h for h in highlights if h],
        focus=focus,
        tone=tone,
    ).write()


def landing_context() -> dict:
    """Context for the Django-rendered `/` landing. Owner block is None when the owner
    setting is unset/unknown — the template falls back to a minimal page."""
    owner = get_owner()
    return {
        "owner": _owner_block(owner) if owner else None,
        "domains": owner_domains(owner) if owner else [],
        "explore_url": f"{settings.FRONTEND_URL}/me",
        "signup_url": f"{settings.FRONTEND_URL}/auth/signup",
    }
```

### 4. `backend/spa/serializers.py`

Add `is_default` to `PortfolioLinkSerializer` — writable (the manage UI sets it in guide 3). Extend
`Meta.fields` (after `"content"`, L280) with `"is_default"`; it is **not** read-only. Then append the
intro input serializer (`PersonalityProfile` is already imported):

```python
class PortfolioIntroSerializer(serializers.Serializer):
    """Input for the AI-intro endpoint — same caps as the rank finale, plus the style
    axis (reusing the personality tone/focus vocab)."""

    query = serializers.CharField(
        max_length=MAX_ANSWER_LEN, required=False, allow_blank=True
    )
    domains = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, max_length=10
    )
    tone = serializers.ChoiceField(
        choices=PersonalityProfile.Tone.choices,
        required=False,
        default=PersonalityProfile.Tone.neutral,
    )
    focus = serializers.ChoiceField(
        choices=PersonalityProfile.Focus.choices,
        required=False,
        default=PersonalityProfile.Focus.balanced,
    )
```

### 5. `backend/spa/views.py`

Extend the `spa.portfolio` import (L21):

```python
from spa.portfolio import (
    build_intro,
    build_payload,
    bump_visit,
    get_owner,
    owner_domains,
    rank_for_query,
)
```

and the serializer import (L22-29) with `PortfolioIntroSerializer`.

`PortfolioNativeView.get` — pass the style axis through (replace L227-237):

```python
    def get(self, request):
        owner = get_owner()
        if owner is None:
            raise Http404
        domains = [
            d.strip()
            for d in (request.query_params.get("domains") or "").split(",")
            if d.strip()
        ]
        lucky = request.query_params.get("lucky") in ("1", "true")
        return Response(
            build_payload(
                owner,
                domains=domains,
                lucky=lucky,
                focus=request.query_params.get("focus") or "balanced",
                tone=request.query_params.get("tone") or "neutral",
            )
        )
```

Append the two new public views (after `PortfolioRankView`):

```python
class PortfolioMetaView(PublicPortfolioAPIView):
    """GET: the questionnaire's building blocks — the owner's REAL domain shortlist (only
    domains with content, so no branch dead-ends) plus the style-axis vocab."""

    def get(self, request):
        owner = get_owner()
        if owner is None:
            raise Http404
        return Response(
            {
                "domains": owner_domains(owner),
                "tones": [
                    {"value": v, "label": str(label)}
                    for v, label in PersonalityProfile.Tone.choices
                ],
                "focuses": [
                    {"value": v, "label": str(label)}
                    for v, label in PersonalityProfile.Focus.choices
                ],
            }
        )


class PortfolioIntroView(PublicPortfolioAPIView):
    """POST: the AI intro — the one generative anonymous call (HirschAI-only, tight
    `portfolio-intro` throttle). Returns `{intro}` ('' when the tower is down / any
    failure → the page shows the standard portfolio with no intro)."""

    throttle_scope = "portfolio-intro"

    def post(self, request):
        owner = get_owner()
        if owner is None:
            raise Http404
        ser = PortfolioIntroSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        intro = build_intro(
            owner,
            domains=ser.validated_data.get("domains") or [],
            question=ser.validated_data.get("query") or "",
            focus=ser.validated_data["focus"],
            tone=ser.validated_data["tone"],
        )
        return Response({"intro": intro})
```

### 6. `backend/spa/urls.py`

Add the two views to the import block and append the paths (order after the existing native paths —
`meta`/`intro` are distinct so no collision):

```python
    path(
        "portfolio/native/meta/",
        PortfolioMetaView.as_view(),
        name="portfolio-meta",
    ),
    path(
        "portfolio/native/intro/",
        PortfolioIntroView.as_view(),
        name="portfolio-intro",
    ),
```

### 7. `backend/lukehirsch/urls.py` — `/` → Django landing, `/health/` → JSON

Replace the DRF `IndexView` (L1-17, L32) with plain Django views. New top of file:

```python
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from spa.portfolio import landing_context


def landing(request):
    # The site root is public + server-rendered (SEO front door / link-tree). The SPA
    # owns /me and /portfolio/* (dev: Vite; prod: nginx routes / here, the rest to the SPA).
    return render(request, "spa/landing.html", landing_context())


def health(request):
    return JsonResponse({"message": "I am alive!"})
```

and the urlpatterns root entries (replace the `IndexView` line):

```python
    path("health/", health, name="health"),
    path("", landing, name="index"),
```

(Drop the now-unused `AllowAny` / `Response` / `APIView` imports.)

### 8. `backend/spa/templates/spa/landing.html` — **new**

`TEMPLATES` has `APP_DIRS=True`, so an app-level template dir is picked up. Create
`backend/spa/templates/spa/landing.html`. Self-contained (inline CSS, no static pipeline),
theme-aware, SEO meta:

```html
{% spaceless %}
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>
      {% if owner %}{{ owner.display_name }} — Portfolio{% else %}Portfolio{%
      endif %}
    </title>
    <meta
      name="description"
      content="{% if owner and owner.bio %}{{ owner.bio }}{% else %}A personal portfolio & CV automation showcase.{% endif %}"
    />
    <meta property="og:type" content="website" />
    <meta
      property="og:title"
      content="{% if owner %}{{ owner.display_name }}{% endif %}"
    />
    <meta
      property="og:description"
      content="{% if owner and owner.bio %}{{ owner.bio }}{% endif %}"
    />
    {% if owner and owner.avatar_url %}
    <meta property="og:image" content="{{ owner.avatar_url }}" />
    {% endif %}
    <style>
      :root {
        --bg: #fafafa;
        --fg: #18181b;
        --muted: #71717a;
        --card: #fff;
        --border: #e4e4e7;
        --accent: #4f46e5;
      }
      @media (prefers-color-scheme: dark) {
        :root {
          --bg: #0a0a0a;
          --fg: #fafafa;
          --muted: #a1a1aa;
          --card: #18181b;
          --border: #27272a;
          --accent: #818cf8;
        }
      }
      * {
        box-sizing: border-box;
      }
      body {
        margin: 0;
        min-height: 100vh;
        background: var(--bg);
        color: var(--fg);
        font:
          16px/1.6 system-ui,
          -apple-system,
          Segoe UI,
          Roboto,
          sans-serif;
        display: grid;
        place-items: center;
        padding: 2rem;
      }
      main {
        width: 100%;
        max-width: 34rem;
        text-align: center;
      }
      img.avatar {
        width: 96px;
        height: 96px;
        border-radius: 50%;
        object-fit: cover;
        margin: 0 auto 1rem;
        display: block;
        border: 1px solid var(--border);
      }
      h1 {
        font-size: 1.9rem;
        margin: 0 0 0.25rem;
        letter-spacing: -0.02em;
      }
      p.bio {
        color: var(--muted);
        margin: 0 auto 1.75rem;
        max-width: 28rem;
      }
      .links {
        display: grid;
        gap: 0.75rem;
      }
      a.card {
        display: block;
        padding: 0.9rem 1.1rem;
        border: 1px solid var(--border);
        border-radius: 0.75rem;
        background: var(--card);
        color: inherit;
        text-decoration: none;
        font-weight: 600;
        transition: border-color 0.15s;
      }
      a.card:hover {
        border-color: var(--accent);
      }
      a.card.primary {
        background: var(--accent);
        color: #fff;
        border-color: var(--accent);
      }
      .socials {
        margin-top: 1.25rem;
        display: flex;
        gap: 1rem;
        justify-content: center;
      }
      .socials a {
        color: var(--muted);
        text-decoration: none;
        font-size: 0.9rem;
      }
      .socials a:hover {
        color: var(--accent);
      }
      .chips {
        margin-top: 1.5rem;
        display: flex;
        flex-wrap: wrap;
        gap: 0.4rem;
        justify-content: center;
      }
      .chip {
        font-size: 0.78rem;
        color: var(--muted);
        border: 1px solid var(--border);
        border-radius: 999px;
        padding: 0.15rem 0.6rem;
      }
    </style>
  </head>
  <body>
    <main>
      {% if owner %} {% if owner.avatar_url %}<img
        class="avatar"
        src="{{ owner.avatar_url }}"
        alt="{{ owner.display_name }}"
      />{% endif %}
      <h1>{{ owner.display_name }}</h1>
      {% if owner.bio %}
      <p class="bio">{{ owner.bio }}</p>
      {% endif %} {% else %}
      <h1>Portfolio</h1>
      {% endif %}

      <div class="links">
        <a class="card primary" href="{{ explore_url }}">Explore my work →</a>
        <a class="card" href="{{ signup_url }}">Build your own CV with JAC</a>
      </div>

      {% if owner.website or owner.linkedin_url or owner.github_url %}
      <div class="socials">
        {% if owner.website %}<a href="{{ owner.website }}">Website</a>{% endif
        %} {% if owner.linkedin_url %}<a href="{{ owner.linkedin_url }}"
          >LinkedIn</a
        >{% endif %} {% if owner.github_url %}<a href="{{ owner.github_url }}"
          >GitHub</a
        >{% endif %}
      </div>
      {% endif %} {% if domains %}
      <div class="chips">
        {% for d in domains %}<span class="chip">{{ d }}</span>{% endfor %}
      </div>
      {% endif %}
    </main>
  </body>
</html>
{% endspaceless %}
```

## Tests

AI-written, on disk, **red before you code**. Distributed per the topic-file convention
(`[[tests-split-by-topic-not-feature]]`):

- **`backend/spa/tests/test_portfolio.py`** — **new** topic file (guides 1–2 shipped without theirs;
  this establishes it). Covers: `get_owner` case-insensitivity (the 404 regression) + sentinel/unset
  → None; `owner_domains` (only domains with content, sorted); `section_order` (technical / soft /
  balanced); `build_payload` style axis (`focus=technical` orders skill-before-job in featured;
  `tone=personal` floats blocks first) and the **empty→default-link fallback** (with a default →
  non-empty; without → empty); `is_default` exclusivity (second default demotes the first; revoke
  frees it); `PortfolioIntroWriter` (`complete` mocked → text; `hirschai_reachable` False → ""; a
  raising `complete` → ""); the public **meta** endpoint (domains + tones + focuses; owner unset →
  404); the public **intro** endpoint (mocked → `{intro}`; owner unset → 404; over-length query →
  400); the **landing** (`GET /` → 200 HTML with the owner name) + `GET /health/` JSON.
- **`backend/spa/tests/test_settings_hardening.py`** — rewrite `test_index_stays_public`: `/` now
  returns **200 HTML** (not the old JSON), and the liveness JSON moved to `/health/`.

LLM calls are always **mocked** (`spa.portfolio.complete`, `spa.portfolio.hirschai_reachable`) — no
live tower in tests, mirroring the rank tests' discipline.

Run: `cd backend && python manage.py test spa.tests.test_portfolio spa.tests.test_settings_hardening`

## Verification

1. `makemigrations spa` → one migration (adds `is_default` + the partial-unique); `migrate` clean.
2. `python manage.py test spa` → the red set goes green; a clean wall of dots
   (`[[test-output-hygiene]]`).
3. **The 404 is gone** — logged out, tower up:
   - `curl -i localhost:8000/api/spa/portfolio/native/` → **200** (previously 404), favourites
     featured, `X-Robots-Tag: noindex`.
   - `curl localhost:8000/api/spa/portfolio/native/meta/` → your real domain names + tone/focus vocab.
   - `curl "localhost:8000/api/spa/portfolio/native/?focus=technical"` vs `?focus=soft_skill` → the
     featured order visibly differs (skills-first vs roles-first).
   - `curl -X POST localhost:8000/api/spa/portfolio/native/intro/ -H 'Content-Type: application/json'
-d '{"domains":["software development"],"query":"local AI models","tone":"personal","focus":"technical"}'`
     → `{"intro": "…"}`; a 7th call within the hour → **429**. Stop the tower → `{"intro": ""}`.
4. **Fallback** — in admin, flag one manual link `is_default`; make `build_payload` return empty by
   querying a domain with no favourites (`?domains=<empty-domain>`) → the response now carries the
   default link's featured content, not an empty page. Flag a second link default → the first
   un-flags (exclusivity).
5. **Landing** — open `http://localhost:8000/` in a browser → the server-rendered link-tree with
   your name/bio/avatar, "Explore my work" (→ `FRONTEND_URL/me`) and "Build your own CV" (→ signup),
   socials (if `show_socials`), domain chips. View source → real HTML, `<title>` + `<meta
description>` + `og:*` present (SEO). `curl localhost:8000/health/` → `{"message": "I am alive!"}`.
6. **Privacy sweep** — grep the native/intro/meta JSON: no email / phone / street / zip anywhere.

> Dev/prod note: in dev the SPA (Vite, :5173) still serves its own `/`; the Django landing is at
> Django's origin (:8000). The unified `/` → Django, `/me` + `/portfolio/*` → SPA split is nginx
> (prod), documented in guide 2. Guide 2 also moves the SPA questionnaire to `/me` and points the
> escape hatch there.

## Results

1. under localhost:8000 i end up in the DRF view saying: im alive. i stopped the dev server for the react app, and retried but ended at the same location.

```HTTP GET / 200 [0.06, 127.0.0.1:55727]
HTTP GET /static/rest_framework/css/bootstrap.min.css 200 [0.03, 127.0.0.1:55727]
HTTP GET /static/rest_framework/css/prettify.css 200 [0.03, 127.0.0.1:55729]
HTTP GET /static/rest_framework/css/bootstrap-tweaks.css 200 [0.03, 127.0.0.1:55728]
HTTP GET /static/rest_framework/js/ajax-form.js 200 [0.03, 127.0.0.1:55732]
HTTP GET /static/rest_framework/css/default.css 200 [0.03, 127.0.0.1:55730]
HTTP GET /static/rest_framework/js/jquery-3.7.1.min.js 200 [0.03, 127.0.0.1:55731]
HTTP GET /static/rest_framework/js/default.js 200 [0.00, 127.0.0.1:55732]
HTTP GET /static/rest_framework/js/csrf.js 200 [0.00, 127.0.0.1:55727]
HTTP GET /static/rest_framework/js/load-ajax-form.js 200 [0.00, 127.0.0.1:55730]
HTTP GET /static/rest_framework/js/bootstrap.min.js 200 [0.00, 127.0.0.1:55729]
HTTP GET /static/rest_framework/js/prettify-min.js 200 [0.00, 127.0.0.1:55728]
HTTP GET /static/rest_framework/img/grid.png 200 [0.00, 127.0.0.1:55731]
```

### Fixes applied (AI, 2026-07-30) — all 6 failures + 1 latent template bug resolved; `spa` suite green (41/41)

1. **Landing lost (the "I am alive" DRF view).** `lukehirsch/urls.py` kept the old
   `path("", IndexView.as_view())` **above** the new `path("", landing)`; Django matches top-down,
   so `/` still hit the DRF JSON view. Removed the stale `IndexView` route + class + its now-unused
   `AllowAny`/`Response`/`APIView` imports. (Dev note: this is Django's `:8000/`. The Vite SPA at
   `:5173/` still serves its own `/` until guide 2 moves the questionnaire to `/me`.)
2. **Style-axis `NoneType` (2 errors) + `build_intro` error.** The typed `_entries`/`_blocks` used
   `exclude_ids: set | None = None` with the filter `if exclude_ids is not None **and** … not in
   exclude_ids` — which drops **every** item whenever no exclusions are passed (the `feat_e`/`feat_b`
   / lucky calls). Swapped to `if exclude_ids is None **or** … not in exclude_ids` in both.
3. **`test_schema_stays_public` AnonymousUser crash (pre-existing, unrelated to this guide).**
   `PortfolioBlockSerializer.get_fields` called `Domain.objects.for_user(request.user)` during
   drf-spectacular schema generation, where `request.user` is `AnonymousUser` → cast-to-int crash.
   Guarded with `and request.user.is_authenticated` (anonymous never POSTs a block, so the default
   queryset is fine for schema gen).
4. **Template syntax error (surfaced once the landing route actually rendered).** Prettier
   (editor format-on-save) reflowed `landing.html` and split `{% endif %}` across a newline
   (`{% endif` / `%}`); Django's tokenizer can't parse a tag spanning lines → the `{% if %}` never
   closed. Rewrote the template with every Django tag isolated on its own line.
5. **Recurrence guard.** Added a root `.prettierignore` (`backend/**/templates/`) so format-on-save
   can't re-mangle Django templates.

Verified: `python manage.py test spa.tests.test_portfolio spa.tests.test_settings_hardening` →
`Ran 41 tests … OK`. Human still to run the live curl/browser verification (steps 3–6) with the
tower up.

2. might not be related to this guide, but still problematic:

```lukas@localhost backend % ./manage.py test
Found 351 test(s).
Creating test database for alias 'default'...
System check identified no issues (0 silenced).
.......................................................................................................................................................................................................................................................................................................EE.........F.E............FE............................
======================================================================
ERROR: test_focus_reorders_featured (spa.tests.test_portfolio.BuildPayloadStyleTests.test_focus_reorders_featured)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/lukas/Projects/jac/backend/spa/tests/test_portfolio.py", line 111, in test_focus_reorders_featured
    tech = [i["id"] for i in build_payload(owner, focus="technical")["featured"]]
                             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/Projects/jac/backend/spa/portfolio.py", line 404, in build_payload
    feat_e = _entries(owner, domains=scope, favourite=True, order=order)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/Projects/jac/backend/spa/portfolio.py", line 295, in _entries
    out += [_career_item(t, o) for o in qs if f"{t}:{o.pk}" not in exclude_ids]
                                              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: argument of type 'NoneType' is not iterable

======================================================================
ERROR: test_tone_personal_floats_blocks_first (spa.tests.test_portfolio.BuildPayloadStyleTests.test_tone_personal_floats_blocks_first)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/lukas/Projects/jac/backend/spa/tests/test_portfolio.py", line 126, in test_tone_personal_floats_blocks_first
    personal = [i["id"] for i in build_payload(owner, tone="personal")["featured"]]
                                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/Projects/jac/backend/spa/portfolio.py", line 404, in build_payload
    feat_e = _entries(owner, domains=scope, favourite=True, order=order)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/Projects/jac/backend/spa/portfolio.py", line 295, in _entries
    out += [_career_item(t, o) for o in qs if f"{t}:{o.pk}" not in exclude_ids]
                                              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: argument of type 'NoneType' is not iterable

======================================================================
ERROR: test_build_intro_wires_a_payload (spa.tests.test_portfolio.PortfolioIntroWriterTests.test_build_intro_wires_a_payload)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/lukas/.pyenv/versions/3.12.10/lib/python3.12/unittest/mock.py", line 1396, in patched
    return func(*newargs, **newkeywargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/Projects/jac/backend/spa/tests/test_portfolio.py", line 219, in test_build_intro_wires_a_payload
    build_intro(owner, domains=["music"], question="gigs", tone="personal"),
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/Projects/jac/backend/spa/portfolio.py", line 545, in build_intro
    payload = build_payload(owner, domains=domains, focus=focus, tone=tone)
              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/Projects/jac/backend/spa/portfolio.py", line 404, in build_payload
    feat_e = _entries(owner, domains=scope, favourite=True, order=order)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/Projects/jac/backend/spa/portfolio.py", line 295, in _entries
    out += [_career_item(t, o) for o in qs if f"{t}:{o.pk}" not in exclude_ids]
                                              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
TypeError: argument of type 'NoneType' is not iterable

======================================================================
ERROR: test_schema_stays_public (spa.tests.test_settings_hardening.DrfDefaultPermissionTests.test_schema_stays_public)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/models/fields/__init__.py", line 2128, in get_prep_value
    return int(value)
           ^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/contrib/auth/models.py", line 549, in __int__
    raise TypeError(
TypeError: Cannot cast AnonymousUser to int. Are you trying to use it in place of User?

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/Users/lukas/Projects/jac/backend/spa/tests/test_settings_hardening.py", line 144, in test_schema_stays_public
    r = self.client.get("/api/schema/")
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/test/client.py", line 1127, in get
    response = super().get(
               ^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/test/client.py", line 475, in get
    return self.generic(
           ^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/test/client.py", line 671, in generic
    return self.request(**r)
           ^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/test/client.py", line 1090, in request
    self.check_exception(response)
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/test/client.py", line 805, in check_exception
    raise exc_value
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/core/handlers/exception.py", line 55, in inner
    response = get_response(request)
               ^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/core/handlers/base.py", line 198, in _get_response
    response = wrapped_callback(request, *callback_args, **callback_kwargs)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/views/decorators/csrf.py", line 65, in _view_wrapper
    return view_func(request, *args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/views/generic/base.py", line 106, in view
    return self.dispatch(request, *args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/rest_framework/views.py", line 515, in dispatch
    response = self.handle_exception(exc)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/rest_framework/views.py", line 475, in handle_exception
    self.raise_uncaught_exception(exc)
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/rest_framework/views.py", line 486, in raise_uncaught_exception
    raise exc
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/rest_framework/views.py", line 512, in dispatch
    response = handler(request, *args, **kwargs)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/drf_spectacular/views.py", line 84, in get
    return self._get_schema_response(request)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/drf_spectacular/views.py", line 92, in _get_schema_response
    data=generator.get_schema(request=request, public=self.serve_public),
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/drf_spectacular/generators.py", line 287, in get_schema
    paths=self.parse(request, public),
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/drf_spectacular/generators.py", line 258, in parse
    operation = view.schema.get_operation(
                ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/drf_spectacular/openapi.py", line 113, in get_operation
    operation['responses'] = self._get_response_bodies()
                             ^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/drf_spectacular/openapi.py", line 1449, in _get_response_bodies
    return {'200': self._get_response_for_code(response_serializers, '200', direction=direction)}
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/drf_spectacular/openapi.py", line 1505, in _get_response_for_code
    component = self.resolve_serializer(serializer, direction)
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/drf_spectacular/openapi.py", line 1700, in resolve_serializer
    component.schema = self._map_serializer(serializer, direction, bypass_extensions)
                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/drf_spectacular/openapi.py", line 990, in _map_serializer
    schema = self._map_basic_serializer(serializer, direction)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/drf_spectacular/openapi.py", line 1083, in _map_basic_serializer
    for field in serializer.fields.values():
                 ^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/utils/functional.py", line 47, in __get__
    res = instance.__dict__[self.name] = self.func(instance)
                                         ^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/rest_framework/serializers.py", line 386, in fields
    for key, value in self.get_fields().items():
                      ^^^^^^^^^^^^^^^^^
  File "/Users/lukas/Projects/jac/backend/spa/serializers.py", line 242, in get_fields
    fields["domains"].child_relation.queryset = Domain.objects.for_user(
                                                ^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/Projects/jac/backend/lukehirsch/managers.py", line 19, in for_user
    return self.filter(
           ^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/models/manager.py", line 87, in manager_method
    return getattr(self.get_queryset(), name)(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/models/query.py", line 1542, in filter
    return self._filter_or_exclude(False, args, kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/models/query.py", line 1560, in _filter_or_exclude
    clone._filter_or_exclude_inplace(negate, args, kwargs)
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/models/query.py", line 1570, in _filter_or_exclude_inplace
    self._query.add_q(Q(*args, **kwargs))
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/models/sql/query.py", line 1676, in add_q
    clause, _ = self._add_q(q_object, can_reuse)
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/models/sql/query.py", line 1708, in _add_q
    child_clause, needed_inner = self.build_filter(
                                 ^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/models/sql/query.py", line 1533, in build_filter
    return self._add_q(
           ^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/models/sql/query.py", line 1708, in _add_q
    child_clause, needed_inner = self.build_filter(
                                 ^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/models/sql/query.py", line 1618, in build_filter
    condition = self.build_lookup(lookups, col, value)
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/models/sql/query.py", line 1445, in build_lookup
    lookup = lookup_class(lhs, rhs)
             ^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/models/lookups.py", line 35, in __init__
    self.rhs = self.get_prep_lookup()
               ^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/models/fields/related_lookups.py", line 113, in get_prep_lookup
    self.rhs = target_field.get_prep_value(self.rhs)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/lukas/.pyenv/versions/jac/lib/python3.12/site-packages/django/db/models/fields/__init__.py", line 2130, in get_prep_value
    raise e.__class__(
TypeError: Field 'id' expected a number but got <django.contrib.auth.models.AnonymousUser object at 0x117199070>.

======================================================================
FAIL: test_landing_renders_owner_html (spa.tests.test_portfolio.LandingTests.test_landing_renders_owner_html)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/lukas/Projects/jac/backend/spa/tests/test_portfolio.py", line 292, in test_landing_renders_owner_html
    self.assertIn("text/html", r["Content-Type"])
AssertionError: 'text/html' not found in 'application/json'

======================================================================
FAIL: test_index_stays_public (spa.tests.test_settings_hardening.DrfDefaultPermissionTests.test_index_stays_public)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/lukas/Projects/jac/backend/spa/tests/test_settings_hardening.py", line 135, in test_index_stays_public
    self.assertIn("text/html", r["Content-Type"])
AssertionError: 'text/html' not found in 'application/json'

----------------------------------------------------------------------
Ran 351 tests in 239.771s

FAILED (failures=2, errors=4)
Destroying test database for alias 'default'...
```
