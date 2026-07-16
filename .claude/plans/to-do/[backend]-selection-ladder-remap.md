# [backend] selection-ladder-remap

> **Guide 2** — *LLM-mode redesign*. Depends on **guide 1 (mode-enum-and-plumbing)** being
> merged. This guide makes the pipeline speak `Mode` natively and **deletes the `mode_to_grade`
> shim and jac's `Grade` vocabulary** (this branch rekeys the enum's last consumers, so the class
> goes with them — see guide 1's compat ledger). Behavior-preserving for the three AI modes;
> `manual` is handled in guide 6.
>
> **Backlog plan.** Full copy-paste code + the red test files are written when this guide is
> activated (they reference the `Mode` API guide 1 lands and would drift if pinned now). The design
> below is precise enough to start from.

## Context / goal

After guide 1, `GenerationRun.mode` is the source of truth but the selection/writer code still
branches on the internal `Grade` (`"light"/"standard"/"strong"`), reached via `mode_to_grade`.
This guide replaces those string-literal branches with mode-native logic so the redesign is real,
not a translation layer, and removes the temporary shim.

## Affected files

| Path | Change |
| --- | --- |
| `backend/jac/filter.py` | `CVFilter.__init__(grade=...)` → `mode=...`; `output()` dispatch keyed on `Mode` (`conversational`→holistic, `instruct`→ranked, **`manual`→`_group_all()`** — keep-all, zero I/O). Embed-only survives as `instruct`'s degrade target and guide 3's prefilter, **not** as a dispatch branch. (The `free_only` cost guard is already alias-keyed since guide 1 — `is_free_alias`.) |
| `backend/jac/cv.py` | `FILTER_GRADE`/`filter_grade`/`normalize_grade` → `filter_mode`/`normalize_mode`; `filter_cv(grade=...)` → `filter_cv(mode=...)`. |
| `backend/jac/cover_letter.py` | Every `self.grade == "strong"/"standard"/"light"` (lines ~369, 529, 577, 583, 616, 669) → mode equivalents: `strong`→`conversational`, `standard`→`instruct`; the **`light`-only paths retire** (no embed-only mode anymore). Where `light` meant "skip the expensive step", `instruct` keeps the standard-path behavior; where it vetoed the personal paragraph, the gate becomes **purely capability-driven** (can-web-search + researched + personality — no mode veto). Rename the `grade=` params to `mode=`. |
| `backend/jac/llm_prompts.py` | `LetterWriter`-family `self.grade` branches (lines ~672, 692, 700) → mode; the `_GRADE_CLAUSE`/`_REWRITE_TAX`/`_CRITIC_GRADES` dicts rekeyed to modes. `PREFERRED_GRADE` on the rung classes → `PREFERRED_MODE` (values become modes; see guide 4 for pin rekeying). **Simplify while rekeying (Lukas's follow-up):** collapse the per-grade clause branching toward one lean clause per mode rather than a mechanical 3→4 rename — the explicit-mode world needs less hedging. (Deeper prompt shrinking rides along with guide 3's shortlist, which bounds the entry list the prompt must reason over.) |
| `backend/jac/tasks.py` | Drop `mode_to_grade`; pass `mode=run.mode` straight through to `CV.filter_cv`/`CoverLetter`. (`free_only=is_free_alias(...)` already landed in guide 1 — keep it.) |
| `backend/jac/models.py` | **Delete** `MODE_TO_GRADE`, `mode_to_grade`, **and the `Grade` class + `normalize_grade`** — this branch rekeys their last consumers (`filter.py`, `cv.py`, the two eval commands; grep-verified 2026-07-16, nothing else imports them — the connector's strength strings are its own copy, `render.py`'s "Grade:" is a degree grade). `GRADE_TO_MODE`/`KNOWN_MODE_INPUTS`/`normalize_mode`'s legacy branch stay (guide 5's — the SPA still sends `grade`). |
| `backend/jac/serializers.py` | The read serializers' compat `grade` now derives from `mode` via a **file-local 3-literal dict** (`{"instruct": "standard", "conversational": "strong", "manual": "light"}`) — deliberately local so its guide-5 deletion is one file; comment it `# compat: dies with guide 5`. |
| `backend/jac/management/commands/{cover_letter,cv_eval}.py` | `opts["grade"] or get_alias_strength(...)` → a `--mode` option defaulting to `instruct`. These commands are the last `get_alias_strength` callers besides the connector report + chat gate. |

## Approach / key decisions

- **The `output()` ladder keeps the same shape, keyed on modes:**
  - `conversational` → `_strong_selection()` → `_select_holistic`, degrade to the instruct-ranked
    path;
  - `instruct` → `_standard_scores()` → `_select_ranked`, degrade to embed `_select` (guide 3
    fronts this with the shared embed prefilter when the embedder is reachable — write the branch
    so a pre-filtered `entries` subset drops in cleanly);
  - `manual` → `_group_all()` (keep everything unscored, **zero LLM/embed calls**). Normal
    operation never gets here — guide 1 rejects manual at the API and fail-fasts it in the task —
    but the filter's own branch means even a future caller bug can't turn "No AI" into network
    traffic. Keep-all is also the semantically right answer: no AI means nothing is filtered; the
    human prunes.
- **Autodetect's death is scheduled, not optional** (Lukas, 2026-07-15: grade select *and*
  autodetect go entirely). Jac's `Grade`/`normalize_grade` die **here** (see Affected files — this
  branch rekeys their last consumers, so keeping the enum would be dead code). The connector side
  stays for now: after this guide, `get_alias_strength`'s only remaining callers are the
  connector's per-alias `strength` report (deleted by **guide 5**, with the SPA reads), the
  `llm_check` strength line, and the chat capability gate (both **guide 7**, which owns the
  connector teardown: `get_alias_strength`, `_autodetect_strength`, `_STRENGTHS`, the `strength`
  config-key handling, the `LLM_STRENGTH` env knob). Don't chase the connector removal in this
  branch.
- **Rename `PREFERRED_GRADE` → `PREFERRED_MODE`** on the rung classes (`Embed`, `Instruct`,
  `AddressExtract`, `FaithfulnessCheck`, `SnippetEmbed`, …). Values: support rungs want `instruct`
  (the cheap tier); the holistic selector is `conversational`. The actual pin routing rekey lives
  in guide 4 (`pick_alias`).

## Tests (written at activation)

- `test_cv_selection.py` — parametrise the existing rung tests over `Mode` instead of `Grade`;
  assert `conversational`/`instruct` pick the holistic/ranked strategy and that the degrade chain
  still fires on empty scores (holistic→ranked→embed); `mode="manual"` → `_group_all` keep-all with
  the rung/embed mocks **never called**.
- `test_cover_letter.py` — the grounding-on-`conversational` and critic-on-`instruct` gates key on
  modes; the personal-paragraph gate is purely capability-driven (no mode veto — an instruct run on
  a search-capable alias gets a real paragraph).
- `test_llm_rungs.py` — `PREFERRED_MODE` values; `_GRADE_CLAUSE`→mode-keyed clause selection.
- `test_models.py` — assert `mode_to_grade`/`MODE_TO_GRADE`/`Grade`/`normalize_grade` are **gone**
  from `jac.models` (red until deleted). This one is scaffolding, not a fixture — delete the
  absence-assertion once the branch merges; a permanent "X doesn't exist" test is noise.

## Verification

Full backend suite green; a live `standard`/`instruct` and `strong`/`conversational` run through
the SPA produces the same tailored CV + letter as before the redesign.

## Results

<!-- Human fills this in. -->
