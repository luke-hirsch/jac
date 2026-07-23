# [backend] portfolio public API — resolve / native / rank

> **Portfolio phase, guide 2 of 5.** Roadmap: #1 portfolio generator (plan:
> `~/.claude/plans/fizzy-cooking-sparrow.md`). Requires guide 1 (`[backend]-portfolio-models`)
> merged. Queued behind the active SPA-phase stack.
>
> **Step 0 — activation pass (AI):** cut branch `backend/portfolio-public-api` off `main`,
> re-verify anchors, land the red tests listed in **Tests**.

## Context / goal

The anonymous read path: resolve a personalised slug to a fully **server-joined, redacted**
payload; serve the native (questionnaire-driven) view for the configured site owner; and the one
compute-bearing anonymous endpoint — the **embed finale** that cosine-ranks the owner's entries
against a visitor's free-text interest on the tower (HirschAI). This is the first genuinely
public API surface beyond the root `IndexView`, so it also introduces **DRF throttling** (none
exists today) and the noindex belt.

Security posture (see CLAUDE.md): public by explicit `AllowAny` opt-in (the `IndexView` pattern,
`lukehirsch/urls.py:11-17`), never by omission. **No generative LLM anywhere in the anonymous
path** — the rank endpoint is embeddings only, hard-routed to the tower by
`llm_connector.embed()` itself (`llm_connector/__init__.py:48-52`), so the never-commercial
guarantee is structural, not conventional.

Design points:

- **Payloads are hand-assembled allowlist dicts** in `spa/portfolio.py` (`build_payload`), not
  ModelSerializers — redaction by construction (a field absent from the builder cannot leak),
  and the assembly is unit-testable without HTTP. The existing spa serializers leak
  email/phone/address and must never serve anonymous traffic.
- **Revoked ≡ missing**: one filtered lookup (`revoked_at__isnull=True`) produces
  byte-identical 404s for both.
- **Native flow is stateless** — scope arrives as query params; bots create zero rows.
- **Owner resolution is a setting** (`PORTFOLIO_OWNER_USERNAME`), never request-derived and
  never the system sentinel; unset → native endpoints 404 and the SPA keeps its static welcome.

## Affected files

| file | why |
| --- | --- |
| `backend/lukehirsch/settings.py` | + `PORTFOLIO_OWNER_USERNAME`, + `DEFAULT_THROTTLE_RATES` |
| `backend/spa/portfolio.py` | + owner resolution, payload assembly (item builders, explore-more, lucky), visit bump, `PortfolioEmbed` + `rank_for_query` |
| `backend/spa/serializers.py` | + `PortfolioRankSerializer` (plain input serializer) |
| `backend/spa/views.py` | + `PublicPortfolioAPIView` base + resolve / native / rank views |
| `backend/spa/urls.py` | + the three public paths |
| `config/nginx.conf` | documentation-only snippet (media + noindex belt) — see Verification notes |

## The code

### 1. `backend/lukehirsch/settings.py`

Next to `SYSTEM_USER_USERNAME` (L211):

```python
# The public portfolio's owner (native "/" flow + questionnaire target). Explicit —
# never derived from a request, never the system sentinel. Empty = native flow off.
PORTFOLIO_OWNER_USERNAME = os.getenv("PORTFOLIO_OWNER_USERNAME", "")
```

Inside `REST_FRAMEWORK` (L222-237) — rates only, **no** `DEFAULT_THROTTLE_CLASSES`: throttling
stays opt-in per view, mirroring the AllowAny opt-in philosophy (authenticated endpoints remain
un-throttled):

```python
    "DEFAULT_THROTTLE_RATES": {
        # Anonymous portfolio reads — generous for humans, hostile to enumeration
        # (31^4 slug suffixes ÷ 60/h ≈ years).
        "portfolio": "60/hour",
        # The embed finale — the only anonymous endpoint costing tower compute.
        "portfolio-rank": "6/hour",
    },
```

Two throttle-fidelity facts to keep in mind (assert the rates in the hardening tests; the rest is
deploy-time): in `DEBUG` the default cache is per-process LocMemCache — fine for dev; **prod
`CACHES` already points at Redis** (settings.py:133-142), so counters are shared across workers.
When nginx fronts the app, add `"NUM_PROXIES": 1` to `REST_FRAMEWORK` — DRF's anon ident honours
`X-Forwarded-For`, which is spoofable until DRF knows how many proxies to trust.

