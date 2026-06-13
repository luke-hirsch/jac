# Phase 3a setup guide — career-data model evolution (jac)

Goal: evolve the career model so the numbers and prose a recruiter actually reads are *trustworthy* before any generation is built on top of them. By the end of this phase a `Skill` can carry a hand-set `years_of_experience_override` that overrides the over-counting computed value, skills can be linked to one another via a symmetric `related_skills` M2M (so a posting that mentions *Accounting* can pull *SevDesk* along), and a new `ResumeSnippet` model holds reusable first-person prose the Phase 6 generator will stitch together. All three are reachable through `/api/jac/` and the Django admin.

This is **Phase 3a only** — pure backend (models, migration, serializers, one new viewset, admin, tests). It does **not** touch the frontend: surfacing the override in the skill editor, wiring a `related_skills` picker, and building a `/cv/snippets` page are a later frontend slice. It does **not** touch the CV pipeline ([backend/jac/cv.py](backend/jac/cv.py)) or generation (Phase 6) — `ResumeSnippet` ships with storage + CRUD only; nothing consumes it yet. Bulk endpoints, `ordering_fields` cleanup, throttles, and the SPA backend are **3b/3c/3d**, not here.

Run every command from `backend/` unless stated otherwise. If a step's "verify" check fails, stop and fix before moving on.

---

## 0. Preflight

Phase 2c must be committed and green (`3814ab3` on `main`). Confirm:

```bash
cd backend
git log --oneline -1            # expect 3814ab3 (or later) — Phase 2c shipped
python manage.py makemigrations --check --dry-run
# expect "No changes detected" — no uncommitted model drift before we start
python manage.py test
# expect "Ran 163 tests … OK"
```

Confirm the surface we're extending answers (log in via the SPA at least once so you have a session cookie, or use the Django shell):

```bash
python manage.py shell -c "from jac.models import Skill; print(Skill._meta.get_field('first_used'))"
# expect: jac.Skill.first_used  — proves you're pointed at the right model
```

If `makemigrations --check` reports pending changes, a previous phase left a migration uncommitted — resolve that first; never stack 3a on top of drift.

---

## 1. The contract you're coding against

Three model changes, each with a precise API consequence. Pin these before writing code.

### 1.1. `Skill.years_of_experience_override`

- New column: `years_of_experience_override = IntegerField(null=True, blank=True)`.
- The **`years_of_experience` property keeps its name and stays read-only in the API**, but now returns the override when set, else the existing computed delta. Callers (the serializer, the CV pipeline, `cv_test`) don't change — they read one effective number.
- Serializer exposes **both**: `years_of_experience` (read-only, effective) + `years_of_experience_override` (writable, nullable). A `PATCH {"years_of_experience_override": 3}` makes `years_of_experience` read `3`; `PATCH {"years_of_experience_override": null}` reverts to the computed value.

`GET /api/jac/skills/<id>/` after this phase:

```jsonc
{
  "id": 7,
  "name": "C/C++",
  "years_of_experience": 3,              // effective: override wins
  "years_of_experience_override": 3,       // the override itself (null when unset)
  "related_skills": [12, 19],            // new — see 1.2
  // …all existing fields unchanged…
}
```

### 1.2. `Skill.related_skills`

- Symmetric self-referential M2M: `related_skills = models.ManyToManyField("self", blank=True)`. Symmetric (the default for `"self"`) means adding 12 to skill 7's set adds 7 to skill 12's set automatically — no reverse accessor, no `through`.
- Serializer field: `PrimaryKeyRelatedField(many=True, required=False)`, queryset scoped to the request user's own skills (reuse the existing `ScopeRelatedToUserMixin` `user_scoped_fields` machinery). A user must not relate to another user's skill, and must not relate a skill to itself — validate both.

### 1.3. `ResumeSnippet` model

New `CvEntry`-free model (it has its own `title`/`content`/timestamps, doesn't need the abstract base's `description`/`updated_by`). Fields:

