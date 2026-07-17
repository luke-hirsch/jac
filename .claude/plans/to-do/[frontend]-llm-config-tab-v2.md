# [frontend] llm-config-tab-v2

> **SPA phase, guide 2.** New 2026-07-17 — no open guide covered this surface: the original
> `[frontend]-llm-config-tab` (in `done/`) built the alias-era tab, and the executor rework
> replaced the API underneath it. The account LLM tab still edits alias/url/model/extra rows
> against endpoints that no longer exist.
>
> **Backlog plan.** Full code + red tests at activation.

## Context / goal

`LLMConfig` collapsed to **one credential per commercial provider**. The landed API
(`/api/llm/configs/`): `{id, provider, default, api_key (write-only), has_api_key,
created_at, updated_at}` — providers `anthropic`/`openai` only (`ollama` is rejected with
"HirschAI is built in"; the tower is the operator's system row), unique per `(user,
provider)`, `default` exclusive ("my runs go to this provider"; server-side, a keyless
default doesn't count). The whole structured-mask / extra-JSON / url machinery is dead
vocabulary.

The tab becomes **one card per commercial provider**: key status (`has_api_key`), set/replace
key (write-only, never echoed), default toggle, the `check` round-trip button, delete. Plus a
HirschAI info line (not configurable here — surface its `reachable` state from
`useExecutors()`).

## Affected files

| Path | Change |
| --- | --- |
| `lib/queries/llm.ts` | Config types + CRUD rewritten to the five-field shape; the mask helpers (`toPayload`/`rowToState`/`switchProvider`/…) shrink drastically or die. |
| `routes/_authenticated/account/llm.tsx` | Cards-per-provider layout replacing the table + structured mask. |
| `frontend/tests/lib/llm.test.ts` | Rewrite for the surviving helpers (payload build; PATCH without `api_key` keeps the stored key). |

## Approach / key decisions

- **Provider list rendered from `useExecutors()`'s commercial rows**, not hardcoded — a
  future catalog provider lands frontend-free.
- **`api_key` semantics unchanged**: write-only, omit on PATCH to keep the stored key; the
  UI shows only the `has_api_key` badge.
- **Default toggle**: optimistic flip + refetch — the server enforces exclusivity (last
  write wins), the UI never juggles it locally.

## Tests (at activation)

- payload builders: create (provider + key), key replace, default toggle, PATCH-without-key
  keeps stored key; types carry no url/model/extra/alias fields.

## Verification

`tsc -b` + vitest green. Click-through: add an Anthropic key → card shows "key set"; check →
pong latency; toggle default; replace the key without touching default; delete the row. The
generate panel (guide 1) reflects the new config on its next executors refetch.

## Results

<!-- Human fills this in. -->
