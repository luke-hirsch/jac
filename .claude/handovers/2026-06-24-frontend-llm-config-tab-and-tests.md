# Handover — frontend LLM-config tab + first frontend test harness

**Date:** 2026-06-24
**Branch:** `frontend/llm-config-tab` → merged to `main` (`--no-ff`) at wrap-up.

## Goal

Ship the account **LLM-config tab** (frontend CRUD over `/api/llm/configs/`, the unblocker for the
whole JAC pipeline since nothing runs without a configured alias), and stand up the project's **first
frontend test harness** (vitest) by covering the pure logic in `frontend/src/lib/`.

## Where it stands

**Done & merged:**
- **LLM-config tab UI** (typed by Lukas): `frontend/src/lib/queries/llm.ts` (271 lines — provider
  specs + pure form↔payload helpers `toPayload`/`rowToState`/`switchProvider`/`buildStructuredExtra`/
  `parseExtraJson`) and `frontend/src/routes/_authenticated/account/llm.tsx` (343 lines — the tab),
  plus the `account.tsx` tab link and regenerated `routeTree.gen.ts`.
- **Test harness** (written by AI): vitest added to `frontend/package.json`
  (`test`/`test:watch` scripts). Tests live in a **separate `frontend/tests/` tree mirroring `src/`**
  (not colocated — Lukas's call), imported via the `@/` alias. `frontend/tests/tsconfig.json` gives
  the editor IntelliSense while staying out of the `tsc -b` build.
- **lib coverage** (8 files under `tests/lib/`): `api`, `auth`, `auth-flow`, `form`, `reauth`,
  `webauthn`, `queries/paginated`, plus the pre-existing `queries/llm.test.ts`.

**Untouched / deferred:**
- **Components and hooks** (`useAuth`, `useDebounced`) — no tests. Need `@testing-library/react` +
  jsdom (not installed). Deliberately deferred until components get styled.
- The two stale to-do plans (`[backend]-setup-resume-creation-pipeline`,
  `[frontend]-setup-crud-api-calls-resumesnippet-model`) were deleted as housekeeping in this branch.

## Decisions + why

- **Tests in a separate `tests/` tree, not colocated** — Lukas finds colocated `*.test.ts` cluttered;
  also keeps them out of the production `tsc -b` build, which matters because `llm.test.ts` is the
  red-first spec for `llm.ts` and would otherwise break the build before/independent of the source.
- **Node env, no jsdom** — `lib/` is pure logic; the few functions touching `fetch`/`document`/
  `window` are covered with `vi.stubGlobal`. jsdom only buys us component/hook tests, deferred.
- **Provider masks over raw `extra` JSON** (commercial providers) — see the done guide; `api_key`
  stays write-only end to end.

## Open threads / risks

- **Tests not yet run by a human.** AI wrote them as green-on-arrival characterisation tests but did
  not execute the suite (workflow: human runs/debugs). Expected first run: the 7 `lib/` files pass;
  `llm.test.ts` passes *only if* `llm.ts` exports match its imported names
  (`PROVIDER_SPECS`/`toPayload`/`rowToState`/`switchProvider`/`buildStructuredExtra`/`parseExtraJson`
  + the `ConfigFormState`/`LLMConfigRow`/`Provider` types). If any name drifted, that file is red.
- Watch the zod v4 surface (`z.string().min()` etc.) and Node-global assumptions (`Headers`, `atob`/
  `btoa`, `Buffer`) — all should be fine on Node 24, but that's where a surprise failure would come
  from.

## Next action

Run `npm test` in `frontend/` and confirm the suite is green (especially `tests/lib/queries/llm.test.ts`
against the new `llm.ts`). Then, if wanted, install `@testing-library/react` + jsdom and add hook
tests for `useAuth`'s status derivation and `useDebounced`.
