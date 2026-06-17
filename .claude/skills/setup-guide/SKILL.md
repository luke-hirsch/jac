---
name: setup-guide
description: Write a code-bearing implementation guide for a roadmap item into .claude/plans/to-do/, detailed enough for an intermediate engineer to implement by hand. Claude does NOT edit application/repo source — the guide is the only deliverable; the human types the code. Use when starting a new roadmap item or implementation chunk (working-style steps 1-4).
---

# setup-guide

Produce **one code-bearing setup guide** for a roadmap item or implementation chunk. This is
working-style phase 4 in CLAUDE.md: the AI plans and writes the guide, the human types the code.

## Hard rule

Do **not** edit application/repo source. The markdown guide is the only thing you write. The human
implements from it (this is how they stay on top of their codebase). The exception is an explicit
volatile/exploration phase the human opens — but even then, prefer a guide unless told otherwise.

## Before writing

1. Confirm which roadmap item (CLAUDE.md "roadmap") this serves.
2. Read the real current code for every file you'll touch — never guess APIs, names, or signatures.
   Reuse existing functions/utilities instead of inventing new ones; cite them by path.
3. If scope or approach is ambiguous, ask the human before writing the guide.

## Output

- A single markdown file at `.claude/plans/to-do/[area]-<slug>.md`, where `[area]` is `[backend]`,
  `[frontend]`, etc. If a matching stub already exists in `to-do/`, fill it rather than create a new file.
- Write so an intermediate engineer could implement it without you in the room.

## Guide contents

1. **Context / goal** — why this change, the intended outcome, link to the roadmap item.
2. **Affected files** — every path that gets created or changed, one line each on why.
3. **The code** — full, copy-paste-ready code per file, in the order it should be typed. Match the
   surrounding code's style and idioms. Call out anything subtle inline.
4. **Tests** — the test code (AI writes tests; human runs them).
5. **Verification** — exact end-to-end steps the human runs to confirm it works (commands, expected
   output, what "done" looks like).

## After

The human types and tests. When the work lands, `/update-claude` refreshes CLAUDE.md + memory and
moves this guide `to-do/` → `done/`.
