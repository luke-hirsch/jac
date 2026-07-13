# [fullstack] personality questions as a model — system defaults + user-added, selectable dossier model

> **Mode note:** default-strict — Lukas types the non-test source; the AI writes the
> tests to disk (red first). Branch: `fullstack/personality-questions` (cut off the
> current HEAD, which already carries the landed questionnaire frontend + letter-quality
> work; `main` is only one commit behind). The uncommitted `[frontend]-personality-
> questionnaire.md` (Lukas's Results notes) rides along — it's the guide this one follows.

## why

Follow-up to `[frontend]-personality-questionnaire.md`. Its Results logged two asks:

1. **Extend / rebalance the question pool.** First live run read very *corporate*, the
   next very *non-corporate* — the oblique-only set pushes the dossier to one extreme.
   Lukas wants a middle ground and *more* questions, and — crucially — the ability to
   **add his own**. That means the pool can no longer be a hardcoded Python list: it
   becomes a **model**, with the current + new questions seeded as read-only **system**
   defaults (same pattern as `jac.Domain`) and users adding private questions on top.
2. **The dossier model is invisible.** The Personality page rebuilds the dossier on a
   fixed `default` alias with no indication which model ran. Make it a **picker**, like
   the application generate panel — the rebuild endpoint already takes `?alias=`.

Two structural consequences fall out of #1:

- The **distiller prompt** currently resolves question labels from a static
  `_QUESTION_LABEL` dict. With user-authored questions it must resolve labels from the
  DB instead, or a user's own question shows up in the prompt as a bare slug.
- `seed_default_domains` already seeds domains **and** both layouts; adding questions
  makes the name a lie. **Rename it `seed_system_defaults`** and fold questions in.

Roadmap: this sharpens the personal-paragraph half of the portfolio/CV showcase
(`[[project-purpose-cv-showcase]]`); no roadmap line moves.

## design decisions

- **`PersonalityQuestion` model, system-scoped.** Rows owned by the
  `SYSTEM_USER_USERNAME` sentinel are shared read-only defaults; a user's own rows are
  private. Reuses the exact `SystemScopedManager.for_user` union that backs `Domain` and
  `ApplicationLayout`.
- **`SystemScopedManager` moves to a shared module** (`backend/lukehirsch/managers.py`).
  It currently lives in `jac/models.py`; `spa` must not import the CV-tool app for core
  infra (and `jac` already depends on `spa` at runtime — a back-edge would be a cycle
  smell). Promoting it to `lukehirsch` (the config package that already holds shared
  middleware/permissions) lets both apps import it cleanly. `jac.models` keeps
  `DomainManager(SystemScopedManager)` via the import — one-line swap.
- **`slug` is the answer key, not the pk.** `PersonalityProfile.answers` stays keyed by
  the stable slug (existing rows keep `"flow"`, `"excessive"`, …), so no data migration
  and no orphaned answers. System defaults seed with their current slugs; user questions
  get a slug derived from the prompt, **deduped against the user's *visible* set** (own +
  system) so it can never shadow a default. Editing a prompt keeps the slug → the answer
  survives an edit.
- **Questions read from the DB at runtime; `PERSONALITY_QUESTIONS` becomes the seed
  source only** (extended, `id`→`slug` key). The serializer's `questions` field and the
  distiller's labels both resolve through `PersonalityQuestion.objects.for_user`. No
  runtime fallback to the Python list — consistent with the Domain pattern; the seed
  command is the single source, run on deploy. `_QUESTION_LABEL` is deleted.
- **CRUD scoping.** `GET/POST /personality/questions/` lists the visible set (pagination
  off → plain array, mirroring the embedded shape) and creates *own* rows;
  `PATCH/DELETE /personality/questions/<pk>/` operates only over the user's own rows, so
  touching a system default 404s (read-only by construction, not by a guard).
- **One question shape everywhere**: `{pk, slug, prompt, editable}`. The frontend keys
  answers by `slug` (was `id`); `editable` drives the per-question remove button.
- **Dossier model picker**: any alias can distil (it's one `complete()` call — no grade
  gate, unlike generation), so the picker offers *all* aliases from `useLLMAliases()`,
  default `"default"`, and threads the choice to `rebuild/?alias=`.

## affected files

