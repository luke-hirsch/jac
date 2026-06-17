# Handover — CvEntry `favourite` flag: ranking nudge, per-type cap, full-stack wiring

## Goal

Let the user pin entries as **favourites** so they get a small ranking boost in the CV pipeline,
with a per-type cap on how many can be pinned, wired end-to-end (model → API → CV filter → React
CRUD UI). Plus a sortable star column in every entry table and a `★` marker in `cv_eval` output.

## Where it stands

**All implemented, working-tree only — NOT committed.** `tsc -b` and `python manage.py check`
both pass clean. Tests are written but **not run** (testing stays with Lukas per CLAUDE.md).

Done this session (uncommitted):
- `backend/jac/models.py` — `favourite` field already existed (Lukas added + migrated). Added
  `CvEntry.FAVOURITE_LIMIT` (None default) + `favourite_count()` classmethod + `clean()` that
  enforces the cap. Per-type limits: Skill 10, Job 4, Education 2, Certification/Project/Language 3.
- `backend/jac/serializers.py` — new `FavouriteLimitMixin` (re-checks the cap at the API boundary
  since DRF skips `full_clean`); mixed into all 6 entry serializers; `favourite` added to each
  `fields` list.
- `backend/jac/cv.py` — `favourite` surfaced into `_flatten_entries`; `CVFilter._FAVOURITE_BONUS =
  0.05` applied post-propagation in `_select`.
- `backend/jac/views.py` — `"favourite"` added to `ordering_fields` on all 6 viewsets.
- `backend/jac/render.py` — `CvRender._sections` prefixes `★ ` to favourite entry headings (shows
  in `cv_eval`'s `<slug>.cv.md`).
- `backend/jac/tests.py` — added `FavouriteLimitModelTests`, `FavouriteLimitAPITests`,
  `FavouriteOrderingAPITests`, `CVFavouriteBonusTests`.
- Frontend: `favourite: boolean` on all 6 row types in `lib/queries/jac.ts`; new
  `components/cv/favourite-field.tsx` (editor checkbox) and `components/cv/favourite-column.tsx`
  (sortable star column); all 6 routes (`skills/jobs/education/certifications/projects/languages`)
  wired — schema + both `initial` branches + editor field + `favFirst` state + star column +
  ordering param.

Migration `0002_certification_favourite_..._and_more.py` is **untracked** (Lukas generated it);
needs to go in the same commit.

## Decisions + why

- **Bonus 0.05, applied after propagation** — deliberately below the smallest non-zero section
  floor (education 0.15) so a favourite the scorer rates ~0 still can't cross its drop threshold.
  Favourites tilt close calls; they don't resurrect irrelevant entries. (Lukas's explicit ask.)
- **Cap enforced in two places** — `model.clean()` (admin/forms) + `FavouriteLimitMixin`
  (API), sharing `favourite_count()`. DRF never calls `full_clean`, so the serializer check is the
  one that actually fires on API writes.
- **Star sort = server-side, favourites-first only (2-state toggle)** — DRF's `ordering` param
  *replaces* the queryset sort, so each route prepends `-favourite` and keeps its natural sort as
  the secondary key (e.g. jobs `-favourite,-started`, skills `-favourite,${expSort||"name"}`).
  Chose 2-state over skills' 3-state cycle because "favourites last" isn't useful.
- **Caps currently equal the one-page targets** (10/4/2/3/3/3) from the cv_eval rank guide. Flagged
  to Lukas as a possible "tighten to a few pins" change — awaiting word.

## Open threads / risks

- **Tests unrun.** Run them before committing (command below).
- **`backend/llm_connector/management/commands/llm_check.py` (modified) and
  `.claude/plans/to-do/[backend]-llm_check-autodetected-strength.md` (untracked) are NOT part of
  this task** — they're separate work in the tree. Don't bundle them into the favourite commit.
- `.claude/plans/to-do/[backend]-cv-eval-rank-feedback.md` shows as modified but that work already
  **landed** (commit `7f03cb2 "cv_eval update to show also ranks"`); the plan should move to
  `done/` — `/update-claude` handles that.
- ⚠️ **Security:** Lukas's `.env` exposed a live OpenAI API key this session. Needs rotating +
  confirm `.env` is gitignored. Not code, but don't lose track of it.

## Next action

Run **`/update-claude`** (the reason for this handover): refresh CLAUDE.md current-state/roadmap
for the favourite feature, distill any durable favourite-ranking decision into memory, and move
`[backend]-cv-eval-rank-feedback.md` to `done/`. Before/around that, run the favourite test suite:

```bash
cd backend && python manage.py test \
  jac.tests.FavouriteLimitModelTests jac.tests.FavouriteLimitAPITests \
  jac.tests.FavouriteOrderingAPITests jac.tests.CVFavouriteBonusTests -v 2
```

Then commit the favourite work (models/serializers/cv/views/render/tests + migration 0002 + the two
new frontend components + 6 routes + jac.ts) — **excluding** the unrelated `llm_check` changes.
