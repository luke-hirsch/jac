# [backend] Small correctness bugs — WS snapshot race, Anthropic text blocks, embed cap, logger

> **Branch:** `backend/bugfixes` (shared). Tests land red first. Four independent, self-contained
> fixes; type them in any order.

## Context / goal

Four isolated defects found in the review. Each is small and low-risk; grouped so they share one
branch.

1. **WS snapshot race** (`jac/consumers.py`). `connect()` reads the snapshot from the DB *then*
   joins the `gen_<pk>` group. Any event published in that window is lost. Invisible with the stub
   task; with the real pipeline a `progress`/`done` fired between the read and the join vanishes.
2. **Anthropic `complete` assumes `content[0]` is text** (`providers/anthropic.py:60`). Returns
   `response.content[0].text`. With any tool/thinking block first, block 0 has no `.text` →
   `AttributeError`. `web_search` already handles this; `complete` doesn't.
3. **`Embed._cap_job_post` is a no-op** (`llm_prompts.py:68-85`). The "summarise/truncate if over
   `_MAX_TOKENS`" branch just returns the full text (the `except` truncation is unreachable because
   the `try` can't raise). The token cap silently doesn't work.
4. **Root logger grab** (`jac/serializers.py:25`). `logger = logging.getLogger()` takes the *root*
   logger, so its warnings bypass per-module config. Should be `getLogger(__name__)`.

Goal: fix all four. No feature change — these make existing paths behave as intended.

## Affected files

| path | why |
| --- | --- |
| `backend/jac/consumers.py` | join the group before sending the snapshot; snapshot becomes a post-subscription baseline |
| `backend/llm_connector/providers/anthropic.py` | `complete` returns the first (or joined) text block, skipping non-text blocks |
| `backend/jac/llm_prompts.py` | `Embed._cap_job_post` actually truncates when over budget |
| `backend/jac/serializers.py` | `logger = logging.getLogger(__name__)` |
| `backend/jac/tests/test_generation_ws.py` | **(test)** — consumer subscribes before reading the snapshot |
| `backend/llm_connector/tests/test_adapters.py` | **(test)** — `complete` skips a leading non-text block |
| `backend/jac/tests/test_llm_rungs.py` | **(test)** — `_cap_job_post` truncates over budget |
| `backend/jac/tests/test_models.py` | **(test)** — serializer logger is module-scoped |

## The code

### 1. `backend/jac/consumers.py`

Keep the reject-before-`accept()` contract (anonymous/non-owner must still fail to connect), but
split the cheap ownership gate from the snapshot payload, and **read the snapshot after joining the
group** so it's a consistent baseline that reconciles anything missed during `accept()`:

```python
    async def connect(self):
        self.pk = int(self.scope["url_route"]["kwargs"]["pk"])  # type: ignore
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        if not await self._owns(user.id, self.pk):
            await self.close(code=4404)
            return
        self.group = f"gen_{self.pk}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        # Read AFTER joining the group: any event during accept() is then reconciled by this
        # baseline snapshot, closing the read-then-subscribe race.
        snapshot = await self._snapshot(self.pk)
        await self.send_json({"event": "snapshot", **snapshot})
```

Add the ownership gate and slim the snapshot to not re-check ownership (the gate did it):

```python
    @database_sync_to_async
    def _owns(self, user_id: int, pk: int) -> bool:
        return GenerationRun.objects.filter(
            pk=pk, job_application__user_id=user_id
        ).exists()

    @database_sync_to_async
    def _snapshot(self, pk: int) -> dict:
        run = GenerationRun.objects.filter(pk=pk).first()
        if run is None:  # deleted between the ownership check and here
            return {"status": "failed", "stage": "", "result": None, "error": "gone"}
        return {
            "status": run.status,
            "stage": run.stage,
            "result": run.result,
            "error": run.error,
        }
```

> `group_add` before `accept()` is fine on Channels — the channel exists as soon as the consumer has
> a `channel_name`. Ownership is still checked before `accept()`, so `test_anonymous_is_rejected` /
> `test_non_owner_is_rejected` stay green.

### 2. `backend/llm_connector/providers/anthropic.py`