| file                                                        | change                                                                                  |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `backend/lukehirsch/managers.py` (new)                      | `SystemScopedManager` promoted here (shared by jac + spa)                                |
| `backend/jac/models.py`                                     | drop the local `SystemScopedManager` class, import it from `lukehirsch.managers`         |
| `backend/spa/models.py`                                     | `PersonalityQuestion` model; `ensure_dossier` builds labels from the DB                  |
| `backend/spa/personality_questions.py`                      | `id`→`slug` key; add ~6 balancing questions; delete `_QUESTION_LABEL`                     |
| `backend/spa/distill.py`                                    | `PersonalityDistiller` takes a `labels` map instead of importing `_QUESTION_LABEL`        |
| `backend/spa/serializers.py`                                | `get_questions` reads the DB; new `PersonalityQuestionSerializer` + `_unique_question_slug` |
| `backend/spa/views.py`                                      | `PersonalityQuestionListCreateView` + `PersonalityQuestionDetailView`                     |
| `backend/spa/urls.py`                                       | two `personality/questions/…` routes                                                     |
| `backend/spa/admin.py`                                      | register `PersonalityQuestion` (optional, nice for tuning defaults)                       |
| `backend/spa/migrations/0005_personalityquestion.py` (gen)  | `makemigrations spa` — Lukas runs it                                                     |
| `backend/jac/management/commands/seed_system_defaults.py`   | **renamed** from `seed_default_domains.py`; seed questions too                            |
| `frontend/src/lib/queries/personality.ts`                   | question shape `{pk,slug,prompt,editable}`; `useRebuildDossier(alias)`; question mutations; `validateQuestionPrompt` |
| `frontend/src/routes/_authenticated/account/personality.tsx`| model picker; per-question remove; add-question row; answers keyed by `slug`             |
| `backend/spa/tests/test_personality.py`                     | (AI, on disk) question model/API/distiller-labels tests; existing tests re-pinned         |
| `backend/jac/tests/test_commands.py`                        | (AI, on disk) `seed_system_defaults` rename + question-seeding assertions                 |
| `frontend/tests/lib/queries/personality.test.ts`            | (AI, on disk) `validateQuestionPrompt`; question fixture reshaped                         |

No change to the personal-paragraph pipeline, tasks, WS, or the `answers` storage format.

---

## the code

### 1. `backend/lukehirsch/managers.py` (new)

```python
"""Shared model managers used across apps.

`SystemScopedManager` backs the "system defaults + per-user rows" pattern: rows owned by
the ``settings.SYSTEM_USER_USERNAME`` sentinel are read-only defaults visible to everyone
(``jac.Domain``, ``jac.ApplicationLayout``, ``spa.PersonalityQuestion``); every other row
is private to its owner. It lives here — the config package that already holds shared
middleware/permissions — rather than in an app, so both ``jac`` and ``spa`` can share it
without an app-to-app import (``jac`` already depends on ``spa`` at runtime; a back-edge
would be a cycle smell).
"""

from django.conf import settings
from django.db import models
from django.db.models import Q


class SystemScopedManager(models.Manager):
    def for_user(self, user):
        return self.filter(
            Q(user=user) | Q(user__username=settings.SYSTEM_USER_USERNAME)
        )

    def defaults(self):
        return self.filter(user__username=settings.SYSTEM_USER_USERNAME)
```

### 2. `backend/jac/models.py` — swap the class for the shared import

Add to the import block near the top (with the other `from` imports):

```python
from lukehirsch.managers import SystemScopedManager
```

Then **delete** the local class definition (the `class SystemScopedManager(models.Manager): …`
block, roughly lines 63–76 — the `for_user` / `defaults` methods and their docstring). Leave
`DomainManager(SystemScopedManager)` exactly as-is; it now subclasses the imported base.
`ApplicationLayout` / `Domain` `objects = SystemScopedManager()` are unchanged.

### 3. `backend/spa/models.py` — the question model + DB-resolved labels

Add to the imports at the top:

```python
from django.conf import settings  # noqa: F401 — used indirectly via the shared manager
from django.db.models import UniqueConstraint

from lukehirsch.managers import SystemScopedManager
from spa.personality_questions import MAX_ANSWER_LEN
```

> `settings` isn't referenced directly here (the manager reads it) — drop that line if your
> linter objects; it's listed only so the import block reads complete.

Append the new model **after** `PersonalityProfile`:

```python
class PersonalityQuestion(models.Model):
    """A questionnaire prompt. Rows owned by the ``settings.SYSTEM_USER_USERNAME`` user are
    the shared defaults every user answers (seeded by ``seed_system_defaults``); a user's own
    rows are private questions they add on top — same read-only-defaults pattern as
    ``jac.Domain``. ``slug`` is the stable key the answer is stored under in
    ``PersonalityProfile.answers``, so editing a prompt never orphans its answer.
    """

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="personality_questions"
    )
    slug = models.SlugField(max_length=50)
    prompt = models.CharField(max_length=MAX_ANSWER_LEN)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SystemScopedManager()

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            UniqueConstraint("user", "slug", name="unique_question_slug_per_user")
        ]

    def __str__(self):
        return self.prompt
```

Rewrite `PersonalityProfile.ensure_dossier` so the distiller gets DB-resolved labels
(the only change is the `labels=…` map; note `PersonalityQuestion` is defined later in the
module, but this reads it at call time, so the forward reference is fine):

```python
    def ensure_dossier(self, *, alias: str = "default", user=None) -> str:
        """Return the dossier, distilling (1 LLM call) if missing or stale. '' if no answers."""
        if not self.has_answers():
            return ""
        if self.dossier and not self.dossier_stale():
            return self.dossier
        from spa.distill import PersonalityDistiller

        # Resolve the slug->prompt map from the DB so a user's own questions render as
        # their real wording in the distiller prompt, not a bare slug.
        labels = {
            q.slug: q.prompt for q in PersonalityQuestion.objects.for_user(self.user)
        }
        text = PersonalityDistiller(
            self.answers, labels=labels, alias=alias, user=user
        ).distill()
        if text:
            self.dossier = text
            self.dossier_built_at = timezone.now()
            self.save(update_fields=["dossier", "dossier_built_at", "updated_at"])
        return self.dossier or ""
```

Then let Lukas generate the migration:

```
cd backend && python manage.py makemigrations spa   # -> 0005_personalityquestion.py
```

### 4. `backend/spa/personality_questions.py` — seed source only

`id`→`slug`, delete `_QUESTION_LABEL`, and add the balancing (work-values) questions. Full
new file:

```python
MAX_ANSWER_LEN = 280  # one tweet; enforced in the serializer, hinted in the UI

# The system-default question pool: SEED SOURCE ONLY. At runtime the questions come from the
# PersonalityQuestion table (seeded from this list by `seed_system_defaults`), so a user's
# own additions live alongside these. `slug` is the stable answers-dict key — don't rename an
# existing one (it would orphan stored answers); add new entries at the end.
#
# The pool deliberately blends oblique/personal prompts (character) with work-values prompts
# (fit) — an all-oblique set pushed the distilled dossier too far from professional register;
# the work-values half pulls it back toward a hireable middle ground.
PERSONALITY_QUESTIONS = [
    # --- oblique / character ---
    {
        "slug": "excessive",
        "prompt": "What's something you do that others find slightly excessive?",
    },
    {
        "slug": "contrarian",
        "prompt": "What's a belief you hold that most people around you disagree with?",
    },
    {"slug": "flow", "prompt": "What do you lose track of time doing?"},
    {
        "slug": "bad_advice",
        "prompt": "What's a piece of common advice you think is just wrong?",
    },
    {
        "slug": "annoyance",
        "prompt": "What's the last thing that genuinely annoyed you — and why?",
    },
    {
        "slug": "small_pride",
        "prompt": "What's a small thing you're disproportionately proud of?",
    },
    {"slug": "most_yourself", "prompt": "When do you feel most like yourself?"},
    {"slug": "childhood", "prompt": "What did you want to be at ten years old?"},
    {"slug": "broken_rule", "prompt": "What's a rule you happily break?"},
    {
        "slug": "changed_mind",
        "prompt": "What's something you changed your mind about recently?",
    },
    {"slug": "admire", "prompt": "Who do you admire, and for what specifically?"},
    {"slug": "obsession", "prompt": "What problem can you not stop thinking about?"},
    # --- work values / fit (balancing set) ---
    {
        "slug": "roll_up_sleeves",
        "prompt": "What kind of problem makes you want to roll up your sleeves and dig in?",
    },
    {
        "slug": "best_environment",
        "prompt": "In what kind of environment do you do your best work?",
    },
    {
        "slug": "proud_project",
        "prompt": "What's a project you're proud of — and what was your part in it?",
    },
    {
        "slug": "collaborate",
        "prompt": "How do you work best with a team?",
    },
    {
        "slug": "good_manager",
        "prompt": "What would a good manager understand about how you work?",
    },
    {
        "slug": "worth_joining",
        "prompt": "Beyond the job title, what makes a company worth joining?",
    },
]
```

