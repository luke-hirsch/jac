# [fullstack] llm-config-check — per-row connectivity check on the LLM config tab

**branch**: `fullstack/llm-config-check`

## Context / goal

The LLM config tab stores a config and shows `key stored ✓` — but the only way to learn whether
the alias actually *works* (right key, reachable URL, existing model) is to run a generation and
watch it fail. The CLI already solves this: `llm_check` round-trips a one-word completion per
alias and prints `OK … latency=812ms` or `FAIL … error=…`.

This chunk gives that same probe an API twin and a button:

- **backend**: `POST /api/llm/configs/<pk>/check/` — a detail action on `LLMConfigViewSet` that
  mirrors `Command._check_alias` in
  `backend/llm_connector/management/commands/llm_check.py:35`: build
  `LLMClient(config.alias, user=request.user)`, time
  `client.complete("Respond with exactly one word: pong")`, return
  `{ok: true, latency_ms}` or `{ok: false, error}`.
- **frontend**: a per-row **Check** button next to Edit/Delete on
  `frontend/src/routes/_authenticated/account/llm.tsx`, with the result shown inline under the
  model line ("OK · 812 ms" muted / error text destructive).

Design decisions (settled in conversation):

- **Completion-only probe** — exactly what `llm_check` does. No embed/web-search matrix.
- **Failure is a result, not an HTTP error.** The endpoint did its job by *reporting* the broken
  config, so both outcomes are HTTP 200 with an `ok` discriminator. HTTP errors stay reserved for
  the endpoint itself misbehaving (404 wrong owner, 401/403 unauthenticated).
- **Checks the saved row** (pk-based), not unsaved form state — save first, then check. Keeps the
  `api_key` write-only contract untouched.
- The probe goes through `LLMClient`, so it lands in `LLMRequestLog` like any other call (spend
  audit for free) and inherits the one transport retry (`client.py:64` `_with_retry`) — a check
  against a dead host takes ~2× the adapter timeout + 2 s. Acceptable for a button click; the
  frontend shows "Checking…" meanwhile.
- No new throttle. The action triggers one outbound LLM call per click — the same capability the
  generation endpoint already exposes, and it's logged. If open signup lands, a DRF throttle scope
  on this action belongs in that hardening pass.

## Affected files

| file | why |
| --- | --- |
| `backend/llm_connector/views.py` | add the `check` detail action (+ `time`, `action`, `LLMClient` imports) |
| `backend/llm_connector/urls.py` | **no change** — the router generates `/configs/<pk>/check/` from the `@action` automatically |
| `frontend/src/lib/queries/llm.ts` | `CheckResult` type, `checkResultLabel` helper, `useCheckConfig` mutation |
| `frontend/src/routes/_authenticated/account/llm.tsx` | per-row Check button + inline result state |
| `backend/llm_connector/tests/test_api.py` | red tests for the action (written by AI, already on disk) |
| `frontend/tests/lib/queries/llm.test.ts` | red tests for `checkResultLabel` (written by AI, already on disk) |

## The code

### 1. `backend/llm_connector/views.py`

Three import additions, then the action on `LLMConfigViewSet`.

Top of the file — add `time` (stdlib, first import block) and extend the DRF/rest imports:

```python
import time

from drf_spectacular.utils import OpenApiResponse, extend_schema
from lukehirsch.permissions import IsOwner
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from llm_connector.base import LLMAdapter
from llm_connector.client import LLMClient
from llm_connector.conf import FALLBACK_ALIAS, get_alias_config, get_alias_strength
from llm_connector.models import LLMConfig, LLMRequestLog
from llm_connector.registry import get_adapter_class
from llm_connector.serializers import LLMConfigSerializer, LLMRequestLogSerializer
```

> `LLMClient` must be imported at module level (not inside the action) — the tests patch
> `llm_connector.views.LLMClient` so no probe ever leaves the test suite.

Then, inside `LLMConfigViewSet` (after `get_queryset`):

```python
    @extend_schema(
        request=None,
        responses=OpenApiResponse(
            description="{ok: true, latency_ms} or {ok: false, error}"
        ),
    )
    @action(detail=True, methods=["post"])
    def check(self, request, pk=None):
        """Connectivity probe for one config — the API twin of `llm_check`.

        Round-trips a one-word completion through the row's alias exactly as the
        pipeline would resolve it. A failed probe is a *result*, not an HTTP
        error: both outcomes are 200 with an `ok` discriminator.
        """
        config = self.get_object()
        try:
            client = LLMClient(config.alias, user=request.user)
            start = time.monotonic()
            client.complete("Respond with exactly one word: pong")
            latency_ms = int((time.monotonic() - start) * 1000)
            return Response({"ok": True, "latency_ms": latency_ms})
        except Exception as exc:  # noqa: BLE001 — any failure is the check's finding
            return Response({"ok": False, "error": str(exc)})
```

Subtleties:

- `self.get_object()` runs the user-scoped queryset + `IsOwner`, so another user's pk is a 404
  before any client is built — same shape as the rest of the viewset.
- `LLMClient(...)` **construction** sits inside the `try`: it can itself raise (unknown provider,
  missing optional SDK, decrypt failure), and that must surface as `{ok: false}` — never a 500.
  Mirrors the command, which wraps the whole `_check_alias` body.
- The probe resolves by **alias** (`get_alias_config`), not by row fields — deliberately identical
  to what the pipeline will do with this alias at generation time.
- `request=None` in `extend_schema` stops drf-spectacular from advertising the config serializer
  as the request body (the action takes none).

### 2. `frontend/src/lib/queries/llm.ts`

New section between the *resolved alias capabilities* block and the *query hooks* block (so the
pure helper sits with the other pure helpers, the hook with the hooks):

