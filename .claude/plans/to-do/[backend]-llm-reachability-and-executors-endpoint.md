# [backend] llm-reachability-and-executors-endpoint

> **Guide 4** — *LLM-mode redesign*. Soft-depends on **guide 1** (uses `Mode`). This is the
> primitive the SPA (guide 5) reads to build the **model-first generate panel** — which executors
> exist, which are up, what each one offers — and where the **support-rung routing rekey** lands.
> Config stays a **single knob** (`LLM_URL`) — drop in the tower's URL and everything follows.
>
> **Backlog plan.** Full code + red tests at activation.

## Context / goal

The app must know whether the self-hosted ollama is reachable *before* a 25-minute task fails, so
the UI can offer/withhold the free self-hosted executor (public name **Dr. Jacll** — the naming
decision lives in guide 5) and show an offline state. There is no health check today.

**Model-first reshape (Lukas, 2026-07-15).** The user picks a *model* first; strategy and knobs
hang off that pick. So the discovery endpoint is **executor-shaped, not mode-shaped**: it returns
the pickable executors with per-executor capabilities, not a list of modes. `manual` ("No AI") is
not an executor and does not appear here — it is always available and lives entirely client-side
(guide 6).

**Pairs with `[infra] tower-inference-server`.** That guide provisions the tower as a GPU ollama
box reachable *only over a WireGuard tunnel from the VPS* — which is exactly why the free path is
intermittently unavailable (tunnel/box down, tower asleep). The single `LLM_URL` knob this guide
keeps points at that tunnel address (the SSRF allowlist already permits a VPN CIDR —
`validate_safe_llm_url`, `test_validators.py:75`); the reachability probe here is what notices the
tunnel is down and empties the offered default. Provisioning is infra; detection is this guide —
keep them decoupled (the probe must not assume WireGuard specifically; it just pings a url).

## Affected files