> Removing `_QUESTION_LABEL` will break `spa/distill.py`'s import until step 5 lands — that's
> expected while typing; do steps 4 and 5 together.

### 5. `backend/spa/distill.py` — labels via constructor, not a static dict

Delete the `from spa.personality_questions import _QUESTION_LABEL` import. Change the
constructor and `_prompt`:

```python
    def __init__(
        self, answers: dict, *, labels: dict | None = None, alias: str = "default", user=None
    ):
        self.answers = answers or {}
        self.labels = labels or {}  # {slug: prompt}; falls back to the slug when missing
        self.alias = alias
        self.user = user
```

```python
    def _prompt(self) -> str:
        blocks = "\n\n".join(
            f"Q: {self.labels.get(qid, qid)}\nA: {ans}"
            for qid, ans in self.answers.items()
            if ans
        )
        return f"{self._INSTRUCTION}\n\n{blocks}\n\nDOSSIER:"
```

Everything else in `distill.py` (the `_INSTRUCTION`, `distill()` body, logging) is unchanged.

### 6. `backend/spa/serializers.py` — DB-backed questions + CRUD serializer

Change the import line and add the helper + serializer. The new import block:

```python
from django.utils.text import slugify
from rest_framework import serializers

from spa.models import PersonalityProfile, PersonalityQuestion, UserProfile
from spa.personality_questions import MAX_ANSWER_LEN
```

Add the slug helper near the top (module level, after the imports):

```python
def _unique_question_slug(user, prompt: str) -> str:
    """A slug for a new user question, unique within the user's *visible* set (own rows +
    system defaults) so a user's key can never collide with — and shadow — a default's."""
    base = slugify(prompt)[:40] or "question"
    taken = set(
        PersonalityQuestion.objects.for_user(user).values_list("slug", flat=True)
    )
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug
```