| Field | Type | Notes |
|-------|------|-------|
| `user` | FK `auth.User`, `related_name="snippets"` | user-scoped like everything else |
| `title` | `CharField(max_length=200)` | short label for the author |
| `content` | `TextField` | the first-person prose itself |
| `kind` | `CharField(choices=…)` | `intro` / `achievement` / `value_statement` / `closing` / `other` |
| `domains` | M2M `Domain`, `blank=True` | relevance matching at generation time |
| `skills` | M2M `Skill`, `blank=True` | relevance matching at generation time |
| `is_active` | `BooleanField(default=True)` | soft on/off without deleting |
| `created_at` / `updated_at` | `auto_now_add` / `auto_now` | |

New endpoint `GET/POST /api/jac/resume-snippets/`, paginated/filterable like the rest. `user` hidden + defaulted server-side. `domains` accepts the user's own + system-default domains (reuse `ScopeDomainsToUserMixin`); `skills` scoped to the user's own.

Spec mirror after migration: <http://localhost:8000/api/docs/> — the new `years_of_experience_override` / `related_skills` fields and the `snippets` resource appear automatically via drf-spectacular.

---

## 2. Stack additions

**None.** Everything here is stock Django + DRF already in the project. No new dependencies — if you reach for one, you're out of scope for 3a.

---

## 3. Models

[backend/jac/models.py](backend/jac/models.py). Three edits, smallest blast radius first.

### 3.1. `Skill` — the override field + the property change

Add the field alongside `first_used`:

```python
    first_used = models.DateField(null=True, blank=True)
    related_skills = models.ManyToManyField("self", blank=True)
    years_of_experience_override = models.IntegerField(null=True, blank=True)
    certification = models.ForeignKey(
        Certification, on_delete=models.SET_NULL, null=True, blank=True
    )
```