Add a helper and use it in `complete`:

```python
    @staticmethod
    def _text_from(response) -> str:
        """Join all text blocks, skipping tool_use / thinking / web_search blocks. Anthropic can
        return a non-text block first (e.g. with tools enabled), so indexing content[0] is unsafe."""
        return "".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()
```

```python
    def complete(self, messages: list[dict], **kwargs) -> str:
        system, api_msgs = self._split_system(
            messages, kwargs.pop("system", self.config.get("system"))
        )
        params = dict(
            model=self._model, max_tokens=self._max_tokens, messages=api_msgs, **kwargs
        )
        if system:
            params["system"] = system
        response = self._client.messages.create(**params)
        return self._text_from(response)
```

### 3. `backend/jac/llm_prompts.py`

Replace `Embed._cap_job_post` with a real truncation (drop the dead `try/except`):

```python
    def _cap_job_post(self) -> str:
        """Cap the job-post text so (entries + post) fits _MAX_TOKENS. Hard char-truncation
        (~4 chars/token) — crude but real; the previous version returned the full text unchanged."""
        tokens_of_entries = 80 * len(self.flatten_entries)
        tokens_of_job_post = len(self.job_post_text.split()) * 4
        if tokens_of_entries + tokens_of_job_post <= self._MAX_TOKENS:
            return self.job_post_text
        room_tokens = max(self._MAX_TOKENS - tokens_of_entries, 0)
        return self.job_post_text[: room_tokens * 4]
```

> Also fix the `_EMBED_INTSTRUCT` typo → `_EMBED_INSTRUCT` (and its one use in `_query`) while
> you're in this class — cosmetic, but it's the light-rung query instruction. Optional; not tested.

### 4. `backend/jac/serializers.py`

```python
logger = logging.getLogger(__name__)
```

## Tests

- `test_generation_ws.py` (**append** to `GenerationConsumerTests`):
  `test_subscribes_before_reading_snapshot` — patch the instance so the order of `group_add` vs
  `_snapshot` is recorded, connect as the owner, assert `group_add` ran first. *(Flagged: this is a
  white-box ordering assertion — the race itself is timing-dependent and can't be made cleanly
  black-box red. The existing `test_owner_gets_snapshot_then_live_event` guards the happy path.)*
- `test_adapters.py` (**new** `AnthropicCompleteTests`): a mocked response whose `content` is
  `[tool_use_block, text_block]` → `complete` returns the text block's text, not an `AttributeError`.
- `test_llm_rungs.py` (**append**): with `_MAX_TOKENS` patched small, a long posting is truncated to
  `<= room_tokens*4` chars and shorter than the original; under budget it's returned unchanged.
- `test_models.py` (**append** `SerializerLoggerTests`): `jac.serializers.logger.name ==
  "jac.serializers"` (red while it's the root logger, whose name is `"root"`).

Run:

```bash
cd backend && python manage.py test \
  jac.tests.test_generation_ws jac.tests.test_llm_rungs jac.tests.test_models \
  llm_connector.tests.test_adapters
```

## Verification

```bash
cd backend && python manage.py test jac llm_connector    # green
```

- WS: with the real pipeline wired, connect a client and confirm no early `progress` event is
  dropped (the post-join snapshot always reflects current status even if one slipped through).
- Anthropic: a `complete()` against a model/config that emits a leading non-text block returns prose
  instead of raising.
- Embed: a pathologically long posting no longer inflates the embed request unbounded.

**Done looks like:** the four defects are fixed, their tests are green, and the rest of the suite is
unaffected.

## Related, deferred (not in this guide)

**Token accounting is dead.** Every adapter implements `token_counts(response)`, but
`LLMClient.complete`/`stream` always log `prompt_tokens=None, completion_tokens=None` — the
`LLMRequestLog` token columns are never populated. Wiring it up is *not* a one-liner: `complete()`
returns a **string**, so the raw response object (needed by `token_counts`) is thrown away. The
least-invasive fix is for each adapter to stash `self._last_response` and expose a
`last_token_counts()` the client reads in its `finally` block — that touches all provider adapters,
so it deserves its own guide rather than riding in with these four. Captured here so it isn't lost.
