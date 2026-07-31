# [backend] portfolio models — links, blocks, visits + application wiring

> **Portfolio phase, guide 1 of 5.** Roadmap: #1 portfolio generator (plan:
> `~/.claude/plans/fizzy-cooking-sparrow.md`, approved 2026-07-19). Queued **behind the active
> SPA-phase stack** — do not start while `[fullstack]-llm-config-rework` … `[fullstack]-chat-assistant-rework`
> are open.
>
> **Step 0 — activation pass (AI, when this guide goes active):** cut branch
> `backend/portfolio-models` off `main`, re-verify every file:line anchor below against the
> post-SPA-stack code, and land the red tests listed in **Tests**. Only then does Lukas type.

## Context / goal

The public portfolio needs its data spine: personalised links (`/portfolio/<slug>`), owner-authored
content blocks the career DB can't hold (text groups, images), and a privacy-light visit counter.
This guide lands the three models, the domain-logic module `spa/portfolio.py` (slug minting,
application-link lifecycle, freeze-at-sent), owner-side manage CRUD, and the jac wiring: a
get-or-create link endpoint on applications plus the freeze hook in the `sent` transition.

After this guide the owner can author blocks/links via Django admin and mint an application link
via the API. The **public** read path (resolve/native/rank) is guide 2; the SPA is guides 3–5.

Decisions this code embodies (from the approved plan):

- **Revocation is a timestamp**, not a boolean/delete: the public queryset filters
  `revoked_at__isnull=True`, so revoked and never-existed slugs 404 identically, and
  revoke-and-regenerate keeps history. A **partial unique constraint** allows exactly one *active*
  link per application.
