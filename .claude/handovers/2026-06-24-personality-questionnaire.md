# Handover — personality questionnaire (spa) + personal-paragraph

## Goal

Give the JAC cover-letter "personal paragraph" a sense of *who the candidate is* — captured via a
distilled questionnaire in the `spa` app (per-user free-text answers → one cached LLM `dossier`,
rebuilt only when answers change). Two sibling guides, one feature / one PR, on branch
`backend/personal-paragraph`:
- `[backend]-personality-questionnaire.md` — implement **first** (provides `PersonalityProfile.ensure_dossier()`).
- `[backend]-personal-paragraph.md` — consumes the dossier; **second half, continues in a new chat**.

## Where it stands

**Questionnaire (this session):** implemented by the human and tests ran. Real files on disk:
`spa/models.py` (`PersonalityProfile`), `spa/personality_questions.py` (the pool + `MAX_ANSWER_LEN`),
`spa/distill.py` (`PersonalityDistiller`), `spa/serializers.py`, `spa/views.py`, `spa/urls.py`,
`spa/signals.py` (auto-create row on user creation), `migrations/0003_personalityprofile.py`.
Tests in `spa/tests/test_personality.py`.

**Personal-paragraph (next):** untouched this session — its guide is still in `to-do/`. This is the
work that continues in the new chat.

## Decisions + why (this session)

- **Questions are oblique + behaviour-eliciting, range beyond work** (e.g. "something you do others
  find slightly excessive", "a belief most people disagree with", "what you lose track of time
  doing"). Replaces the original corporate self-report set ("what do you value?") — self-report
  invites a performed brand, which is exactly what the project's grounding/faithfulness ethos
  rejects.
- **Pick any ~5 of 12, each capped at 280 chars (one tweet).** The cap is load-bearing, not
  cosmetic: 5 × ~280 chars ≈ 350 dense tokens is short enough for the `llama3.2:1b` rung to distill
  without producing mush. The user-good constraint == the cheap-self-hosted-rung constraint.
- **"Undercover" selection-pattern signal — considered and dropped (KISS).** The idea: infer
  personality from *which* questions the user chose to answer. Dropped because it's ungrounded by
  definition and only works on strong models — not worth the complexity now.
- **Sparse answers need no model change** — `answers` is a JSON dict keyed by question id; answering
  5 of 12 just stores 5 keys. Length cap enforced in the serializer (`validate_answers`), keys NOT
  pinned to the pool (frontend owns which render).
- **Questions live in their own module** (`spa/personality_questions.py`), signal in `spa/signals.py`
  — diverges from the guide's original "all in models.py"; guide updated to match.

## Open threads / risks

- **Distiller prompt is the part that needs live iteration** — dossier quality depends on it, and it
  was only validated structurally (mocked tests), never against a real model run.
- Existing users need a `PersonalityProfile` row backfilled (`makemigrations` won't do it) — see the
  guide's Verification step 1.
- The personal-paragraph side must NOT turn dossier characterisation into asserted cover-letter
  claims that `FaithfulnessCheck` would (rightly) flag — the dossier flavors, it doesn't assert.

## Next action

In the new chat: pick up `[backend]-personal-paragraph.md` (the second half) — wire the dossier into
the cover-letter personal-paragraph writer, capability-gated per `[[project-purpose-cv-showcase]]`.
Branch `backend/personal-paragraph` is kept for it.