Replace `PersonalityProfileSerializer.get_questions` with a DB read (system defaults first,
then the user's own — deterministic):

```python
    def get_questions(self, obj):
        rows = sorted(
            PersonalityQuestion.objects.for_user(obj.user),
            key=lambda q: (q.user_id == obj.user_id, q.order, q.pk),
        )
        return [
            {
                "pk": q.pk,
                "slug": q.slug,
                "prompt": q.prompt,
                "editable": q.user_id == obj.user_id,
            }
            for q in rows
        ]
```

Append the CRUD serializer at the end of the file:

```python
class PersonalityQuestionSerializer(serializers.ModelSerializer):
    """CRUD over a user's own personality questions. `slug` is server-assigned on create and
    read-only thereafter (editing a prompt keeps the answer key intact). `editable` mirrors the
    embedded-questions flag so one shape serves both endpoints."""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    editable = serializers.SerializerMethodField()

    class Meta:
        model = PersonalityQuestion
        fields = ("pk", "user", "slug", "prompt", "order", "editable")
        read_only_fields = ("slug", "order")

    def get_editable(self, obj) -> bool:
        request = self.context.get("request")
        return bool(request and obj.user_id == request.user.id)

    def validate_prompt(self, value):
        text = (value or "").strip()
        if not text:
            raise serializers.ValidationError("A question needs a prompt.")
        if len(text) > MAX_ANSWER_LEN:
            raise serializers.ValidationError(
                f"Question exceeds the {MAX_ANSWER_LEN}-character limit."
            )
        return text

    def create(self, validated_data):
        validated_data["slug"] = _unique_question_slug(
            validated_data["user"], validated_data["prompt"]
        )
        return super().create(validated_data)
```

### 7. `backend/spa/views.py` — the two question views

Extend the serializer/model imports:

```python
from spa.models import PersonalityProfile, PersonalityQuestion, UserProfile
from spa.serializers import (
    PersonalityProfileSerializer,
    PersonalityQuestionSerializer,
    UserProfileSerializer,
)
```

Append the views (after `PersonalityDossierRebuildView`):

```python
class PersonalityQuestionListCreateView(generics.ListCreateAPIView):
    """GET the user's visible questions (system defaults + own); POST adds one of the user's
    own. Small list — pagination off, so the response is a plain array matching the shape
    embedded in the personality endpoint."""

    serializer_class = PersonalityQuestionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return PersonalityQuestion.objects.for_user(self.request.user)


class PersonalityQuestionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """PATCH/DELETE a question the user OWNS. System defaults are read-only: they are absent
    from this queryset, so addressing one 404s (no guard needed)."""

    serializer_class = PersonalityQuestionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PersonalityQuestion.objects.filter(user=self.request.user)
```

### 8. `backend/spa/urls.py` — routes

Import the two views and add the paths (before or after the existing `personality/…` lines;
order doesn't matter — all patterns are exact with a trailing slash):

```python
from spa.views import (
    AccountDeleteView,
    PersonalityDossierRebuildView,
    PersonalityProfileView,
    PersonalityQuestionDetailView,
    PersonalityQuestionListCreateView,
    UserProfileView,
)
```

```python
    path(
        "personality/questions/",
        PersonalityQuestionListCreateView.as_view(),
        name="personality-questions",
    ),
    path(
        "personality/questions/<int:pk>/",
        PersonalityQuestionDetailView.as_view(),
        name="personality-question-detail",
    ),
```

### 9. `backend/spa/admin.py` — register (optional but handy for tuning defaults)

```python
from spa.models import PersonalityQuestion, UserProfile


@admin.register(PersonalityQuestion)
class PersonalityQuestionAdmin(admin.ModelAdmin):
    list_display = ["slug", "prompt", "user", "order"]
    list_filter = ["user"]
    search_fields = ["slug", "prompt"]
    raw_id_fields = ["user"]
    ordering = ["user", "order"]
```

### 10. Rename `seed_default_domains.py` → `seed_system_defaults.py` + seed questions

```
cd backend && git mv jac/management/commands/seed_default_domains.py \
                     jac/management/commands/seed_system_defaults.py
```

Then edit the renamed file. Header docstring — update the first paragraph + usage lines to
name questions and the new command:

```python
"""Seed the shared system defaults: the Domain taxonomy, the personality-question pool, and
the default ApplicationLayouts.

Rows owned by the ``settings.SYSTEM_USER_USERNAME`` user are read-only defaults visible to
every user (see ``SystemScopedManager.for_user``). There is no fixture or data migration for
them — this command is the single, idempotent source of truth, so a freshly deployed box gets
the same picker/questionnaire defaults as dev.

The default layouts also carry their template file (``jac/resources/*.json``, a declarative
spec the frontend react-pdf renderer consumes); they back the ``JobApplication.layout`` field
default / SET_DEFAULT target.

Usage:
    python manage.py seed_system_defaults          # create anything that is missing
    python manage.py seed_system_defaults --prune   # also delete system rows not in these lists

Re-runnable: existing rows are left untouched (question wording/order is re-synced from
PERSONALITY_QUESTIONS); only missing ones are created. Domains are kept deliberately *broad*
(industries / sectors) — a user adds their own narrower tags on top.
"""
```

Add the imports (with the existing ones):

```python
from spa.models import PersonalityQuestion
from spa.personality_questions import PERSONALITY_QUESTIONS
```

Inside `handle`, after the domain loop / prune (before the layout loop), add the question
seeding — it mirrors the domain pattern but also re-syncs wording so `PERSONALITY_QUESTIONS`
edits propagate on re-seed:

```python
        q_created = []
        for i, q in enumerate(PERSONALITY_QUESTIONS):
            obj, was_created = PersonalityQuestion.objects.get_or_create(
                user=system,
                slug=q["slug"],
                defaults={"prompt": q["prompt"], "order": i},
            )
            if was_created:
                q_created.append(q["slug"])
            elif obj.prompt != q["prompt"] or obj.order != i:
                # let the wording/order be re-tuned in PERSONALITY_QUESTIONS and re-seeded
                obj.prompt = q["prompt"]
                obj.order = i
                obj.save(update_fields=["prompt", "order"])

        q_pruned = []
        if options["prune"]:
            wanted_slugs = {q["slug"] for q in PERSONALITY_QUESTIONS}
            for q in PersonalityQuestion.objects.filter(user=system).exclude(
                slug__in=wanted_slugs
            ):
                q_pruned.append(q.slug)
                q.delete()
```

And report it alongside the domain summary (after the existing `if created:` / `if pruned:`
block):

```python
        self.stdout.write(
            self.style.SUCCESS(
                f"Personality questions: "
                f"{PersonalityQuestion.objects.filter(user=system).count()} total, "
                f"{len(q_created)} created."
            )
        )
        if q_created:
            self.stdout.write("  created: " + ", ".join(q_created))
        if q_pruned:
            self.stdout.write(self.style.WARNING("  pruned: " + ", ".join(q_pruned)))
```

> The `--prune` flag now also prunes stale system questions — its help text ("Delete existing
> system-default domains not in DEFAULT_DOMAINS") is a touch narrow now; widen it to "…system
> defaults not in these lists" if you like. Historical `plans/done/*.md` still say
> `seed_default_domains`; leave them — they're a record of what was true then.

### 11. `frontend/src/lib/queries/personality.ts`

Reshape the question type, thread `alias` through rebuild, add the question mutations and the
`validateQuestionPrompt` helper. Diff-style — the pure answer helpers
(`cleanAnswers`/`answeredCount`/`overlongAnswers`/`answersDirty`/`dossierState`/`personalityHint`)
are unchanged; they key by arbitrary strings.

Replace the `PersonalityQuestion` type:

```ts
/** One row of the questionnaire — a system default or one of the user's own.
 *  Answers are keyed by `slug` (stable across prompt edits). */
export type PersonalityQuestion = {
  pk: number;
  slug: string;
  prompt: string;
  editable: boolean;
};
```

Add the question cap + validator next to `MAX_ANSWER_LEN`:

```ts
/** Mirrors the CRUD serializer's validate_prompt (spa/serializers.py). */
export const MAX_QUESTION_LEN = 280;

/** Add-a-question input validation — mirror of the serializer. Error string or null. */
export function validateQuestionPrompt(prompt: string): string | null {
  const text = prompt.trim();
  if (!text) return "A question needs a prompt.";
  if (text.length > MAX_QUESTION_LEN)
    return `Keep it under ${MAX_QUESTION_LEN} characters.`;
  return null;
}
```

Thread the alias through `useRebuildDossier`:

```ts
export function useRebuildDossier() {
  const qc = useQueryClient();
  return useMutation({
    // The distiller runs on whatever alias the user picked (any model can distil —
    // no grade gate, unlike generation). Defaults to the settings "default" alias.
    mutationFn: (alias: string = "default") =>
      api<{ dossier: string }>(
        `${URL}rebuild/?alias=${encodeURIComponent(alias)}`,
        { method: "POST" },
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
```

Add the question mutations at the end of the file (they invalidate the personality query so the
embedded `questions` list refreshes):

```ts
const QUESTIONS_URL = "/api/spa/personality/questions/";

export function useCreateQuestion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (prompt: string) =>
      api<PersonalityQuestion>(QUESTIONS_URL, {
        method: "POST",
        body: JSON.stringify({ prompt }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useDeleteQuestion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pk: number) =>
      api<void>(`${QUESTIONS_URL}${pk}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
```

### 12. `frontend/src/routes/_authenticated/account/personality.tsx`

Full new file — answers key by `slug`, a model picker feeds the rebuild, each owned question
gets a remove button, and an add-a-question row sits under the list:

```tsx
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { useLLMAliases } from "@/lib/queries/llm";
import {
  MAX_ANSWER_LEN,
  answeredCount,
  answersDirty,
  cleanAnswers,
  dossierState,
  overlongAnswers,
  useCreateQuestion,
  useDeleteQuestion,
  usePersonality,
  useRebuildDossier,
  useUpdateAnswers,
  validateQuestionPrompt,
} from "@/lib/queries/personality";

export const Route = createFileRoute("/_authenticated/account/personality")({
  component: PersonalityPage,
});

const STATE_LABEL = {
  none: "no dossier yet",
  stale: "rebuilds on the next generation",
  fresh: "up to date",
} as const;

function PersonalityPage() {
  const personality = usePersonality();
  const aliases = useLLMAliases();
  const update = useUpdateAnswers();
  const rebuild = useRebuildDossier();
  const createQuestion = useCreateQuestion();
  const deleteQuestion = useDeleteQuestion();
  // Seeded from the server once; refetches must not clobber edits (adjust-state-
  // during-render, same pattern as the content card's server re-seed).
  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  const [alias, setAlias] = useState("default");
  const [newQuestion, setNewQuestion] = useState("");
  if (personality.data && draft === null) setDraft(personality.data.answers);

  if (!personality.data || draft === null)
    return <p className="text-sm">loading…</p>;
  const row = personality.data;

  const overlong = overlongAnswers(draft);
  const dirty = answersDirty(row.answers, draft);
  const state = dossierState(row);
  const answered = answeredCount(draft);
  const newQuestionError = validateQuestionPrompt(newQuestion);

  function onSave() {
    update.mutate(cleanAnswers(draft!), {
      onSuccess: () => toast.success("Answers saved"),
      onError: () => toast.error("Could not save the answers"),
    });
  }

  function onRebuild() {
    rebuild.mutate(alias, {
      onSuccess: () => toast.success("Dossier rebuilt"),
      onError: () => toast.error("Could not rebuild the dossier"),
    });
  }

  function onAddQuestion() {
    if (validateQuestionPrompt(newQuestion)) return;
    createQuestion.mutate(newQuestion.trim(), {
      onSuccess: () => {
        setNewQuestion("");
        toast.success("Question added");
      },
      onError: () => toast.error("Could not add the question"),
    });
  }

  function onDeleteQuestion(pk: number, slug: string) {
    deleteQuestion.mutate(pk, {
      onSuccess: () => {
        // Drop any local draft answer for the removed question so it can't be re-sent.
        setDraft((d) => {
          if (!d) return d;
          const { [slug]: _gone, ...rest } = d;
          return rest;
        });
        toast.success("Question removed");
      },
      onError: () => toast.error("Could not remove the question"),
    });
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-medium">Personality</h2>
        <p className="text-sm text-muted-foreground">
          Oblique and work-values questions — answer the ones that spark
          something (about five to eight is plenty, one tweet each). A model
          distils them into the dossier the cover letter's personal paragraph
          grounds "you" in. Add your own questions at the bottom.
        </p>
      </div>

      <div className="space-y-4">
        {row.questions.map((q) => {
          const value = draft[q.slug] ?? "";
          const over = value.trim().length > MAX_ANSWER_LEN;
          return (
            <div key={q.slug} className="space-y-1">
              <div className="flex items-center gap-2">
                <Label htmlFor={`q-${q.slug}`}>{q.prompt}</Label>
                {q.editable && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-xs text-muted-foreground"
                    onClick={() => onDeleteQuestion(q.pk, q.slug)}
                    disabled={deleteQuestion.isPending}
                  >
                    Remove
                  </Button>
                )}
              </div>
              <Textarea
                id={`q-${q.slug}`}
                value={value}
                rows={2}
                onChange={(e) =>
                  setDraft({ ...draft, [q.slug]: e.target.value })
                }
              />
              <p
                className={`text-xs ${over ? "text-destructive" : "text-muted-foreground"}`}
              >
                {value.trim().length}/{MAX_ANSWER_LEN}
              </p>
            </div>
          );
        })}
      </div>

      <div className="flex items-end gap-2">
        <div className="flex-1 space-y-1">
          <Label htmlFor="new-question">Add your own question</Label>
          <Input
            id="new-question"
            value={newQuestion}
            placeholder="e.g. What does a great week at work look like for you?"
            onChange={(e) => setNewQuestion(e.target.value)}
          />
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={onAddQuestion}
          disabled={!!newQuestionError || createQuestion.isPending}
        >
          {createQuestion.isPending ? "Adding…" : "Add"}
        </Button>
      </div>

      <div className="flex items-center gap-3">
        <Button
          onClick={onSave}
          disabled={!dirty || overlong.length > 0 || update.isPending}
        >
          {update.isPending ? "Saving…" : "Save answers"}
        </Button>
        <span className="text-xs text-muted-foreground">
          {answered} of {row.questions.length} answered
        </span>
      </div>

      <Separator />

      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium">Dossier</h3>
          <Badge variant="outline">{STATE_LABEL[state]}</Badge>
          <div className="ml-auto flex items-center gap-2">
            <Select value={alias} onValueChange={setAlias}>
              <SelectTrigger className="w-56">
                <SelectValue
                  placeholder={
                    aliases.isLoading ? "Loading models…" : "Pick a model"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {(aliases.data ?? []).map((a) => (
                  <SelectItem key={a.alias} value={a.alias}>
                    {a.alias} — {a.model} ({a.strength})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              size="sm"
              variant="outline"
              onClick={onRebuild}
              disabled={state === "none" || rebuild.isPending}
            >
              {rebuild.isPending ? "Rebuilding…" : "Rebuild now"}
            </Button>
          </div>
        </div>
        {row.dossier ? (
          <p className="whitespace-pre-wrap rounded border bg-muted/40 p-3 text-sm">
            {row.dossier}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            No dossier yet — save some answers first. It is built automatically
            on the next generation, or on demand here (one LLM call).
          </p>
        )}
      </div>
    </div>
  );
}
```

> Subtle: `useLLMAliases()` includes the `"default"` settings-fallback alias, so the picker is
> never empty even before the user configures a provider; `"default"` is the initial pick,
> matching the endpoint's own default. Save answers before Rebuild — the distiller reads the
> **saved** answers, not the unsaved draft (the badge already flags staleness).

---

## tests (already on disk, land red)

| file                                             | class / block                                | covers                                                                                                                                     |
| ------------------------------------------------ | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `backend/spa/tests/test_personality.py`          | `PersonalityQuestionModelTests` (new)        | `for_user` returns system + own (not other users'); `defaults()`; `(user, slug)` uniqueness constraint                                       |
| `backend/spa/tests/test_personality.py`          | `PersonalityQuestionAPITests` (new)          | GET embeds DB questions as `{pk,slug,prompt,editable}`, system-first, editable flags; POST creates an owned question (editable, slug set); slug dedupes vs a system slug; DELETE own; DELETE/PATCH a system default 404s |
| `backend/spa/tests/test_personality.py`          | `PersonalityDistillerTests` (ext)            | `labels` map resolves the prompt in the LLM prompt; missing label falls back to the slug                                                     |
| `backend/spa/tests/test_personality.py`          | `EnsureDossierTests` (ext)                    | `ensure_dossier` feeds DB-resolved labels (a user question's wording appears in the distiller prompt)                                         |
| `backend/spa/tests/test_personality.py`          | `PersonalityAPITests` (updated)              | `_seed_questions()` helper; `test_get_includes_questions` re-pinned to the seeded DB rows (shape + editable), no longer `== PERSONALITY_QUESTIONS` |
| `backend/jac/tests/test_commands.py`             | `SeedSystemDefaultsTests` (renamed)          | `seed_system_defaults` seeds domains + layouts **and** the full question pool (count, system-owned, slugs), idempotent; `--prune` drops a stale system question |
| `frontend/tests/lib/queries/personality.test.ts` | `validateQuestionPrompt` (new)               | empty/whitespace → error; over 280 → error; exactly 280 and normal → null                                                                   |
| `frontend/tests/lib/queries/personality.test.ts` | `row()` fixture (updated)                    | questions reshaped to `{pk,slug,prompt,editable}` (no behavioural assertion change to the answer helpers)                                     |

Why they start red: `PersonalityQuestion` doesn't exist yet (import error across the whole spa
suite until the model + migration land), the seed command's new name isn't found until the
rename, `PersonalityDistiller` has no `labels` kwarg, and `validateQuestionPrompt` isn't
exported. Flag: the backend tests **need the migration** (`makemigrations spa`) to even collect
— they're red-by-import until then, which is the intended acceptance gate.

Run:

```
cd backend && python manage.py test spa.tests.test_personality jac.tests.test_commands
cd frontend && npx vitest run tests/lib/queries/personality.test.ts
```

## Verification (Lukas)

1. `makemigrations spa` → `0005_personalityquestion.py`; `migrate`; then
   `python manage.py seed_system_defaults` — output reports the domains, layouts, **and**
   "Personality questions: 18 total, 18 created" on a fresh box (idempotent on re-run). Old
   command name `seed_default_domains` is gone (`manage.py` no longer lists it).
2. Both suites green, full backend suite green, `npx tsc -b` clean.
3. Click-through (dev stack up): Account → Personality shows all 18 seeded questions (blend of
   oblique + work-values), system ones with no Remove button. Add a question → it appears with a
   Remove button and survives a reload; answer it, Save, reload → the answer persists under its
   generated slug. Remove it → gone, and its draft answer doesn't linger.
4. **Model picker**: pick a non-default alias, Rebuild now → the dossier regenerates and the
   badge flips to "up to date". Watch the run's request log to confirm the chosen model ran (not
   always `default`). Try a commercial alias vs the local `default` and eyeball the register
   difference.
5. **Rebalance check** (the original ask): fill ~5 oblique + ~3 work-values answers, rebuild, and
   read the dossier — it should land in a hireable middle register, neither stiffly corporate nor
   off-puttingly casual. Judgement call for Results: is the blended pool the right mix, and are 18
   questions too many to present at once (paginate/collapse later if so)?
6. End-to-end: a strong/web-capable generation with "Personal paragraph" ticked now grounds "you"
   in a dossier that includes your own questions — confirm a user-added question's answer visibly
   informs the paragraph.

## Results

_(filled by Lukas after testing — raw test output, observed issues, what works)_