- **Application slugs are readable + entropy** (`acme-corp-x7f3`): 4 chars over a confusable-free
  alphabet — obscurity against enumeration (backed by guide 2's 60/h throttle), not auth.
- **Freeze-at-sent**: while draft/approved the public page will render the live
  `application.cv_content`; the `sent` transition snapshots the kept ids into
  `link.content["featured"]` so the page forever matches the PDF in the recruiter's hand.
- **Lazy link creation** (get-or-create from the export card, guide 4) — auto-runs that never
  export create no rows.
- Id grammar extends the existing one: `"block:<pk>"` beside `"job:12"` (`jac/generation_result.py:68`).

## Affected files

| file | why |
| --- | --- |
| `backend/spa/models.py` | + `PortfolioBlock`, `PortfolioLink`, `PortfolioVisit` (the promised "portfolio link models", docstring L1) |
| `backend/spa/portfolio.py` | **new** — slug minting, `link_for_application`, `freeze_link`; views stay thin, logic unit-testable without HTTP |
| `backend/spa/serializers.py` | + `PortfolioBlockSerializer`, `PortfolioLinkSerializer` (owner-side; content-JSON validation) |
| `backend/spa/views.py` | + manage CRUD views (generics, matching the file's idiom) + revoke |
| `backend/spa/urls.py` | + `portfolio/manage/…` paths |
| `backend/spa/admin.py` | register the three models — admin is the authoring surface until guide 5 |
| `backend/jac/views.py` | + `portfolio-link` action on `JobApplicationViewSet`; freeze hook in `transition` (L534-553) |
| `backend/spa/migrations/0002_*.py` | generated — `python manage.py makemigrations spa` |

Import direction note: jac→spa is the established runtime dependency (`jac/cover_letter.py:599`
imports spa lazily; `lukehirsch/managers.py` docstring). The models below reference jac only via
**string refs** (`"jac.Domain"`, `"jac.JobApplication"`); `spa/portfolio.py` touches jac models only
through the `application` instance handed to it, so no spa→jac module import exists at all in this
guide. `jac/views.py` importing `spa.portfolio`/`spa.serializers` at module level is safe:
those import only spa + django.

## The code

### 1. `backend/spa/models.py` — three new models

Add to the imports at the top (`Path`, `models`, `timezone`, `_` are already there):

```python
from django.core.exceptions import ValidationError
from django.db.models import Q
```

Append after `PersonalityQuestion`:

```python
def _block_image_path(instance, filename):
    """Namespace block images per owner; Django suffixes duplicates itself."""
    return f"portfolio/blocks/{instance.user_id}/{Path(filename).name}"


class PortfolioBlock(models.Model):
    """Owner-authored portfolio content the career DB can't hold: a markdown text
    group or a captioned image. Domain-taggable so every filtering axis that applies
    to career entries applies to blocks too; `favourite` = featured-by-default (same
    axis as `CvEntry.favourite`, no cap — blocks are already hand-curated). Public id
    grammar: `block:<pk>` beside the career ids (`job:12`).
    """

    class Kind(models.TextChoices):
        text = "text", _("Text")
        image = "image", _("Image")

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="portfolio_blocks"
    )
    kind = models.CharField(max_length=5, choices=Kind, default=Kind.text)
    title = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)  # markdown for text blocks; caption for images
    image = models.ImageField(upload_to=_block_image_path, blank=True)
    alt_text = models.CharField(max_length=200, blank=True)
    domains = models.ManyToManyField(
        "jac.Domain", blank=True, related_name="portfolio_blocks"
    )
    favourite = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title or f"{self.kind} block {self.pk}"

    def clean(self):
        """A block must carry its kind's payload. DRF doesn't call full_clean — the
        serializer repeats this rule; this keeps admin/forms honest (the CvEntry pattern,
        jac/models.py:200)."""
        super().clean()
        if self.kind == self.Kind.image and not self.image:
            raise ValidationError({"image": "An image block needs an image."})
        if self.kind == self.Kind.text and not (self.body or "").strip():
            raise ValidationError({"body": "A text block needs a body."})


class PortfolioLink(models.Model):
    """A personalised public portfolio URL (`/portfolio/<slug>`).

    `manual` links are owner-named and hand-curated; `application` links are minted by
    jac via `spa.portfolio.link_for_application` (readable-plus-entropy slug) and render
    the application's tailored selection — live while draft, frozen at the `sent`
    transition (`spa.portfolio.freeze_link`). Revocation is a timestamp, not a delete:
    the public queryset filters `revoked_at__isnull=True` (revoked ≡ never-existed →
    identical 404s) and the partial unique constraint below allows one *active* link per
    application while keeping revoked history for regenerate.
    """

    class Kind(models.TextChoices):
        manual = "manual", _("Manual")
        application = "application", _("Application")

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="portfolio_links"
    )
    slug = models.SlugField(max_length=80, unique=True)
    kind = models.CharField(max_length=12, choices=Kind, default=Kind.manual)
    title = models.CharField(max_length=200, blank=True)
    intro = models.TextField(blank=True)
    application = models.ForeignKey(
        "jac.JobApplication",
        on_delete=models.SET_NULL,  # a frozen page survives application deletion
        null=True,
        blank=True,
        related_name="portfolio_links",
    )
    # {"featured": ["job:12", "block:7", …], "domains": [names], "hide_explore": bool}
    # — ids only, joined against the live career DB at render time (deleted rows drop
    # silently, the cv-doc philosophy). Application links keep featured empty until the
    # sent-freeze; the public view falls back to the live cv_content meanwhile.
    content = models.JSONField(default=dict, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["application"],
                condition=Q(revoked_at__isnull=True, application__isnull=False),
                name="one_active_link_per_application",
            )
        ]

    def __str__(self):
        state = ", revoked" if self.revoked_at else ""
        return f"/{self.slug} ({self.kind}{state})"

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def revoke(self) -> None:
        """Idempotent soft-kill: the public path 404s from the next request on."""
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at", "updated_at"])


class PortfolioVisit(models.Model):
    """Daily visit bucket per link — deliberately GDPR-light (no IP/UA/timestamps) and
    bounded at links × days. Bumped by the public resolve view (guide 2), which skips
    the owner's own previews. Native-flow traffic is deliberately untracked (bots)."""

    link = models.ForeignKey(
        PortfolioLink, on_delete=models.CASCADE, related_name="visits"
    )
    day = models.DateField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["link", "day"], name="one_visit_row_per_link_day"
            )
        ]

    def __str__(self):
        return f"{self.link.slug} {self.day}: {self.count}"
```

Then generate the migration (string refs auto-add the jac dependency):

```bash
cd backend && python manage.py makemigrations spa && python manage.py migrate
```

### 2. `backend/spa/portfolio.py` — new module

```python
"""spa.portfolio — portfolio-link domain logic.

Slug minting and the application-link lifecycle (get-or-create + freeze-at-sent).
Guide 2 adds owner resolution, payload assembly, and the embed ranking here. Views
stay thin; everything in this module is unit-testable without HTTP.
"""

import secrets

from django.db import IntegrityError, transaction
from django.utils.text import slugify

from spa.models import PortfolioLink

# Confusable-free suffix alphabet (no 0/o/1/l/i): 31^4 ≈ 9.2e5 combos. The suffix is
# obscurity against enumeration — backed by the public 60/h throttle (guide 2) — not
# authentication; the page content is redacted-public anyway.
SLUG_SUFFIX_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
SLUG_SUFFIX_LEN = 4


def slug_suffix() -> str:
    return "".join(
        secrets.choice(SLUG_SUFFIX_ALPHABET) for _ in range(SLUG_SUFFIX_LEN)
    )


def application_slug(application) -> str:
    """`<company>-<suffix>` — readable but not guessable.

    Company preference: the user-corrected letter recipient
    (`letter_meta["recipient"]["company"]`, jac/cover_letter.py:366-379) → the extracted
    posting address → the posting title → a plain fallback. `getattr` on the reverse
    one-to-one is safe: Django's RelatedObjectDoesNotExist subclasses AttributeError.
    """
    address = getattr(application.posting, "address", None)
    company = (
        ((application.letter_meta or {}).get("recipient") or {}).get("company")
        or (address.company if address else "")
        or application.posting.title
        or "application"
    )
    base = slugify(company)[:40].strip("-") or "application"
    return f"{base}-{slug_suffix()}"


def link_for_application(application) -> PortfolioLink:
    """The application's active portfolio link, minted on first request (idempotent).

    The loop re-checks + re-rolls: an IntegrityError is either a slug collision (re-roll
    the suffix) or a concurrent create tripping `one_active_link_per_application` (the
    re-check then returns the winner's row).
    """
    for _ in range(5):
        existing = application.portfolio_links.filter(
            revoked_at__isnull=True
        ).first()
        if existing:
            return existing
        try:
            with transaction.atomic():
                return PortfolioLink.objects.create(
                    user=application.user,
                    kind=PortfolioLink.Kind.application,
                    slug=application_slug(application),
                    application=application,
                    title=application.posting.title,
                )
        except IntegrityError:
            continue
    raise IntegrityError("could not mint a unique portfolio slug after 5 attempts")


def freeze_link(application) -> None:
    """Snapshot the sent selection into the application's active link (idempotent).

    Called from the `sent` transition: `featured` = the application's kept entry ids in
    cv_content section order (rows the editor flagged `deselected` drop out), `domains`
    = the latest done run's scope (empty → guide 2's explore-more falls back to
    favourites + blocks). A link that already carries a featured list is left alone, so
    re-entering sent-era statuses never rewrites the page the recruiter saw.
    """
    link = application.portfolio_links.filter(revoked_at__isnull=True).first()
    if link is None or link.content.get("featured"):
        return
    featured = [
        row["id"]
        for rows in (application.cv_content or {}).values()
        for row in rows
        if row.get("id") and not row.get("deselected")
    ]
    run = (  # status string == GenerationRun.Status.done — no jac import needed
        application.runs.filter(status="done").order_by("-created_at").first()
    )
    link.content = {
        **link.content,
        "featured": featured,
        "domains": list(run.domains) if run and run.domains else [],
    }
    link.save(update_fields=["content", "updated_at"])
```

### 3. `backend/spa/serializers.py` — owner-side serializers

Add to the imports:

```python
import re

from django.conf import settings
from django.db.models import Sum

from spa.models import PortfolioBlock, PortfolioLink
```

(extend the existing `from spa.models import …` line rather than adding a second one)

Append:

```python
FEATURED_ID_RE = re.compile(
    r"^(skill|job|education|certification|project|language|block):\d+$"
)


class PortfolioBlockSerializer(serializers.ModelSerializer):
    """Owner CRUD over portfolio blocks. `domains` accepts pks from the user's visible
    taxonomy (own + system defaults). Mirrors the model's kind↔payload rule because DRF
    never calls full_clean."""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = PortfolioBlock
        fields = (
            "id",
            "user",
            "kind",
            "title",
            "body",
            "image",
            "alt_text",
            "domains",
            "favourite",
            "order",
            "is_active",
            "updated_at",
        )
        read_only_fields = ("id", "updated_at")

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if request is not None:
            from jac.models import Domain  # runtime-only; models use string refs

            fields["domains"].child_relation.queryset = Domain.objects.for_user(
                request.user
            )
        return fields

    def validate(self, attrs):
        kind = attrs.get("kind", getattr(self.instance, "kind", ""))
        image = attrs.get("image", getattr(self.instance, "image", None))
        body = attrs.get("body", getattr(self.instance, "body", ""))
        if kind == PortfolioBlock.Kind.image and not image:
            raise serializers.ValidationError(
                {"image": "An image block needs an image."}
            )
        if kind == PortfolioBlock.Kind.text and not (body or "").strip():
            raise serializers.ValidationError({"body": "A text block needs a body."})
        return attrs


class PortfolioLinkSerializer(serializers.ModelSerializer):
    """Owner-side link CRUD. Manage-created links are always `manual` (kind/application
    are read-only — application links come from jac's portfolio-link action); `url` is
    the absolute public URL the QR encodes, built from FRONTEND_URL so the frontend
    never hardcodes the domain."""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    url = serializers.SerializerMethodField()
    visits = serializers.SerializerMethodField()

    class Meta:
        model = PortfolioLink
        fields = (
            "id",
            "user",
            "slug",
            "kind",
            "title",
            "intro",
            "application",
            "content",
            "revoked_at",
            "url",
            "visits",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "kind",
            "application",
            "revoked_at",
            "created_at",
            "updated_at",
        )

    def get_url(self, obj) -> str:
        return f"{settings.FRONTEND_URL}/portfolio/{obj.slug}"

    def get_visits(self, obj) -> int:
        # Small owner lists — the per-row aggregate is fine; revisit if links grow.
        return obj.visits.aggregate(total=Sum("count"))["total"] or 0

    def validate_slug(self, value):
        slug = slugify(value)[:80]
        if not slug:
            raise serializers.ValidationError("Slug can't be empty.")
        return slug

    def validate_content(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Expected an object.")
        featured = value.get("featured", [])
        domains = value.get("domains", [])
        if not isinstance(featured, list) or not all(
            isinstance(i, str) and FEATURED_ID_RE.match(i) for i in featured
        ):
            raise serializers.ValidationError(
                "featured must be a list of '<type>:<pk>' ids."
            )
        if not isinstance(domains, list) or not all(
            isinstance(d, str) and d.strip() for d in domains
        ):
            raise serializers.ValidationError("domains must be a list of names.")
        return {
            "featured": featured,
            "domains": domains,
            "hide_explore": bool(value.get("hide_explore", False)),
        }
```

Subtle: `validate_content` **normalises** — unknown keys are dropped, the three known keys always
present. Frozen application links are still PATCHable by the owner (title/intro/content) — freezing
guards against *pipeline* rewrites, not owner edits.

### 4. `backend/spa/views.py` — manage views

Extend the imports:

```python
from django.shortcuts import get_object_or_404

from spa.models import PortfolioBlock, PortfolioLink
from spa.serializers import PortfolioBlockSerializer, PortfolioLinkSerializer
```

(fold the model/serializer names into the existing import blocks)

Append:

```python
class PortfolioBlockListCreateView(generics.ListCreateAPIView):
    """Owner CRUD over portfolio blocks. Small list — pagination off, like the
    personality questions. Multipart for the image field (the avatar-upload pattern)."""

    serializer_class = PortfolioBlockSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return PortfolioBlock.objects.filter(
            user=self.request.user
        ).prefetch_related("domains")


class PortfolioBlockDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PortfolioBlockSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PortfolioBlock.objects.filter(user=self.request.user)


class PortfolioLinkListCreateView(generics.ListCreateAPIView):
    """List every link — manual and application, revoked included (the owner sees
    history); POST creates a manual link (kind is read-only, application links come
    from jac's portfolio-link action)."""

    serializer_class = PortfolioLinkSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return (
            PortfolioLink.objects.filter(user=self.request.user)
            .select_related("application")
            .order_by("-created_at")
        )


class PortfolioLinkDetailView(generics.RetrieveUpdateDestroyAPIView):
    """PATCH title/intro/content/slug; DELETE hard-removes (revoke is the soft path —
    prefer it for anything ever sent out)."""

    serializer_class = PortfolioLinkSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PortfolioLink.objects.filter(user=self.request.user)


class PortfolioLinkRevokeView(APIView):
    """POST: soft-kill a link — public 404 from the next request on. Idempotent."""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        link = get_object_or_404(PortfolioLink, pk=pk, user=request.user)
        link.revoke()
        return Response(
            PortfolioLinkSerializer(link, context={"request": request}).data
        )
```

### 5. `backend/spa/urls.py`

Extend the view import and append paths:

```python
    path(
        "portfolio/manage/blocks/",
        PortfolioBlockListCreateView.as_view(),
        name="portfolio-blocks",
    ),
    path(
        "portfolio/manage/blocks/<int:pk>/",
        PortfolioBlockDetailView.as_view(),
        name="portfolio-block-detail",
    ),
    path(
        "portfolio/manage/links/",
        PortfolioLinkListCreateView.as_view(),
        name="portfolio-links",
    ),
    path(
        "portfolio/manage/links/<int:pk>/",
        PortfolioLinkDetailView.as_view(),
        name="portfolio-link-detail",
    ),
    path(
        "portfolio/manage/links/<int:pk>/revoke/",
        PortfolioLinkRevokeView.as_view(),
        name="portfolio-link-revoke",
    ),
```

### 6. `backend/spa/admin.py`

Extend the import to include the three models, then append:

```python
@admin.register(PortfolioBlock)
class PortfolioBlockAdmin(admin.ModelAdmin):
    list_display = ["title", "kind", "user", "favourite", "order", "is_active"]
    list_filter = ["kind", "favourite", "is_active"]
    search_fields = ["title", "body"]
    raw_id_fields = ["user"]
    filter_horizontal = ["domains"]


@admin.register(PortfolioLink)
class PortfolioLinkAdmin(admin.ModelAdmin):
    list_display = ["slug", "kind", "user", "application", "revoked_at", "created_at"]
    list_filter = ["kind"]
    search_fields = ["slug", "title"]
    raw_id_fields = ["user", "application"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(PortfolioVisit)
class PortfolioVisitAdmin(admin.ModelAdmin):
    list_display = ["link", "day", "count"]
    ordering = ["-day"]
    raw_id_fields = ["link"]
```

### 7. `backend/jac/views.py` — application wiring

Module-level import (with the other app imports):

```python
from spa.portfolio import freeze_link, link_for_application
from spa.serializers import PortfolioLinkSerializer
```

New action on `JobApplicationViewSet` (beside `rewrite`/`chat`/`transition`):

```python
    @extend_schema(
        request=None,
        responses=OpenApiResponse(
            description="The application's active portfolio link (slug, url, …)"
        ),
    )
    @action(detail=True, methods=["post"], url_path="portfolio-link")
    def portfolio_link(self, request, pk=None):
        """Get-or-create the application's public portfolio link — idempotent; the
        export card calls this when the portfolio-QR toggle first goes on (guide 4).
        Lazy creation keeps auto-runs that never export from littering link rows."""
        application = self.get_object()
        link = link_for_application(application)
        return Response(
            PortfolioLinkSerializer(link, context={"request": request}).data
        )
```

Freeze hook — in the existing `transition` action (L534-553), after `application.save()` and
before the response:

```python
        application.save()
        if application.status == JobApplication.StatusChoices.sent:
            freeze_link(application)  # snapshot the sent selection; no-op without a link
        return Response(
            JobApplicationSerializer(application, context={"request": request}).data
        )
```

`apply_transition` itself stays untouched (its no-save contract, models.py:594-635) — the freeze is
a view-level side effect, exactly like the save.

## Tests

Landed **red at activation** (step 0), per the approved plan — the active SPA stack's red/skip
bookkeeping stays clean meanwhile. Distributed by topic (never a per-feature file across apps):

- `backend/spa/tests/test_portfolio.py` — **new topic file** (portfolio is a new spa topic):
  block kind↔payload validation (model `clean` + serializer); link slug slugify/uniqueness;
  `application_slug` shape (`base-xxxx`, confusable-free) + company preference order +
  collision re-roll (mock `slug_suffix`); `link_for_application` idempotence + concurrent-create
  IntegrityError recovery; `revoke()` idempotence; `one_active_link_per_application` (second
  active create → IntegrityError, revoke-then-create ok); manage CRUD ownership isolation
  (user B never sees/edits A's rows); `validate_content` grammar accept/reject + normalisation.
- `backend/jac/tests/test_api.py` — additions to the existing topic file: `POST
  /api/jac/applications/<pk>/portfolio-link/` (201-shape payload, idempotent second call returns
  the same slug, other user's application 404s); `transition` to `sent` freezes the active link
  (featured = non-deselected cv_content ids, domains from the latest done run) and is a no-op
  when no link exists / featured already set.

Run: `cd backend && python manage.py test spa.tests.test_portfolio jac.tests.test_api`

## Verification

1. `python manage.py makemigrations spa` — one migration, adds the jac dependency automatically;
   `python manage.py migrate` runs clean.
2. `python manage.py test spa jac` — the guide's red set goes green; a clean wall of dots.
3. Admin: create a text block + an image block (image required iff kind=image), a manual link.
4. API smoke (session-authenticated, e.g. via the SPA devtools console or `http`):
   - `POST /api/spa/portfolio/manage/links/` `{"slug": "For Grandma", "content": {"featured": []}}`
     → 201, slug `for-grandma`; second POST with the same slug → 400 unique.
   - `POST /api/jac/applications/<pk>/portfolio-link/` twice → same slug both times, shape
     `<company>-<4 chars>`.
   - `POST .../manage/links/<pk>/revoke/` → `revoked_at` set; repeat → unchanged.
   - Transition an application approved→sent → its link's `content.featured` now lists the kept
     entry ids.

## Results

_(human fills after testing)_
