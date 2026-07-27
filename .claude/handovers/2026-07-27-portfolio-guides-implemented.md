# Handover — 2026-07-27 — portfolio phase implemented (reset-fix + guides 4 & 5)

## Goal
Lukas clicked through the mid-flight portfolio build, hit bugs (notably: "I feel lucky" → empty
page with **no way back except clearing cookies**), and we talked through direction. Decision:
**Path 1 — fix & finish the single-owner portfolio** (not the multi-tenant pivot; the per-visitor
questionnaire is the real moat, and the models already support both so no rollback needed). He then
said **"implement all portfolio guides tonight, I'll test in the morning"** → explicit *volatile /
just-code-it* phase, so I wrote the source directly on my own branch. Testing + merge stay with him.

## Where it stands — DONE, on branch `portfolio-all`
Cut off **`portfolio`** (NOT `main` — guides 1–3 aren't merged to main yet; the touched files only
exist on `portfolio`). Three clean commits, each a reviewable unit:

- `508c092` **portfolio reset-fix** — the localStorage dead-end
- `a99cbdd` **guide 4** — CV header QR + application portfolio link
- `6a23623` **guide 5** — owner manage UI (links + blocks + featured picker)

**Merge target = `portfolio`** (not main), `--no-ff`, after you've click-tested. Working tree still
carries only *your* uncommitted beautify plan-file moves (`to-do/beautify/` → `done/beautify/`) —
untouched.

### Verification I ran (green)
- `npx tsc -b` — **clean**.
- `npx vite build` — **clean** (also how `routeTree.gen.ts` got regenerated for the new routes).
- New unit tests: **reset (9), portfolio-qr (3), portfolio-link-form (14), contactLine (+3),
  render-templates portfolio (+2)** — all green.
- Full suite: **20 failed / 259 passed / 11 skipped** — the 20 failures are the **same 7 files** as
  before I started (baseline was also 20/7). **Zero new failures introduced.**

### Left for YOU (per the workflow)
- The manual click-through in each guide's `## Results` chapter (I filled none — that's yours).
- Backend is **untouched** — all three guides are frontend-only; the endpoints (portfolio-link
  action, manage links/blocks, revoke) already landed in guides 1–2 on `portfolio`.

## The 20 pre-existing red tests are NOT mine — and you already know them
They're the **deferred frontend fallout** from the 2026-07-27 *done-guide test reconciliation*
handover: `letter-doc` (appendParagraph/replaceStub/PERSONAL_STUB), `generations`
(aiShareBadge/qualityBadge), `snippet-form`, `personality`, `applications`, `cv-doc`, `export`.
Source↔test drift where the source on `portfolio` is behind the tests. Out of scope for "implement
portfolio guides"; I left them exactly as they were. **Grep-confirmed I add zero to that count.**

## Two pre-existing tsc breakages I DID fix (the branch didn't compile)
`tsc -b` was already red on `portfolio` (consistent with the `;ladsjfk` WIP commit). Both fixed in
the reset-fix commit, both legit:
1. `lib/portfolio/content.ts` `reorderByRank` was a **stub** (`console.log`, returned `void`) →
   implemented it for real (rank-ordered, never filters, stable) + tests. This is the `?q=`
   free-text finale that was silently doing nothing.
2. `lib/portfolio/questionnaire.ts:78` `walk()` — TS7022 on `opt`; added the `QOption | undefined`
   annotation.

## Deviations from the guide drafts (anchors had drifted since 2026-07-23)
- **Guide 4 / templates.tsx**: `Image` was **already imported**; `CvPages` had gained a `subtitle`
  prop the guide predated → I added `portfolio` *alongside* subtitle, didn't drop it.
- **Guide 4 / render test**: the guide wanted "URL lands in the PDF text layer" asserted on a
  render. `pdfTextRuns` can't cleanly extract page text once the QR **image XObject** shares the
  stream pool (binary noise). Moved that assertion to the **unit level** (`contactLine` test, where
  the belt actually lives); the render test keeps the robust **page-count invariance** check.
- **Guide 5 / content type**: `PortfolioLinkRow.content` keys are **optional** (`featured?`) — a
  fresh application link has `content: {}` before the sent-freeze. `link-editor` uses `?? []`.
- Everything else typed straight from the guides; all cv-doc/jac/ui anchors verified present first.

## Morning click-test checklist (condensed from the guides' Verification)
**Reset-fix** (anonymous window): questionnaire → `/explore` → **Start over** returns to the
questionnaire and *stays* (reload `/` = questionnaire, not bounced back). "I feel lucky" →
**Feeling lucky again** reshuffles. 404/empty states show an escape, never a blank trap.

**Guide 4**: application → export card → **Add portfolio link** → QR + URL + include-toggle. Export
PDF (cv/complete) → QR top-right page 1 only, URL in contact line, **page count unchanged** vs
toggle off. Copy → private window renders the tailored page. Revoke → 404.

**Guide 5**: header **Portfolio** → `/portfolio/links`. Blocks tab: text + image block (image
upload preview), tag with a Domain, favourite. Links tab: new manual link `for-jane`, pick/reorder
featured across sections + a block, tick explore domains → Create → private window renders in your
order. Slug `links`/`blocks` rejected. Edit an `application` link here → slug read-only.

## ⚠️ Data caveat for the "empty page"
The empty-lucky page is partly **data**: `PORTFOLIO_OWNER_USERNAME` defaults to `Lukas`
(settings.py:294), and `build_payload(lucky=True)` features **favourites only**. If the `Lukas`
career DB has no `favourite=True` rows / sparse entries, lucky looks empty (now shows the
empty-state + escape instead of a blank trap, but you'll still want real data). Flag a few
favourites and confirm the questionnaire domain names match the owner's jac Domain tags
(case-insensitive) — see `lib/portfolio/questionnaire.ts:4-6`.

## Open question for Lukas
Want me to take a pass at the 20 pre-existing frontend red tests (the letter/generations/snippet
drift) next, or is that still gated behind your cert-attachments + polished-render reconciliation?