### 2. `backend/spa/portfolio.py` — public read logic

Extend the module imports:

```python
import random

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import F
from django.utils import timezone

from jac.llm_prompts import Embed  # no spa import in jac.llm_prompts — cycle-safe

from spa.models import PortfolioBlock, PortfolioLink, PortfolioVisit
```

Append:

```python
def get_owner() -> User | None:
    """The public portfolio's owner — an explicit setting, never request-derived and
    never the system sentinel. None (unset / unknown / inactive) disables the native
    flow: callers 404."""
    username = settings.PORTFOLIO_OWNER_USERNAME
    if not username or username == settings.SYSTEM_USER_USERNAME:
        return None
    return User.objects.filter(username=username, is_active=True).first()


# ── payload assembly ──────────────────────────────────────────────────────────
# Hand-built allowlist dicts, deliberately not ModelSerializers: a field absent from
# a builder cannot leak. Never add email / phone / street / zip / notes / status /
# letter fields here — entry `description` is owner-authored CV text, public by intent.

_SECTION_ORDER = ["job", "project", "skill", "education", "certification", "language"]


def _career_models() -> dict:
    from jac import models as jac_models  # runtime-only; spa models use string refs

    return {
        "skill": jac_models.Skill,
        "job": jac_models.Job,
        "education": jac_models.Education,
        "certification": jac_models.Certification,
        "project": jac_models.Project,
        "language": jac_models.Language,
    }


def _career_item(type_name: str, obj) -> dict:
    """One public item. Dates stay `date` objects — DRF's encoder ISO-formats them."""
    item = {
        "id": f"{type_name}:{obj.pk}",
        "type": type_name,
        "title": "",
        "subtitle": "",
        "description": obj.description or "",
        "started": None,
        "ended": None,
        "url": "",
        "domains": [],
    }
    if type_name != "language":
        item["domains"] = sorted(d.name for d in obj.domains.all())
    if type_name == "skill":
        item.update(title=obj.name, subtitle=obj.get_proficiency_display())
    elif type_name == "job":
        item.update(
            title=obj.title,
            subtitle=obj.company,
            started=obj.started,
            ended=obj.ended,
            url=obj.url,
        )
    elif type_name == "project":
        item.update(
            title=obj.name,
            subtitle=obj.job.company if obj.job else "",
            started=obj.started,
            ended=obj.ended,
            url=obj.url,
        )
    elif type_name == "education":
        item.update(
            title=obj.degree or obj.field_of_study,
            subtitle=obj.institution,
            started=obj.started,
            ended=obj.ended,
        )
    elif type_name == "certification":
        item.update(title=obj.name, subtitle=obj.issuer, started=obj.issued_on, url=obj.url)
    elif type_name == "language":
        item.update(title=obj.name, subtitle=obj.get_fluency_display())
    return item


def _block_item(block: PortfolioBlock) -> dict:
    return {
        "id": f"block:{block.pk}",
        "type": "block",
        "kind": block.kind,
        "title": block.title,
        "body": block.body,
        # MEDIA_URL-relative; dev Vite proxies /media, prod nginx must serve it.
        "image_url": block.image.url if block.image else None,
        "alt_text": block.alt_text,
        "domains": sorted(d.name for d in block.domains.all()),
    }


def _matched_domains(owner, names: list[str]) -> list:
    """Case-insensitive join of requested names against the owner's visible taxonomy —
    forgiving of drift between the frontend questionnaire constants and the DB tags."""
    from jac.models import Domain

    lowered = {n.strip().lower() for n in names if n and n.strip()}
    if not lowered:
        return []
    return [d for d in Domain.objects.for_user(owner) if d.name.lower() in lowered]


def resolve_items(owner, ids: list[str]) -> list[dict]:
    """Join featured ids against the live DB, preserving order. Dead / foreign /
    malformed ids drop silently (the cv-doc philosophy: selection frozen, text live)."""
    wanted: dict[str, list[int]] = {}
    for i in ids:
        t, _, pk = i.partition(":")
        if pk.isdigit():
            wanted.setdefault(t, []).append(int(pk))
    found: dict[str, dict] = {}
    models_map = _career_models()
    for t, pks in wanted.items():
        if t == "block":
            rows = PortfolioBlock.objects.filter(
                user=owner, is_active=True, pk__in=pks
            ).prefetch_related("domains")
            found.update({f"block:{b.pk}": _block_item(b) for b in rows})
        elif t in models_map:
            qs = models_map[t].objects.filter(user=owner, pk__in=pks)
            if t != "language":
                qs = qs.prefetch_related("domains")
            found.update({f"{t}:{o.pk}": _career_item(t, o) for o in qs})
    return [found[i] for i in ids if i in found]


def _entries(owner, *, domains=None, favourite=False, exclude_ids=frozenset()) -> list[dict]:
    """Career items in section order; `domains` (Domain rows) scopes, `favourite`
    restricts. Language has no domains M2M — it drops out of any domain-scoped view."""
    out = []
    for t, model in ((t, _career_models()[t]) for t in _SECTION_ORDER):
        qs = model.objects.filter(user=owner)
        if favourite:
            qs = qs.filter(favourite=True)
        if domains is not None:
            if t == "language":
                continue
            qs = qs.filter(domains__in=domains).distinct()
        if t != "language":
            qs = qs.prefetch_related("domains")
        out += [
            _career_item(t, o) for o in qs if f"{t}:{o.pk}" not in exclude_ids
        ]
    return out


def _blocks(owner, *, domains=None, favourite=False, exclude_ids=frozenset()) -> list[dict]:
    qs = PortfolioBlock.objects.filter(user=owner, is_active=True).prefetch_related(
        "domains"
    )
    if favourite:
        qs = qs.filter(favourite=True)
    if domains is not None:
        qs = qs.filter(domains__in=domains).distinct()
    return [_block_item(b) for b in qs if f"block:{b.pk}" not in exclude_ids]


def _owner_block(owner) -> dict:
    """Public identity card. Socials only when the profile opts in (`show_socials`,
    spa/models.py:57); never email / phone / postal address."""
    profile = owner.profile
    full = f"{owner.first_name} {owner.last_name}".strip()
    block = {
        "display_name": profile.display_name or full or owner.username,
        "bio": profile.bio,
        "avatar_url": profile.avatar.url if profile.avatar else None,
    }
    if profile.show_socials:
        block.update(
            website=profile.website,
            linkedin_url=profile.linkedin_url,
            github_url=profile.github_url,
        )
    return block


def build_payload(
    owner, *, link=None, domains=None, lucky=False, seed=None
) -> dict:
    """The whole public page in one dict: `{owner, kind, title, intro, featured, more}`.

    Link mode: featured = the link's frozen ids (application links fall back to the
    live `cv_content` while un-frozen — accurate preview before `sent`); `more` = the
    link's domain scope, or favourites + blocks when unscoped; `hide_explore` empties it.
    Native mode: `domains` (names) scope both lists; no/unknown domains = the full
    portfolio (favourites featured). `lucky` = favourites featured + a seeded random
    tasting menu (fresh serendipity per request; `seed` keeps tests deterministic).
    Position IS the featured signal — the favourite flag itself never leaks.
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
        pool = _entries(owner, exclude_ids=exclude) + _blocks(owner, exclude_ids=exclude)
        rng = random.Random(seed)
        more = rng.sample(pool, min(10, len(pool)))
    else:
        matched = _matched_domains(owner, domains or [])
        scope = matched or None  # nothing matched → the full portfolio
        featured = _entries(owner, domains=scope, favourite=True)
        featured += _blocks(owner, domains=scope, favourite=True)
        exclude = {i["id"] for i in featured}
        more = _entries(owner, domains=scope, exclude_ids=exclude)
        more += _blocks(owner, domains=scope, exclude_ids=exclude)
    return {
        "kind": "native",
        "title": "",
        "intro": "",
        "owner": _owner_block(owner),
        "featured": featured,
        "more": more,
    }


def bump_visit(link: PortfolioLink) -> None:
    """Race-safe daily bump: get_or_create the bucket, then an F() increment."""
    row, _ = PortfolioVisit.objects.get_or_create(
        link=link, day=timezone.localdate()
    )
    PortfolioVisit.objects.filter(pk=row.pk).update(count=F("count") + 1)


# ── embed finale ──────────────────────────────────────────────────────────────


class PortfolioEmbed(Embed):
    """Visitor free-text interest → entry ranking. Tower-only by construction:
    `llm_connector.embed` hard-routes to HirschAI (never a commercial key), and there
    is no generative call anywhere in the anonymous path. The SnippetEmbed pattern
    (jac/llm_prompts.py:126-134)."""

    _EMBED_INSTRUCT = (
        "Given a visitor's stated interest, retrieve the portfolio entries most "
        "relevant to it."
    )
    DOC_KIND = "portfolio"


MAX_RANK_DOCS = 200  # caps one rank call's tower work


def rank_for_query(owner, query: str, domains: list[str]) -> list[dict]:
    """Cosine-rank the owner's entries + blocks against `query`. `user=None` keeps the
    Qdrant store path off (no "portfolio" doc kind yet — classic per-call embed;
    store-backed is a later optimisation, see jac/vectors.py)."""
    from jac.cv import CV

    matched = _matched_domains(owner, domains)
    cv = CV(owner.pk, domains=[d.name for d in matched] or None)
    # Deliberate reuse of the pipeline's flattener (private by name only); promote it
    # to a public method if a third caller appears.
    docs = [{"id": e["id"], "text": e["text"]} for e in cv._flatten_entries()]
    blocks = PortfolioBlock.objects.filter(user=owner, is_active=True)
    if matched:
        blocks = blocks.filter(domains__in=matched).distinct()
    docs += [
        {"id": f"block:{b.pk}", "text": f"{b.title}\n{b.body}".strip()}
        for b in blocks
    ]
    docs = docs[:MAX_RANK_DOCS]
    if not docs:
        return []
    ranked = PortfolioEmbed(query, docs, user=None).ranked_entries()
    return [{"id": r["id"], "score": round(r["score"], 4)} for r in ranked]
```