| Path | Change |
| --- | --- |
| `backend/llm_connector/conf.py` | `is_reachable(alias, user=None) -> bool` — resolve the alias, ping its endpoint with a short timeout, cache the result briefly (`django.core.cache`, ~15–30 s TTL keyed by resolved url). Free/self-hosted providers only need reachability; commercial providers count as "reachable if configured" (a key present) — don't burn a paid ping. |
| `backend/llm_connector/providers/ollama.py` (+ `custom.py`) | A cheap liveness probe — `GET {base}/api/tags` (ollama) with a 1–2 s timeout; `LLMTransportError`/timeout → not reachable. Add a `ping()`/`is_reachable()` on the adapter, or a small standalone in `conf.py` that does the urllib GET so adapters stay I/O-thin. |
| `backend/jac/views.py` (or `llm_connector/views.py`) | `GET /api/jac/executors/` (authed) → the shape below. **This is where the offered default is computed** — the free executor's key when the probe answers, else `null`. The DB `mode` field default stays `instruct`; the *offered executor* comes from here. |
| `backend/jac/views.py` (run create) | **Enqueue-time fail-fast:** `POST /generations/` whose **alias resolves to a free provider** (`is_free_alias`) consults the cached probe first; unreachable → **409** with `{"code": "llm_unreachable"}` instead of enqueueing a run that sits pending → fails minutes later. Commercial aliases never ping. |
| `backend/llm_connector/conf.py` | **Rekey `pick_alias` / `LLMGradePin`** from strength tiers to modes: a rung's `PREFERRED_MODE` resolves to the user's pin for that mode when set, **else the free self-hosted alias when reachable** (token-generosity default — Lukas is happy to run users' side tasks on his infra), **else the run's main alias**. (`free_only` is already alias-keyed since guide 1 — `is_free_alias`.) |
| `backend/llm_connector/models.py` | `LLMGradePin.strength` → a mode-keyed field: reversible data migration mapping old strengths → modes (mirroring `GRADE_TO_MODE`), **including the `unique_together ("user", "strength")` → `("user", "mode")` rekey**. `manual` is **not** a pin choice — no model runs in manual, so a manual pin is unrepresentable by design. |

## Endpoint shape

```json
{
  "executors": [
    {"key": "default", "provider": "ollama", "free": true, "available": true,
     "strategies": ["instruct"], "models": [], "knobs": []},
    {"key": "my-claude", "provider": "anthropic", "free": false, "available": true,
     "strategies": ["conversational", "instruct"],
     "models": ["claude-sonnet-5", "…"], "knobs": ["effort"]}
  ],
  "default": "default"
}
```

- **One row per executable alias**: the server's free self-hosted alias plus each of the user's
  own `LLMConfig` rows. `key` = the alias. **No `label` field** — display names are SPA branding
  ("Dr. Jacll" for the free executor, the user's own alias names for theirs); the API stays
  branding-free.
- **`available`**: free/self-hosted → the cached probe; commercial → key present (never ping paid).
- **`strategies`**: what the executor sensibly offers, first entry = suggested default. The free
  small model advertises `["instruct"]` only (holistic set-choice on a 1–7B is the old
  strong-on-small failure mode); commercial rows advertise `["conversational", "instruct"]` —
  conversational is the big-model default, instruct stays selectable for experimentation (Lukas).
- **`models` / `knobs`**: enriched by `[fullstack]-llm-model-catalog-and-knobs`; empty lists until
  that guide lands — this endpoint must not block on it.
- **`default`**: the executor the panel preselects and the auto-run (guide 5) fires on — the free
  alias when reachable, else **`null`**. `null` means "nothing runs for free right now": the SPA
  steers to manual curation. **The default never names a paid executor** — the app must not
  default anyone into spending money.
- Booleans and keys only — never response bodies, never resolved URLs (don't leak infra topology
  into the API shape).

## Approach / key decisions

- **Reachability = liveness, not correctness.** The probe answers "is *something* answering at this
  url", not "is the right model pulled". A pulled-model check is a nice-to-have (`/api/tags` lists
  models — could verify `model`/`embed_model` are present) but keep it optional; a false-negative
  that hides the free path is worse than a rare "model not found" at run time (which already
  surfaces as a clean failure).
- **The probe is SSRF-bounded.** It may only ping URLs that already passed `validate_safe_llm_url`
  (stored `LLMConfig` rows are validated on save; the operator's `LLM_URL` default is trusted) —
  never a URL taken from request input. `GET`, 1–2 s timeout, **no redirect following** (an answer
  is an answer; following a redirect walks the probe into arbitrary hosts).
- **Availability rules — concrete:** a commercial row counts as available iff it has a key
  (paid ≈ up, no pinging); the free executor counts iff the probe answers. Consequences: tower
  down + no keys → every executor unavailable, `default: null` → the SPA offers manual; tower down
  + an OpenAI key → the paid executor is pickable but `default` stays `null` (paid never runs
  unasked, so no auto-run); tower up → Dr. Jacll available and preselected.
- **Enqueue-time fail-fast closes the poll race.** The SPA's picture is up to ~TTL+poll-interval
  stale; a user (or the auto-run-on-create) can fire a free-alias run right as the tower dies. The
  create view re-checks the cached probe when the run's alias is free and 409s
  (`{"code": "llm_unreachable"}`) rather than enqueueing a doomed run. Trade-off accepted: a probe
  false-negative blocks a run that might have worked — the user retries seconds later; the
  alternative is the old failure mode this whole redesign exists to kill (a silent multi-minute
  pending → fail). Commercial aliases never ping. Guide 5 maps the 409 to the offline state + an
  executors refetch.
- **Cache the probe** so the endpoint (polled by the SPA) and every `pick_alias` call don't hammer
  ollama. Short TTL so bringing the tower up is noticed within ~30 s.
- **Support-rung default = free self-hosted (the generous nudge).** When a paid `conversational`
  run needs address extraction / grounding / snippet-embed, those default to the reachable ollama,
  not the user's paid model — saving their tokens. User-overridable via a pin. The SPA surfaces
  this as a hint (guide 5).
- **Single config knob.** Confirm nothing hardcodes the ollama url besides `settings.LLM["default"]`
  (`LLM_URL`). Swapping localhost → the tower's WireGuard-tunnel url is the only change needed; the
  SSRF allowlist (`LLM_URL_ALLOWLIST` / `LLM_URL_ALLOW_PRIVATE`) already covers a private tower
  address.

## Tests (written at activation)

- `llm_connector/tests/test_client.py` — `is_reachable` true/false against a mocked probe; result
  cached (probe called once within TTL).
- `llm_connector/tests/test_config.py` — `pick_alias` rekey: pin wins; no pin + reachable → free
  self-hosted; no pin + unreachable → run alias; `free_only` refuses a paid pin.
- `jac/tests/test_executors_api.py` (new) — `GET /api/jac/executors/`: anonymous → 403; the free
  executor's `available` flips with the mocked probe; a keyed commercial config appears with **no**
  probe call; an unkeyed one is `available: false`; `default` = the free key when reachable, `null`
  otherwise — **never a paid key**; the free executor advertises `["instruct"]`, commercial rows
  both strategies; no url-shaped string anywhere in the payload.
- `jac/tests/test_generation_api.py` — create fail-fast: a free-alias run with the probe mocked
  unreachable → 409 `{"code": "llm_unreachable"}`, no run row, nothing enqueued; a commercial-alias
  run with the probe unreachable still enqueues (no ping on paid).
- `llm_connector/tests/test_client.py` — the probe never follows a redirect and never fires for a
  commercial alias (key-presence short-circuit).

## Verification

Stop local ollama → `GET /api/jac/executors/` shows the free executor `available: false` and
`default: null` within the cache TTL, and a `POST /generations/` on the free alias 409s with
`llm_unreachable` (no pending run appears); start it → `available: true`, `default: "default"`,
and the same POST enqueues. A `conversational` run on a paid alias with ollama up routes support
rungs onto the free alias (assert via the request log / `pick_alias`).

## Results

<!-- Human fills this in. -->
