# [fullstack] llm-config-tab-v2

> **SPA phase, guide 2.** Renamed `[frontend]` → `[fullstack]` 2026-07-17: the executor
> rework (`done/[backend]-executor-connector.md`) specced the new user-facing config API in
> its §9 but that section was **never implemented** — the serializer on disk still lists
> columns the model no longer has and crashes on first use. The repairs land here, first,
> before the tab.
>
> **Backlog plan.** Full code + red tests at activation (backend tests included — configs
> CRUD is currently untestable because the serializer raises).

## Context / goal

`LLMConfig` collapsed to **one credential per commercial provider** (landed:
`llm_connector/models.py` — `user`+`provider` unique, `default` flag, encrypted key; no
alias, no model). The **target** API for `/api/llm/configs/`: `{id, provider, default,
api_key (write-only), has_api_key, created_at, updated_at}` — providers
`anthropic`/`openai` only (`ollama` rejected: "HirschAI is built in"; the tower is the
operator's system row), `default` exclusive server-side (enforced in `LLMConfig.save()`;
a keyless default doesn't count for the executors endpoint). The whole structured-mask /
extra-JSON / url machinery is dead vocabulary.

The tab becomes **one card per commercial provider**: key status (`has_api_key`),
set/replace key (write-only, never echoed), default toggle, the `check` round-trip button,
delete. Plus a HirschAI info line (not configurable here — surface its `reachable` state
from `useExecutors()`).

## Backend repairs — the rework's missed surfaces (do these first)

Guide 1 declared both breakages an accepted window; this guide closes it.

### 1. `llm_connector/serializers.py` — `LLMConfigSerializer`

On disk it still lists `alias` and `model` in `fields` and validates uniqueness on
`("user", "alias")` — all dead columns, so DRF raises on any `/api/llm/configs/` request.
Target (verbatim from `done/[backend]-executor-connector.md` §9; keep the existing
`create()`/`update()` api_key pop/encrypt pattern):

```python
class LLMConfigSerializer(serializers.ModelSerializer):
    """One credential per commercial provider. `api_key` is write-only; omitting it
    on PATCH keeps the stored key. The tower is not configurable here — it is the
    operator's system row."""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    api_key = serializers.CharField(
        write_only=True, required=False, allow_blank=True,
        style={"input_type": "password"},
    )
    has_api_key = serializers.BooleanField(read_only=True)

    class Meta:
        model = LLMConfig
        fields = ("id", "user", "provider", "default", "api_key", "has_api_key",
                  "created_at", "updated_at")
        read_only_fields = ("id", "has_api_key", "created_at", "updated_at")
        validators = [
            serializers.UniqueTogetherValidator(
                queryset=LLMConfig.objects.all(), fields=("user", "provider")
            )
        ]

    def validate_provider(self, value):
        if value == Provider.ollama:
            raise serializers.ValidationError(
                "HirschAI is built in — configure commercial providers only."
            )
        return value
```

The old `validate()` (SSRF url check) leaves with the `url` field; `validate_safe_llm_url`
stays a model-level concern for the system row (`LLMConfig.clean()`).

### 2. `llm_connector/serializers.py` — `LLMRequestLogSerializer`

Drop `"alias"` from `fields` (the column died; the read-only viewset crashes the same way).

### 3. `spa/views.py` — `PersonalityDossierRebuildView`

Still calls `prof.ensure_dossier(alias=…, user=…)`; the signature is now
`ensure_dossier(executor)` → TypeError on every rebuild click. Target:

```python
from llm_connector.conf import ExecutorError, resolve_executor


class PersonalityDossierRebuildView(APIView):
    """POST: force-rebuild + return the dossier (preview the distilled text).
    Optional body {provider, model}; blank = the user's default executor."""

    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        try:
            executor = resolve_executor(
                request.user,
                request.data.get("provider", ""),
                request.data.get("model", ""),
            )
        except ExecutorError as exc:
            return Response(
                {"provider": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST
            )
        prof = PersonalityProfile.objects.get(user=request.user)
        prof.dossier_built_at = None  # force stale -> always re-distils
        return Response({"dossier": prof.ensure_dossier(executor)})
```

(The frontend side — plain `POST` with no body, picker deleted — already lands with
guide 1; this endpoint just has to accept it.)

## Affected files

| Path | Change |
| --- | --- |
| `backend/llm_connector/serializers.py` | Repairs 1 + 2 above. |
| `backend/spa/views.py` | Repair 3 above. |
| `frontend/src/lib/queries/llm.ts` | Zone B (frozen through guide 1) rewritten: `LLMConfigRow` → the five-field shape; `PROVIDER_SPECS`/mask helpers (`buildStructuredExtra`, `parseExtraJson`, `toPayload`, `rowToState`, `switchProvider`) shrink drastically or die; CRUD hooks + `checkResultLabel` stay. |
| `frontend/src/routes/_authenticated/account/llm.tsx` | Cards-per-provider layout replacing the table + structured mask. |
| `frontend/tests/lib/queries/llm.test.ts` | Rewrite: the zone-B describes die with the mask; survivors = payload build (provider + key, PATCH-without-key keeps the stored key), `checkResultLabel`. |
| `backend/llm_connector/tests/` | Configs CRUD red tests (see below) — distribute into the existing topic files, not a new per-feature file. |

## Approach / key decisions

- **Provider list rendered from `useExecutors()`'s commercial rows**, not hardcoded — a
  future catalog provider lands frontend-free.
- **`api_key` semantics unchanged**: write-only, omit on PATCH to keep the stored key; the
  UI shows only the `has_api_key` badge.
- **Default toggle**: optimistic flip + refetch — the server enforces exclusivity (last
  write wins in `LLMConfig.save()`), the UI never juggles it locally.

## Tests (at activation)

- Backend: configs CRUD round-trip (create anthropic row with key → `has_api_key: true`,
  key never echoed; `ollama` rejected with the built-in message; duplicate provider 400;
  PATCH without `api_key` keeps the stored key; `default` exclusivity across two rows);
  request-log list serialises; dossier rebuild 200 on default executor + 400 shaped
  `{"provider": […]}` on an unresolvable pick.
- Frontend: payload builders (create: provider + key; key replace; default toggle;
  PATCH-without-key), types carry no url/model/extra/alias fields.

## Verification

`tsc -b` + vitest + backend suite green. Click-through: add an Anthropic key → card shows
"key set"; check → pong latency; toggle default; replace the key without touching default;
delete the row; personality tab → rebuild dossier works again. The generate panel (guide 1)
reflects the new config on its next executors refetch.

## Results

<!-- Human fills this in. -->