### 3. `backend/spa/serializers.py` — rank input

```python
class PortfolioRankSerializer(serializers.Serializer):
    """Input caps for the embed finale — one-tweet query (the questionnaire's own
    MAX_ANSWER_LEN), at most 10 domain names."""

    query = serializers.CharField(max_length=MAX_ANSWER_LEN)
    domains = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, max_length=10
    )
```

(`MAX_ANSWER_LEN` is already imported at the top of the file.)

### 4. `backend/spa/views.py` — public views

Extend the imports:

```python
from django.http import Http404
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle

from spa.portfolio import bump_visit, build_payload, get_owner, rank_for_query
from spa.serializers import PortfolioRankSerializer
```

Append:

```python
class PublicPortfolioAPIView(APIView):
    """Base for the anonymous portfolio endpoints: explicit AllowAny (the IndexView
    pattern — public by opt-in, never by omission), scoped throttling, and an
    X-Robots-Tag so the API URLs themselves never get indexed."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "portfolio"

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["X-Robots-Tag"] = "noindex"
        return response


class PortfolioResolveView(PublicPortfolioAPIView):
    """GET: personalised slug → payload. Revoked ≡ missing — the single filtered
    lookup yields identical 404s. Counts the visit unless the owner previews their
    own link."""

    def get(self, request, slug):
        link = get_object_or_404(
            PortfolioLink.objects.filter(revoked_at__isnull=True), slug=slug
        )
        if request.user.pk != link.user_id:
            bump_visit(link)
        return Response(build_payload(link.user, link=link))


class PortfolioNativeView(PublicPortfolioAPIView):
    """GET: the native (questionnaire-driven) view of the configured owner's
    portfolio. `?domains=a,b` scopes; `?lucky=1` = favourites + a random tasting
    menu. Stateless — bots create zero rows."""

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
        return Response(build_payload(owner, domains=domains, lucky=lucky))


class PortfolioRankView(PublicPortfolioAPIView):
    """POST: the embed finale. The tight `portfolio-rank` scope is the abuse valve —
    this is the only anonymous endpoint that costs tower compute."""

    throttle_scope = "portfolio-rank"

    def post(self, request):
        owner = get_owner()
        if owner is None:
            raise Http404
        ser = PortfolioRankSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ranked = rank_for_query(
            owner,
            ser.validated_data["query"],
            ser.validated_data.get("domains") or [],
        )
        return Response({"ranked": ranked})
```

