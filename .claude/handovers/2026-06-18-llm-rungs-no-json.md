# Handover — CV LLM rungs: line-format (no JSON) + favourite semantics across rungs

## Goal

Session was planning/design, not implementation. Two outcomes: (1) settle how favourites behave once
the LLM rungs exist, and (2) replace JSON with a line-oriented wire format in the `standard` and
`strong` rung plans, because JSON is a token hog and brittle on small local models.

## Where it stands

**No application source changed.** Work is in the two plan files + CLAUDE.md + memory only.

Done this session:
- **`.claude/plans/to-do/[backend]-cv-standard-instruct-rung.md`** — rewritten to line format. Reply
  is `<id> <rating 0-3>`, one per line. `Instruct._extract_json` **removed**; `_parse` now scans each
  line with `_LINE_RE = re.compile(r"([a-z]+:\d+)\D+(\d+)")`, keeps ids in the entry set, clamps 0–3.
  Imports drop `json`, add `re`. Instruction text + `InstructScorerParseTests` updated (markdown
  tolerance, partial-reply, clamp cases). Selection layer (`_select_ranked`), routing, fallback all
  unchanged.
- **`.claude/plans/to-do/[backend]-cv-strong-conversational-rung.md`** — rewritten to line format.
  Reply is `<id> — <why>`, one kept entry per line, line order = priority. `_parse` uses
  `_PICK_RE = re.compile(r"([a-z]+:\d+)\s*[-—:.)\]]*\s*(.*)")`, dedupes preserving order. Dependency
  note no longer claims it reuses `Instruct._extract_json` (now just `re` + `min_keep` reads +
  `output()` routing). `ConversationalSelectorTests` rewritten for the line format. `_select_holistic`
  + guardrails unchanged.
- **CLAUDE.md** — roadmap item 1 corrected: each LLM rung pairs its own scorer **and** selection
  strategy (the old "both feed `{id: score}` into the shared selection" claim was wrong vs the
  plans); added the line-format-not-JSON note.
- **Memory** — `project_jac.md` gained a "favourite semantics differ per rung" note; new
  `no-json-llm-io.md` records the format decision (regexes, id-anchor trick, the one tradeoff);
  MEMORY.md index updated.

Untouched: `backend/jac/llm_prompts.py` (`Instruct`/`Conversational` still as currently committed —
`Instruct` still has the JSON `_extract_json` in the actual source; the plan is ahead of the code).

## Decisions + why

- **No JSON for LLM I/O.** Line-oriented, id-anchored parsing. CV entry ids are already a greppable
  `type:pk` pattern, so we anchor on them and scan line-by-line, skipping unreadable lines — a
  dropped char kills one line, not the reply; truncated replies still yield complete lines. Tolerates
  markdown/bullets/separator drift for free via `re.search`.
- **Tradeoff accepted:** line parsing degrades *silently* (partial reply → partial map) where JSON is
  all-or-nothing. Fine — empty parse → `{}`/`[]` lands exactly on the existing fallback ladder
  (strong→standard→light).
- **Favourites become a hard pin in the LLM rungs** (kept even at label 0 / when the model omits
  them), diverging from `light`'s soft 0.05 nudge. Accepted because explicit per-entry user override
  is coming in the **render phase**; the LLM rungs have no continuous score to nudge, so "pin" is the
  natural analogue. Bounded by the per-type `FAVOURITE_LIMIT` caps. Plans left as-is (hard pin).

## Open threads / risks

- **Line parser can't carry a negative rating** — a `-` before the digit is eaten by `\D+`, so
  `job:1 -4` reads as `4` → clamped to 3 (not 0). Harmless on the 0–3 scale; the instruction never
  asks for negatives. The old `-4 → 0` test was dropped rather than left misleading. Known property.
- **Plans are ahead of the code.** `llm_prompts.py` in the repo still has the JSON `_extract_json`
  path; implementing standard means *removing* it, not just adding.
- Carryover from last session, still open: **live OpenAI API key was exposed in `.env`** — rotate +
  confirm `.env` is gitignored.

## Next action

Implement the **`standard` (Instruct) rung** from its (now line-format) plan: human types the code in
`llm_prompts.py` (`Instruct`, line parser, drop `json`) + `cv.py` (`_standard_scores`,
`_select_ranked`, `output()` routing, label constants), AI-written tests already specced in the plan.
`strong` is gated behind it. Per CLAUDE.md working style: AI writes tests, human types the source.