(`related_skills` is symmetric by default for a `"self"` M2M — don't pass `symmetrical=False`. That symmetry is exactly the "these go together" semantics the roadmap calls for; promoting to a `through` model with a relation-type is a deliberately deferred Phase-later move.)

Then make the property honour the override. Keep the existing computed logic as the fallback:

```python
    @property
    def years_of_experience(self) -> int | None:
        """Effective years of experience for this skill.

        Returns `years_of_experience_override` when the author has set it — the
        escape hatch for intermittent skills the automatic recogniser
        over-counts (e.g. a language picked up once at university but rarely
        used since). Otherwise falls back to the computed whole-year delta
        since the earliest evidence of use.

        The computed branch reads `_earliest_job_started` /
        `_earliest_project_started` annotated by `SkillManager`, so it issues
        zero extra queries — provided the instance came from `Skill.objects.…`.
        """
        if self.years_of_experience_override is not None:
            return self.years_of_experience_override
        earliest = _min_ignoring_none(
            self.first_used,
            getattr(self, "_earliest_job_started", None),
            getattr(self, "_earliest_project_started", None),
        )
        if earliest is None:
            return None
        return (timezone.localdate() - earliest).days // 365
```

### 3.2. `ResumeSnippet` — new model

Add at the end of [backend/jac/models.py](backend/jac/models.py):

```python
class ResumeSnippet(models.Model):
    """Reusable, hand-written first-person prose the application generator
    stitches together with minimal LLM glue — keeping the applicant's voice
    human instead of fully-generated. `domains` / `skills` drive relevance
    matching at generation time (Phase 6); nothing consumes these yet.
    """

    class Kind(models.TextChoices):
        intro = "intro", _("Introduction")
        achievement = "achievement", _("Achievement")
        value_statement = "value_statement", _("Value statement")
        closing = "closing", _("Closing")
        other = "other", _("Other")

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="snippets"
    )
    title = models.CharField(max_length=200)
    content = models.TextField()
    kind = models.CharField(max_length=16, choices=Kind, default=Kind.other)
    domains = models.ManyToManyField(Domain, blank=True)
    skills = models.ManyToManyField(Skill, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "title"]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.title}"
```

### 3.3. Migration

```bash
python manage.py makemigrations jac
```

You should see **one** migration adding: `Skill.years_of_experience_override`, `Skill.related_skills`, and `ResumeSnippet` (+ its two M2M through tables). Read the generated file — confirm no unexpected `AlterField` on unrelated columns. Then:

```bash
python manage.py migrate
```

**Verify:**

```bash
python manage.py shell <<'PY'
from django.contrib.auth import get_user_model
from jac.models import Skill
u = get_user_model().objects.first()
s = Skill.objects.create(user=u, name="ZZ-test", first_used="2010-01-01")
print("computed:", s.years_of_experience)          # ~15 (year delta)
s.years_of_experience_override = 3
print("override:", s.years_of_experience)           # 3
s.delete()
print("ok")
PY
```

Override wins; clearing it returns the computed value. Stop and fix if not.

---

## 4. Serializers

[backend/jac/serializers.py](backend/jac/serializers.py).

### 4.1. `SkillSerializer` — two new fields

`related_skills` is user-scoped, so extend `user_scoped_fields`. Add the writable override field. `related_skills` needs a self-reference guard (can't relate a skill to itself):

```python
class SkillSerializer(ScopeDomainsToUserMixin, serializers.ModelSerializer):
    user_scoped_fields = ("certification", "related_skills")
    domain_scoped_fields = ("domains",)

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    domains = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Domain.objects.all(), required=False,
    )
    certification = serializers.PrimaryKeyRelatedField(
        queryset=Certification.objects.all(), required=False, allow_null=True,
    )
    related_skills = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Skill.objects.all(), required=False,
    )

    # Effective years (override or computed) — read-only.
    years_of_experience = serializers.SerializerMethodField()

    class Meta:
        model = Skill
        fields = [
            "id",
            "name",
            "proficiency",
            "category",
            "domains",
            "first_used",
            "years_of_experience_override",
            "certification",
            "related_skills",
            "years_of_experience",
            "description",
            "user",
        ]
        read_only_fields = ["id", "years_of_experience"]
        validators = [
            UniqueTogetherValidator(
                queryset=Skill.objects.all(),
                fields=["user", "name"],
            )
        ]

    def get_years_of_experience(self, obj):
        return obj.years_of_experience

    def validate_related_skills(self, value):
        if self.instance and any(s.pk == self.instance.pk for s in value):
            raise serializers.ValidationError("A skill can't relate to itself.")
        return value
```

Two non-obvious points:

- `years_of_experience_override` is **not** in `read_only_fields` — it's writable. `years_of_experience` is read-only and now reflects the override transparently, so the frontend reads one number and writes another.
- The self-reference guard only fires on edit (`self.instance` exists). On create the skill has no PK yet, so it can't list itself.

### 4.2. `ResumeSnippetSerializer` — new

`domains` reuses the system-default-aware scoping; `skills` is plain user-scoped:

```python
class ResumeSnippetSerializer(ScopeDomainsToUserMixin, serializers.ModelSerializer):
    user_scoped_fields = ("skills",)
    domain_scoped_fields = ("domains",)

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    domains = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Domain.objects.all(), required=False,
    )
    skills = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Skill.objects.all(), required=False,
    )

    class Meta:
        model = ResumeSnippet
        fields = [
            "id",
            "title",
            "content",
            "kind",
            "domains",
            "skills",
            "is_active",
            "created_at",
            "updated_at",
            "user",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
```

Add `ResumeSnippet` to the model import at the top of the file.

---

## 5. Viewset + route

[backend/jac/views.py](backend/jac/views.py): one new viewset, same scoping pattern as the rest.

```python
class ResumeSnippetViewSet(viewsets.ModelViewSet):
    serializer_class = ResumeSnippetSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    search_fields = ["title", "content"]
    filterset_fields = ["kind", "is_active", "domains", "skills"]
    ordering_fields = ["kind", "title", "created_at", "updated_at"]

    def get_queryset(self):
        return ResumeSnippet.objects.filter(user=self.request.user).order_by("kind", "title")
```

Add `ResumeSnippet` to the models import and `ResumeSnippetSerializer` to the serializers import in `views.py`.

[backend/jac/urls.py](backend/jac/urls.py): register it.

```python
router.register("resume-snippets", ResumeSnippetViewSet, basename="resume-snippet")
```

(Import `ResumeSnippetViewSet` in the `from jac.views import (…)` block.)

**Verify:**

```bash
python manage.py runserver  # in another terminal
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/jac/resume-snippets/
# 403/401 anonymous (auth required) — proves the route exists and is mounted
```

The route resolving (not 404) is the check; auth rejection is expected without a session.

---

## 6. Admin

[backend/jac/admin.py](backend/jac/admin.py). Register `ResumeSnippet` (the roadmap calls for admin-registered) and surface the new Skill fields so the door's always usable from the admin too.

```python
from .models import (
    Domain, Location, Education, Certification, Skill, Job, Project, Language,
    ResumeSnippet,
)
```

Extend `SkillAdmin` and add `SnippetAdmin`:

```python
@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "proficiency", "years_of_experience_override")
    list_filter = ("category", "proficiency", "domains")
    search_fields = ("name",)
    filter_horizontal = ("domains", "related_skills")


@admin.register(ResumeSnippet)
class SnippetAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "is_active", "updated_at")
    list_filter = ("kind", "is_active")
    search_fields = ("title", "content")
    filter_horizontal = ("domains", "skills")
```

**Verify:** `python manage.py runserver`, log into `/admin/` (with MFA per the gate), open a Skill → "Related skills" horizontal picker + "Manual years of experience" field present. "ResumeSnippets" appears in the jac section; create one.

---

## 7. Tests

[backend/jac/tests.py](backend/jac/tests.py). Add cases proving each new behaviour. Match the existing file's style (DRF `APITestCase` / `APIClient`, per-user fixtures). At minimum:

**`Skill.years_of_experience` override (model-level):**
- Skill with `first_used` 10y ago and no override → property ≈ computed delta.
- Same skill with `years_of_experience_override = 2` → property returns `2`.
- Override set then cleared (`None`) → property falls back to computed.

**`years_of_experience_override` round-trips through the API:**
- `PATCH /api/jac/skills/<id>/ {"years_of_experience_override": 4}` → 200, response `years_of_experience == 4`, `years_of_experience_override == 4`.
- `PATCH … {"years_of_experience_override": null}` → 200, `years_of_experience` reverts to computed.
- `years_of_experience_override` is *writable* (not silently dropped); `years_of_experience` is *read-only* (POSTing it doesn't change the stored value).

**`related_skills`:**
- `PATCH /api/jac/skills/<a>/ {"related_skills": [b]}` → 200; re-`GET /api/jac/skills/<b>/` lists `a` (symmetry).
- `PATCH /api/jac/skills/<a>/ {"related_skills": [a]}` → 400 (self-reference guard).
- A user **cannot** relate to another user's skill: `PATCH` with a foreign skill PK → 400 (queryset scoping rejects it). This is the security-relevant one — write it.

**`ResumeSnippet` CRUD + scoping:**
- `POST /api/jac/resume-snippets/` (title/content/kind) → 201, `user` set to request user (never trusted from the body).
- List is user-scoped: user B's `GET /api/jac/resume-snippets/` does not include user A's snippets.
- `kind` outside the choices → 400.
- A snippet's `skills`/`domains` can't reference another user's rows → 400.

Run:

```bash
python manage.py test
# expect a count meaningfully above 163, all OK
```

If any scoping test passes when it should fail, the mixin isn't wired on the field — recheck `user_scoped_fields` / `domain_scoped_fields`.

---

## 8. End-to-end verification — the full loop

Backend running, logged in as a verified user (use the SPA session or DRF browsable API with session auth).

1. **Migration applied.** `python manage.py showmigrations jac` → the new migration has an `[X]`.
2. **Override beats computation.** Create a Skill with `first_used` a decade back via `/api/jac/skills/` → `years_of_experience` shows the big computed number → `PATCH years_of_experience_override: 3` → `years_of_experience` now `3`. Clear it → big number returns.
3. **Symmetric relation.** Create skills "Accounting" and "SevDesk". `PATCH` Accounting's `related_skills` to include SevDesk → `GET` SevDesk → Accounting is in *its* `related_skills`.
4. **Self / cross-user guards.** Relating a skill to itself → 400. Relating to a skill owned by a second user → 400.
5. **ResumeSnippet.** `POST` an `intro` snippet with content + one domain + one skill → 201. It shows in `/admin/`. Filter `/api/jac/resume-snippets/?kind=intro&is_active=true` → returns it; `?kind=closing` → doesn't.
6. **Isolation.** Second user's `/api/jac/resume-snippets/` and `/api/jac/skills/` show only their own rows.
7. **Spec.** Open <http://localhost:8000/api/docs/> → `snippets` resource present; Skill schema shows `years_of_experience_override` + `related_skills`.
8. **Suite.** `python manage.py test` green.

All eight pass → 3a is done.

---

## 9. What you should have at the end

```
backend/jac/
├── models.py          # Skill.years_of_experience_override + related_skills; new ResumeSnippet; property honours override
├── migrations/
│   └── 00xx_skill_manual_years_related_snippet.py   # one migration
├── serializers.py     # SkillSerializer (+2 fields, self-ref guard); new ResumeSnippetSerializer
├── views.py           # new ResumeSnippetViewSet
├── urls.py            # router.register("resume-snippets", …)
├── admin.py           # SnippetAdmin; SkillAdmin gains related_skills + manual years
└── tests.py           # override / related_skills / ResumeSnippet CRUD + scoping cases
```

Re-run the suite, then commit code + this guide together:

```bash
python manage.py test
git add backend/jac/ .claude/plans/phase-3a-setup-guide.md CLAUDE.md
git commit -m "Phase 3a: Skill years-override + related_skills + ResumeSnippet model"
```

(Update the CLAUDE.md jac table + roadmap "Shipped" list to note `ResumeSnippet`, `Skill.years_of_experience_override`, and `related_skills` shipped before committing.)

---

## 10. Known gaps to revisit

Don't fix in 3a — log for the named later phase:

- **Frontend surfacing (next frontend slice / 3b).** The skill editor still shows only the computed `years_of_experience`. Add the "auto: N — override" field and a `related_skills` `SkillPicker` (the picker component already exists from 2c §8d). A `/cv/snippets` CRUD page is also frontend work. None of it lands in 3a.
- **Ordering by effective experience (3b).** `SkillViewSet`'s `experience_since` annotation orders by the *computed* earliest date and ignores `years_of_experience_override` (a count, not a date — they don't combine cleanly at the DB level). Sorting still works; it just doesn't reflect overrides. Revisit if/when it matters.
- **ResumeSnippet has no consumer yet (Phase 6).** Storage + CRUD only. The generation wrapper that selects snippets by relevance, orders them, and asks the LLM for connective tissue is Phase 6. `domains`/`skills` exist now purely so the data's ready.
- **`related_skills` relation type (later).** Symmetric "these go together" only. If directed semantics ("implemented-via-tool") prove necessary, promote to a `through` model then — not speculatively now.
- **`ResumeSnippet.content` translation (3f).** Output localization stores prose translations in a `translations` JSONField on entries; `ResumeSnippet` is named as getting the same treatment. That's 3f — `ResumeSnippet` ships English-only here.
- **ResumeSnippet bulk / `is_active` toggle UX.** When the frontend page lands, an inline active toggle is nicer than editing the row. Defer to that slice.

---

## What's next

**3b — backend refactors surfaced by 2c/2d**: `POST /api/jac/<resource>/bulk/` (replacing the client-side fan-out in `useBulkDestroy`/`useBulkPatchDomains`), inline Skill creation from the picker (now that Skills carry category + proficiency + these new fields), explicit `ordering_fields` lockdown including `updated_at`, multipart/avatar upload, and the Domain "system default" `is_default` flag. 3a gave 3b the richer Skill it needs for the inline-create mini-form.