```ts
/* ---------- connectivity check ---------- */

/** Result of POST /api/llm/configs/<id>/check/ — the API twin of `llm_check`.
 *  A failed probe is a result (ok: false), not an HTTP error. */
export type CheckResult =
  | { ok: true; latency_ms: number }
  | { ok: false; error: string };

/** Inline row label: "OK · 812 ms" on success, the raw error text on failure. */
export function checkResultLabel(r: CheckResult): string {
  return r.ok ? `OK · ${r.latency_ms} ms` : r.error;
}
```

And with the other hooks at the bottom (no cache invalidation — a probe changes nothing):

```ts
export function useCheckConfig() {
  return useMutation({
    mutationFn: (id: number) =>
      api<CheckResult>(`${URL}${id}/check/`, { method: "POST" }),
  });
}
```

### 3. `frontend/src/routes/_authenticated/account/llm.tsx`

Extend the import from `@/lib/queries/llm`:

```ts
import {
  PROVIDER_SPECS,
  checkResultLabel,
  rowToState,
  switchProvider,
  toPayload,
  useCheckConfig,
  useCreateConfig,
  useDeleteConfig,
  useLLMConfigs,
  useUpdateConfig,
  type CheckResult,
  type ConfigFormState,
  type LLMConfigRow,
  type Provider,
} from "@/lib/queries/llm";
```

In `LLMConfigPage`, next to the existing `del` mutation, add the check mutation and a per-row
result map (one mutation instance is shared by all rows — `check.variables` tells which row is in
flight):

```tsx
  const check = useCheckConfig();
  const [checks, setChecks] = useState<Record<number, CheckResult>>({});
```

In the row `<li>`, the result line goes under the model line (inside the `min-w-0` div):

```tsx
              <p className="truncate text-sm text-muted-foreground">
                {c.model}
              </p>
              {checks[c.id] && (
                <p
                  className={`truncate text-xs ${
                    checks[c.id].ok ? "text-muted-foreground" : "text-destructive"
                  }`}
                >
                  {checkResultLabel(checks[c.id])}
                </p>
              )}
```

And the button, first in the actions group (before Edit):

```tsx
              <Button
                variant="outline"
                size="sm"
                disabled={check.isPending}
                onClick={() => {
                  setChecks(({ [c.id]: _stale, ...rest }) => rest);
                  check.mutate(c.id, {
                    onSuccess: (r) =>
                      setChecks((s) => ({ ...s, [c.id]: r })),
                    onError: () => toast.error("Check failed"),
                  });
                }}
              >
                {check.isPending && check.variables === c.id
                  ? "Checking…"
                  : "Check"}
              </Button>
```

Subtleties:

- The `setChecks(({ [c.id]: _stale, ...rest }) => rest)` line drops the row's stale result while
  the new probe runs, so "OK" never lingers next to "Checking…".
- `disabled={check.isPending}` disables **all** check buttons while one runs — sequential probes,
  one shared mutation. Fine for a handful of aliases; parallel checks would need one mutation per
  row for no real gain.
- `onError` covers *endpoint* failures only (session expired, server down). A broken LLM config
  arrives through `onSuccess` with `ok: false` — that's the whole 200-on-failure design.
- Results are overwrite-only; editing a config does not clear its old check result. Cheap to add
  later if it bothers you (clear in the dialog's `onSuccess`).

## Tests (already on disk, red)

| file | covers |
| --- | --- |
| `backend/llm_connector/tests/test_api.py` → `LLMConfigCheckActionTests` | 200+`ok`+`latency_ms` on success (LLMClient mocked, `assert_called_once_with("writer", user=alice)`); `ok: false`+error text on a raising `complete`; construction failure (missing SDK) also `{ok: false}`, never 500; other user's pk → 404 with the client never built; unauthenticated → 401/403 |
| `frontend/tests/lib/queries/llm.test.ts` → `describe("checkResultLabel …")` | success label `"OK · 812 ms"`; failure label = raw error text |

Run them:

```sh
cd backend && python manage.py test llm_connector.tests.test_api
cd frontend && npx vitest run tests/lib/queries/llm.test.ts
```

> **Red-state notes.**
> Backend: the mock-bearing tests **error** (patching `llm_connector.views.LLMClient` raises
> `AttributeError` until the import exists) and the rest fail on 404-vs-200 — both count as red.
> Frontend: the static import of `checkResultLabel` makes the **whole** `llm.test.ts` file error
> until the helper exists — the previously green helper tests in that file go red with it. That's
> the import mechanics, not a regression; implement `llm.ts` first and the file splits back into
> green-except-the-new-ones, then green.

## Verification

1. Backend red → type `views.py` → `python manage.py test llm_connector.tests.test_api` green.
2. Frontend red → type `llm.ts` + `llm.tsx` → `npx vitest run tests/lib/queries/llm.test.ts`
   green, `npx tsc -b` clean.
3. Click-through (dev stack up — valkey/ollama/runserver; celery not needed for this):
   - Account → LLM tab. Every row shows a **Check** button.
   - Check a working ollama row → inline "OK · *n* ms" appears, button showed "Checking…"
     meanwhile.
   - Edit that row's URL to a dead port (e.g. `http://localhost:11435`), save, Check → after
     ~2× timeout + 2 s retry, inline red transport error. Restore the URL.
   - Check an anthropic row with a garbage key → red error mentioning authentication; the key
     itself must not appear anywhere in the response (eyeball the network tab).
   - `/api/llm/request-logs/` (or the admin) shows the probe calls — provenance for free.

Done = both suites green + the click-through matches.

## Results

*(filled by Lukas after testing — raw test output, observed issues, what works)*
