# Setup guide — personality questionnaire (spa)

> Branch: shares `backend/personal-paragraph` with its sibling guide
> `[backend]-personal-paragraph.md` (they're one feature / one PR). **Implement this guide first** —
> the personal-paragraph guide consumes `PersonalityProfile.ensure_dossier()`.
> Tests for this guide: `backend/spa/tests_personality.py` (already on disk, red).

## Context / goal

The cover-letter "personal paragraph" (sibling guide) needs a sense of *who the candidate is* —
something no CV-anchored snippet carries. This guide captures that via a **distilled
questionnaire**: free-text answers stored per user, distilled once by an LLM into a compact,
reusable `dossier`, rebuilt only when the answers change.

It lives in the **`spa`** app next to `UserProfile`, reusing the same one-to-one + post_save-signal
pattern. It's self-contained — no `jac` changes here — and the dossier is also useful later for the
portfolio (roadmap #2).

Decisions (from planning): distilled questionnaire (not raw, not free-text fields); home = `spa`.

**Questionnaire shape (from planning).** Not a corporate self-report ("what do you value?") — that
just invites a performed brand. Instead a **pool of oblique, behaviour-eliciting questions that
range beyond work**, of which the user answers **any ~5** (the rest are skippable), each capped at
**tweet length (280 chars)**. The cap is load-bearing, not cosmetic: 5 × ~280 chars is ~350 tokens
of dense, pre-distilled signal — short enough that even the `llama3.2:1b` rung can hold the thread
and produce a usable dossier (give a small model long rambling answers and it returns mush). So the
constraint that's good for the user is the same one that keeps this on the cheap self-hosted rung.
*(KISS: the "which questions did they choose to answer" meta-signal — an inferred read of selection
pattern — was considered and **dropped**. It's ungrounded by definition and only works on strong
models; not worth the complexity now.)*

## Affected files

- `backend/spa/personality_questions.py` — `PERSONALITY_QUESTIONS` pool + `MAX_ANSWER_LEN`
  + `_QUESTION_LABEL` map.
- `backend/spa/models.py` — `PersonalityProfile` model.
- `backend/spa/signals.py` — extend the existing post_save receiver to auto-create the row.
- `backend/spa/distill.py` (new) — `PersonalityDistiller` (answers → dossier, 1 LLM call).
- `backend/spa/serializers.py` — `PersonalityProfileSerializer` (per-answer length validation).
- `backend/spa/views.py` — `PersonalityProfileView` + `PersonalityDossierRebuildView`.
- `backend/spa/urls.py` — two routes.
- `backend/spa/admin.py` — register (dossier + timestamps read-only).
- `backend/spa/migrations/0002_personalityprofile.py` — `makemigrations spa` (human runs).

## The code

### 1. `spa/personality_questions.py` — the question pool

A pool of 12 oblique, behaviour-eliciting questions spanning beyond work. The user answers any ~5;
each answer is capped at `MAX_ANSWER_LEN`. Order is the display order. Keep ids stable — they're the
keys in `answers` and changing one orphans a stored answer.

```python
MAX_ANSWER_LEN = 280  # one tweet; enforced in the serializer, hinted in the UI

PERSONALITY_QUESTIONS = [
    {"id": "excessive",     "prompt": "What's something you do that others find slightly excessive?"},
    {"id": "contrarian",    "prompt": "What's a belief you hold that most people around you disagree with?"},
    {"id": "flow",          "prompt": "What do you lose track of time doing?"},
    {"id": "bad_advice",    "prompt": "What's a piece of common advice you think is just wrong?"},
    {"id": "annoyance",     "prompt": "What's the last thing that genuinely annoyed you — and why?"},
    {"id": "small_pride",   "prompt": "What's a small thing you're disproportionately proud of?"},
    {"id": "most_yourself", "prompt": "When do you feel most like yourself?"},
    {"id": "childhood",     "prompt": "What did you want to be at ten years old?"},
    {"id": "broken_rule",   "prompt": "What's a rule you happily break?"},
    {"id": "changed_mind",  "prompt": "What's something you changed your mind about recently?"},
    {"id": "admire",        "prompt": "Who do you admire, and for what specifically?"},
    {"id": "obsession",     "prompt": "What problem can you not stop thinking about?"},
]
_QUESTION_LABEL = {q["id"]: q["prompt"] for q in PERSONALITY_QUESTIONS}
```

### 1b. `spa/models.py` — the model

```python
from django.db import models   # already imported
from django.utils import timezone


class PersonalityProfile(models.Model):
    """Per-user personality questionnaire + a cached, LLM-distilled dossier.

    Answers are free text keyed by question id; the dossier is regenerated when answers change
    (dossier_stale). Used by the JAC cover-letter personal paragraph and (later) the portfolio.
    """

    user = models.OneToOneField("auth.User", on_delete=models.CASCADE, related_name="personality")
    answers = models.JSONField(default=dict, blank=True)   # {question_id: text}
    dossier = models.TextField(blank=True)                 # distilled, cached
    answers_updated_at = models.DateTimeField(null=True, blank=True)
    dossier_built_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Personality({self.user})"

    def has_answers(self) -> bool:
        return any((self.answers or {}).values())

    def dossier_stale(self) -> bool:
        if self.dossier_built_at is None:
            return True
        return bool(self.answers_updated_at and self.answers_updated_at > self.dossier_built_at)

    def ensure_dossier(self, *, alias: str = "default", user=None) -> str:
        """Return the dossier, distilling (1 LLM call) if missing or stale. '' if no answers."""
        if not self.has_answers():
            return ""
        if self.dossier and not self.dossier_stale():
            return self.dossier
        from spa.distill import PersonalityDistiller
        text = PersonalityDistiller(self.answers, alias=alias, user=user).distill()
        if text:
            self.dossier = text
            self.dossier_built_at = timezone.now()
            self.save(update_fields=["dossier", "dossier_built_at", "updated_at"])
        return self.dossier or ""
```

Extend the **existing** signal receiver in `spa/signals.py` so it also creates the personality row:

```python
@receiver(post_save, sender=get_user_model())
def _create_profile_on_user_creation(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
        PersonalityProfile.objects.create(user=instance)
```

### 2. `spa/distill.py` (new)

```python
import logging

from llm_connector import complete
from spa.personality_questions import _QUESTION_LABEL

logger = logging.getLogger(__name__)


class PersonalityDistiller:
    """Turn raw questionnaire answers into a compact, reusable personality dossier (1 LLM call).

    Output is free prose (not line-format): a short factual character sketch the paragraph writer
    can draw on. Any failure -> '' so callers fall back to no personal paragraph.
    """

    _INSTRUCTION = (
        "Below are a candidate's own answers to a short, informal questionnaire about who they are "
        "— how they think, what drives them, what they care about, in and out of work. Distil them "
        "into a compact 'personality dossier': 4-6 sentences capturing the person — what makes them "
        "tick and what makes them distinctive. Write factual, third-person prose grounded ONLY in "
        "the answers — invent nothing, add no skills or achievements. No headers, no markdown, no "
        "preamble."
    )

    def __init__(self, answers: dict, *, alias: str = "default", user=None):
        self.answers = answers or {}
        self.alias = alias
        self.user = user

    def distill(self) -> str:
        if not any(self.answers.values()):
            return ""
        try:
            raw = complete(prompt=self._prompt(), alias=self.alias, user=self.user)
        except Exception:
            logger.exception("PersonalityDistiller: LLM call failed")
            return ""
        return (raw or "").strip()

    def _prompt(self) -> str:
        blocks = "\n\n".join(
            f"Q: {_QUESTION_LABEL.get(qid, qid)}\nA: {ans}"
            for qid, ans in self.answers.items() if ans
        )
        return f"{self._INSTRUCTION}\n\n{blocks}\n\nDOSSIER:"
```

### 3. `spa/serializers.py` — add

```python
from spa.models import PersonalityProfile, UserProfile
from spa.personality_questions import MAX_ANSWER_LEN, PERSONALITY_QUESTIONS


class PersonalityProfileSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    questions = serializers.SerializerMethodField()

    class Meta:
        model = PersonalityProfile
        fields = ("id", "user", "answers", "dossier", "questions",
                  "answers_updated_at", "dossier_built_at", "updated_at")
        read_only_fields = ("id", "dossier", "questions",
                            "answers_updated_at", "dossier_built_at", "updated_at")

    def get_questions(self, obj):
        return PERSONALITY_QUESTIONS

    def validate_answers(self, value):
        """Drop blanks; reject any answer over the tweet cap. Keys aren't pinned to the pool —
        the frontend owns which questions render; unknown keys just sit unused."""
        if not isinstance(value, dict):
            raise serializers.ValidationError("answers must be an object of {question_id: text}.")
        cleaned = {k: v.strip() for k, v in value.items() if isinstance(v, str) and v.strip()}
        too_long = [k for k, v in cleaned.items() if len(v) > MAX_ANSWER_LEN]
        if too_long:
            raise serializers.ValidationError(
                f"answers over {MAX_ANSWER_LEN} chars: {', '.join(sorted(too_long))}."
            )
        return cleaned

    def update(self, instance, validated_data):
        if "answers" in validated_data:
            from django.utils import timezone
            instance.answers_updated_at = timezone.now()  # marks the dossier stale
        return super().update(instance, validated_data)
```

### 4. `spa/views.py` — add (sibling of `UserProfileView`)

```python
from rest_framework.response import Response   # already imported via APIView block
from spa.models import PersonalityProfile, UserProfile
from spa.serializers import PersonalityProfileSerializer, UserProfileSerializer


class PersonalityProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = PersonalityProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return PersonalityProfile.objects.get(user=self.request.user)


class PersonalityDossierRebuildView(APIView):
    """POST: force-rebuild + return the dossier (preview the distilled text). ?alias= (default)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        prof = PersonalityProfile.objects.get(user=request.user)
        prof.dossier_built_at = None  # force stale -> always re-distils
        text = prof.ensure_dossier(
            alias=request.query_params.get("alias", "default"), user=request.user
        )
        return Response({"dossier": text})
```

### 5. `spa/urls.py` — add two routes

```python
from spa.views import (
    AccountDeleteView, PersonalityDossierRebuildView, PersonalityProfileView, UserProfileView,
)

urlpatterns = [
    path("profile/", UserProfileView.as_view(), name="user-profile"),
    path("personality/", PersonalityProfileView.as_view(), name="personality-profile"),
    path("personality/rebuild/", PersonalityDossierRebuildView.as_view(), name="personality-rebuild"),
    path("account/", AccountDeleteView.as_view(), name="account-delete"),
]
```

### 6. `spa/admin.py` — register

Register `PersonalityProfile` with `dossier`, `dossier_built_at`, `answers_updated_at`, `updated_at`
read-only. Then `python manage.py makemigrations spa` → `0002_personalityprofile`.

## Tests

`backend/spa/tests_personality.py` (on disk, red). Covers: signal auto-creation;
`has_answers` / `dossier_stale`; `ensure_dossier` distils-once-and-caches + rebuilds when
`answers_updated_at` advances (mock `spa.distill.complete`); `PersonalityDistiller` empty→no-call /
success→stripped prose / failure→''; API `GET` returns `questions`, `PATCH` updates answers + stamps
`answers_updated_at`, blank answers dropped, over-cap answer (`> MAX_ANSWER_LEN`) → 400, `dossier`
read-only, `rebuild/` force-distils.

```
cd backend && python manage.py makemigrations spa && python manage.py migrate
cd backend && python manage.py test spa.tests_personality
```

## Verification

1. After migrate, every existing user needs a row — `makemigrations` won't backfill. Either
   recreate the test user or run a one-off:
   `for u in User.objects.all(): PersonalityProfile.objects.get_or_create(user=u)`.
2. `PATCH /api/spa/personality/` with `{"answers": {"values": "...", "company_fit": "..."}}`.
3. `POST /api/spa/personality/rebuild/?alias=strong` → response `dossier` reads as a sensible 4-6
   sentence sketch. Re-`GET` shows it cached with `dossier_built_at` set.
