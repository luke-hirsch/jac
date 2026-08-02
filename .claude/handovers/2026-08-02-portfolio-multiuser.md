# Handover — portfolio multi-user hosting + flow-rework finish (2026-08-02)

## Goal

Finish the portfolio into a **multi-user, public** product: one SPA build serving apex / `app.` /
`<handle>.` hosts, owner resolved from the request host, a reworked per-visitor anonymous flow
(flat-form questionnaire + AI intro), and open signup so a recruiter can build a live portfolio on
Lukas's domain. Two phases: `portfolio-rework` (single-owner flow rework) → `portfolio-multiuser`
(host-based, multi-owner).

## Where it stands

**All six guides done and in `.claude/plans/done/portfolio/`; `to-do/` is empty.** Everything is
committed on branch **`portfolio-flow-rework`**. Unit tests green both sides (Lukas confirmed).

- **Backend (`portfolio-rework/[backend]-portfolio-flow`)** — landed + green (Results: 41/41). Owner
  `iexact` fix, `owner_domains`, `section_order`, `is_default` fallback link, `PortfolioIntroWriter`
  + `build_intro`, `PortfolioMetaView`/`PortfolioIntroView`, Django `/` landing.
- **Backend (`portfolio-multiuser/[backend]-host-resolution`)** — landed + green. `owner_for_host`/
  `resolve_owner`/`_configured_owner`, `mint_handle`, `public_portfolio_url`, per-user slugs
  (`_dedupe_slug`), `UserProfile.handle`, migration `0006`. Results-chapter issues all resolved (the
  migration `IntegrityError` and the stale `get_owner` import — no `get_owner` refs remain).
- **Infra (`portfolio-multiuser/[infra]-subdomain-hosting`)** — dev config landed (settings
  `ALLOWED_HOSTS`/CSRF/CORS wildcard, `vite.config.ts` host-preserving proxy, `VITE_BASE_DOMAIN`).
  Prod nginx/DNS/TLS recipe is in the guide, **not deployed**.
- **Frontend (`portfolio-multiuser/[frontend]-host-aware-routing`)** — landed. `lib/host.ts`,
  host-branch `routes/index.tsx`, `routes/$slug.tsx`, filled `explore-result.tsx`, `stamp.ts`
  focus/tone, `useNativeMeta`/`useNativeIntro`. `me.tsx`/`explore.tsx` deleted.
- **Frontend (`portfolio-rework/[frontend]-portfolio-flow`)** — this session's finish: the flat-form
  questionnaire (`lib/portfolio/questionnaire.ts` + `components/portfolio/questionnaire.tsx`) that
  host-aware-routing had deliberately left undone. Turned the last 8 red `flow.test.ts` tests +
  the one `tsc` error (`index.tsx → hasAnswer`) green.
- **Fullstack (`portfolio-multiuser/[fullstack]-open-signup`)** — landed. Writable `handle` +
  `validate_handle`, `generation` throttle scope, profile/signup disclosure copy. `HandleClaimTests`
  green. `ACCOUNT_ALLOW_SIGNUPS` stays False in dev (the launch toggle).

## Decisions + why

- **Owner from host, not a setting** — the multi-user pivot. `BASE_DOMAIN` +
  `PORTFOLIO_ORIGIN_TEMPLATE` are the only two knobs; a neutral-domain move is config-only.
- **Per-origin localStorage = free multi-owner stamp isolation** — no handle in the stamp.
- **Session cookie only on `app.`** — public subdomains always anonymous; anonymous POSTs are
  CSRF-exempt under DRF SessionAuth. Cost: owner self-preview bumps its own visit counter (accepted).
- **Hybrid engine** — selection is all embeddings; the *only* generative anonymous call is the
  HirschAI-only, 6/h-throttled AI intro that degrades to `""`. Keeps "never commercial in the public
  path" structurally true.
- **The flat-form questionnaire finish stayed in the rework guide** (host-aware-routing pointed back
  to it) — so the rework frontend guide was slimmed to just that piece before implementing.
- **11 frontend test skips are unrelated** — dormant executor-rework SPA-phase guides
  (`[frontend]-entry-pins-ui`, `[frontend]-manual-no-run-mode`); each guide's step 0 unskips them.

## Open threads / risks

- **NOT merged to `main`.** main is a *strict ancestor* of `portfolio-flow-rework` (zero divergence,
  conflict-free merge) but has **none** of the portfolio work — every phase (reset-fix, guides 4–5,
  rework, multiuser) lives only on the branch. Merging brings all of it in at once. Left for Lukas
  to decide (his established pattern is to keep portfolio work on the branch).
- **Live/prod verification is entirely pending** — the guides' Results chapters don't log it:
  wildcard DNS, DNS-01 wildcard TLS, the nginx apex/`app.`/`*.` host-split, live signup + email
  verify, multi-owner click-through (`jane.localhost` vs `lukas.localhost`), live AI intro with the
  tower up, and the generation soft-cap 429.
- Minor: `PortfolioLinkSerializer` keeps both `validate` (per-user active-slug uniqueness) and
  `validate_slug` (format) — intentional, they do different jobs; not merged.
- `.prettierignore` must keep covering `backend/**/templates/` or format-on-save re-breaks the
  Django landing template's `{% %}` tags.

## Next action

Either (a) live-verify the multi-user flow in dev — `runserver 0.0.0.0:8000` + `npm run dev`, open
`lukas.localhost:5173` and a second user's `<handle>.localhost:5173`, confirm different domains per
host + the AI intro (tower up) — and log it in the done guides' Results chapters; or (b) decide the
`main` merge (`git checkout main && git merge --no-ff portfolio-flow-rework`). No coding work is
open — `to-do/` is empty.