### 5. `backend/spa/urls.py`

```python
    path(
        "portfolio/links/<slug:slug>/",
        PortfolioResolveView.as_view(),
        name="portfolio-resolve",
    ),
    path(
        "portfolio/native/",
        PortfolioNativeView.as_view(),
        name="portfolio-native",
    ),
    path(
        "portfolio/native/rank/",
        PortfolioRankView.as_view(),
        name="portfolio-rank",
    ),
```

(The `portfolio/manage/…` paths from guide 1 don't collide — distinct prefix.)

### 6. `config/nginx.conf` — documentation only (prod gap, tracked)

The file is currently empty; prod serving is unconfigured. When it gets written, the portfolio
needs these two blocks — recorded here so they aren't forgotten:

```nginx
# media (block images + avatars) — Django only serves media in DEBUG
location /media/ { alias /path/to/backend/media/; }

# personalised pages: noindex belt (the SPA meta tag is the primary signal; do NOT
# robots.txt-Disallow /portfolio/ — a crawler that can't fetch can't see the noindex)
location /portfolio/ {
    add_header X-Robots-Tag "noindex" always;
    try_files $uri /index.html;
}
```

## Tests

Landed **red at activation** (step 0). Distribution:

- `backend/spa/tests/test_portfolio.py` (topic file from guide 1) — payload assembly:
  **redaction sweep** (recursive key-scan of a full payload: `email`, `phone`, `street`, `zip`,
  `notes`, `favourite` never appear); socials present iff `show_socials`; application link
  falls back to live `cv_content` before freeze and uses frozen `featured` after;
  `hide_explore`; explore-more excludes featured ids; domain matching is case-insensitive and
  unknown names → full-portfolio fallback; `lucky` with a fixed `seed` is deterministic and
  ≤10 `more` items; `resolve_items` drops dead/foreign/malformed ids but preserves order;
  visit bump creates one row per day and increments (owner request skipped); rank endpoint with
  `llm_connector.embed` **mocked** (never a live tower call in tests: doc cap at
  `MAX_RANK_DOCS`, block texts included, response shape `{ranked:[{id,score}]}`); query >280
  and >10 domains → 400; unset `PORTFOLIO_OWNER_USERNAME` → native + rank 404.
- `backend/spa/tests/test_settings_hardening.py` — additions: `DEFAULT_THROTTLE_RATES` contains
  exactly the two scopes with the expected rates; the three public paths respond to anonymous
  requests (the AllowAny audit — resolve via a fixture link, native via a configured owner);
  revoked and missing slugs return **equal** status + body; `X-Robots-Tag: noindex` present on
  public portfolio responses and absent on `/`.

Throttle-behaviour note: DRF throttling with LocMemCache is awkward to assert directly (shared
state between tests); the hardening tests pin the **configuration**, not 429 behaviour — the 429
path is a manual verification step below.

Run: `cd backend && python manage.py test spa`

## Verification

1. `PORTFOLIO_OWNER_USERNAME=<your username>` in `backend/.env`; restart runserver.
2. Logged **out** (fresh private window):
   - `curl -i localhost:8000/api/spa/portfolio/native/` → 200, payload with your favourites
     featured; `X-Robots-Tag: noindex` header present.
   - `curl "localhost:8000/api/spa/portfolio/native/?domains=software%20development"` → scoped.
   - `curl "localhost:8000/api/spa/portfolio/native/?lucky=1"` twice → different `more` samples.
   - `curl -i localhost:8000/api/spa/portfolio/links/<slug>/` (a link from guide 1) → 200; the
     matching `PortfolioVisit` row incremented. Revoke it → 404 identical to a garbage slug.
   - Grep the JSON: no email/phone/street/zip anywhere.
3. Rank (tower must be up): `curl -X POST localhost:8000/api/spa/portfolio/native/rank/ -H
   'Content-Type: application/json' -d '{"query": "embedded rust on bicycles"}'` → ranked ids;
   7th call within the hour → **429**.
4. `python manage.py test spa` — green wall of dots.

## Results

_(human fills after testing)_
