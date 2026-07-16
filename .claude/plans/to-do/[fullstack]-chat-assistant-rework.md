# [fullstack] chat-assistant-rework

> **⚠️ STALE (2026-07-16 executor rework).** The chat endpoint is now executor-keyed
> (`provider`/`model` via `resolve_executor`) and the strength gate is gone — any executor
> chats. Rework the UX against that backend; the alias plumbing referenced below is dead.

> **Guide 7** — spun out of the *LLM-mode redesign* conversation. Independent of guides 1–6:
> the `get_alias_strength` chat gate and the entire connector strength/autodetect machinery
> **already died in guide 2** (direction change 2026-07-16 — no staged deletions). This guide
> reworks the narrow "refine the letter body" chat into a **streamed, real-multi-turn job-hunting
> assistant**.
>
> **Backlog plan.** Full copy-paste code + red tests at activation. The SSE-vs-WS transport is a
> decision embedded below (recommendation given); streaming needs a live click-through to verify.

## Context / goal

Today's `chat` (`LetterChat`) is a narrow letter-body refiner: **sync one-shot**, and it flattens
the transcript into `USER:/ASSISTANT:` text in a single `complete()` — it doesn't use
provider-native multi-turn at all (the adapters were just never exercised with it, though
`base.py` shows `complete(messages)`/`stream(messages)` have always been the contract). Its old
strength gate (`get_alias_strength(alias) == "light"`) died in guide 2.

Lukas wants it to **feel like chatting with an AI in its own app, leaning to job hunting**:
- **Scope** — a job-hunting assistant, not just a letter tool: talk about the posting, tailor the
  letter *and* CV, interview prep, career questions; propose a letter-body revision when relevant.
  Context = the posting + the current letter + the **tailored CV** (`application.cv_content`). The
  fabrication guardrail is scoped to **drafting** (don't invent employers/dates/numbers when writing
  letter/CV text), not to the whole conversation.
- **Experience** — **stream tokens** with **real multi-turn `messages`** (system + transcript),
  like ChatGPT/Claude.
- **No capability gate** — any alias the user configured is fair game (their choice, same as
  picking a small model in any app). Guide 2 already deleted the gate along with the whole
  connector autodetect machinery (its verification grep is the redesign's closing checkmark);
  this guide's only obligation is **not reintroducing one** — the picker offers every configured
  alias.

`rewrite` (`ParagraphRewrite`) is already ungated and fine — this guide leaves it, only aligning its
tone note. The rework is about `chat`.

## Affected files

| Path | Change |
| --- | --- |
| `backend/jac/llm_prompts.py` | `LetterChat` reworked: new assistant `_INSTRUCTION` (job-hunting, drafting-scoped guardrail); build a real **`messages`** list (system with posting + letter + tailored-CV context, then the transcript turns) instead of a flat prompt; add `stream()` yielding token deltas. Keep the `REVISED BODY:` marker convention (parsed client-side now). |
| `backend/jac/views.py` | `chat` action → **streams** (SSE `StreamingHttpResponse`, `text/event-stream`). Keep `_chat_problem` shape/size checks (pre-LLM, still cheap). Add a `ScopedRateThrottle` (`llm-chat`) on the action. Pass `application.cv_content` into `LetterChat`. (The strength gate is already gone — guide 2.) |
| `frontend/src/components/applications/refine-chat.tsx` | Consume the SSE stream (`fetch` + `response.body.getReader()` + `TextDecoder`), render deltas live; multi-turn transcript state; "apply revision to letter" affordance from the client-side `REVISED BODY:` split. Rename/relabel toward "assistant", not "refine letter". The streamed `fetch` must go through the same CSRF-header helper the JSON mutations use — a raw `fetch` silently misses the token and 403s only against session auth. |
| `frontend/src/lib/letter-chat.ts` | Pure helpers: build the request body, parse a streamed line into a delta, split a finished reply on `REVISED BODY:`. (Testable per the `frontend-test-layout` memory.) |
| `frontend/src/lib/queries/llm.ts` | The chat model picker offers any configured alias (the strength vocabulary is already gone from the API since guide 2 and from the SPA types since guide 5; the free executor displays as "Dr. Jacll", guide 5's branding constant; once `[fullstack]-llm-model-catalog-and-knobs` lands, reuse its catalog dropdown here — one shared source). |
| `frontend/src/components/applications/letter-editor.tsx` | Wire the assistant panel + the "apply revision" path into the body editor. |
| `backend/jac/tests/test_llm_rungs.py` | Update `LetterChatTests` — `messages` build (system carries posting/letter/CV; transcript roles), `stream()` yields deltas, revision marker still recognised. |
| `backend/jac/tests/test_job_application.py` | Update the chat-endpoint tests (`:537`) — streamed response shape. (The gate-is-gone assertion landed with guide 2.) |
| `backend/llm_connector/tests/test_adapters.py` | Add a multi-turn `messages` + `stream()` test for ollama/custom (the path this rework relies on; currently unexercised). |

## Approach / key decisions

- **Transport: SSE via `StreamingHttpResponse` (recommended) over a WS consumer.** A chat turn is
  request-driven and unidirectional (client sends transcript → server streams one reply), which is
  exactly SSE's shape and a fraction of the code of a stateful channels consumer. The generation WS
  exists for a different job (long async task progress). The `chat` action returns a
  `StreamingHttpResponse(generator, content_type="text/event-stream")`; `get_object()` still
  enforces auth/ownership. Sketch:

  ```python
  @action(detail=True, methods=["post"])
  def chat(self, request, pk=None):
      application = self.get_object()
      body = request.data.get("body") or ""
      messages = request.data.get("messages") or []
      alias = (request.data.get("alias") or "default").strip() or "default"
      problem = self._chat_problem(body, messages)
      if problem:
          return Response(problem, status=status.HTTP_400_BAD_REQUEST)

      chat = LetterChat(
          body, messages,
          posting_text=application.posting.posting_text,
          language=application.posting.language or "en",
          cv=application.cv_content,          # tailored-CV context (drafting-scoped)
          alias=alias, user=request.user,
      )

      def events():
          try:
              for delta in chat.stream():
                  yield f"data: {json.dumps({'delta': delta})}\n\n"
              yield f"data: {json.dumps({'done': True})}\n\n"
          except Exception:                    # noqa: BLE001 — surface, don't 500 mid-stream
              logger.exception("chat stream failed")
              yield f"data: {json.dumps({'error': 'stream failed'})}\n\n"

      return StreamingHttpResponse(events(), content_type="text/event-stream")
  ```

  > Under the app's ASGI/channels server a `StreamingHttpResponse` with a **sync** generator runs in
  > a threadpool; the ollama adapter's `stream()` is a sync urllib generator, so no async plumbing is
  > needed. Set `Cache-Control: no-cache` and `X-Accel-Buffering: no` on the response **up front**
  > rather than discovering nginx buffering in prod (note it in `config/nginx` either way).

- **Bound the stream.** A hung adapter holds a threadpool worker for as long as it likes: give the
  ollama/custom `stream()` a socket **read timeout**, and give `events()` a wall-clock cap (stop
  after ~120 s / a generous token count with a final `error` event) so a stuck model can't pin
  threads open indefinitely — a self-DoS with several tabs even single-user, a real one once jac
  opens.

- **Rate-limit now, not at open signup.** The posture is "treat every authed surface as
  internet-facing", and a streaming LLM endpoint is a token/GPU-burning primitive — the cheapest
  thing on the site to abuse. A DRF `ScopedRateThrottle` on the action (`throttle_scope =
  "llm-chat"`, something like `20/min`) is three lines today; per-user budgets and turn caps can
  still wait for open signup. Keep `_MAX_TRANSCRIPT_CHARS`/`_MAX_BODY_CHARS` as the size gate.

- **Real multi-turn.** `LetterChat` builds `messages = [{"role":"system","content": <instruction +
  posting + letter + CV context>}, *transcript]` and calls `LLMClient(alias, user).stream(messages=
  messages)`. Ollama's native `/api/chat` honours `role: system`/`user`/`assistant`; the adapter
  already sends `messages` verbatim. Keep the line-format `REVISED BODY:` convention (no JSON — the
  `no-json-llm-io` memory) for the optional whole-body proposal.

- **Revision affordance moves client-side.** Stream the reply as-is; when it finishes, the client
  splits on `REVISED BODY:` (reuse the existing `_REVISION_RE` logic in `lib/letter-chat.ts`) and, if
  present, offers "apply as new body". No separate endpoint.

- **One GPU — chatting on the tower stalls behind a generation run.** Ollama queues requests, so a
  chat turn fired while a multi-minute pipeline run holds the model waits for it (the 7B itself
  streams fluently at ~40 tok/s when idle — contention, not model size, is the constraint). Don't
  engineer around it: let the picker default to the user's preferred chat alias (most likely
  commercial, exactly as Lukas expects), and when the pick is the self-hosted alias while a run is
  in flight, a small "the local model is busy generating — replies may wait" hint beats a frozen
  spinner.

- **Guardrail scoped to drafting.** The system prompt says: be a helpful job-hunting assistant; when
  you *draft or edit* letter/CV text, never invent facts about the candidate (skills, employers,
  titles, numbers, dates) — those come from the letter, CV, or what the user tells you; general
  advice (interview prep, strategy) is unconstrained.

- **The posting is untrusted input inside the system prompt — frame it as data.** Posting text
  comes from arbitrary employers/websites; a hostile posting can carry instructions aimed at the
  assistant ("disregard your guidelines and…"). This isn't an open-signup problem — it bites the
  single operator today, because the injected text can steer the proposed letter body. Mitigation
  is structural, not clever: delimit the posting/letter/CV context in clearly labelled blocks and
  state in `_INSTRUCTION` that content inside them is reference material whose instructions must
  never be followed; and keep the revision affordance **manual** (the model proposes, only the
  user's click applies — never auto-apply `REVISED BODY:`). Small models won't resist every
  injection, which is exactly why the apply step stays human.

- **Public-surface note (partially built now).** The size caps and the throttle above are the
  cheap-now layer; the rest (per-user token budgets, turn caps, audit logging) waits for open
  signup (public-site-posture memory). Flag it; don't build it here.

## Tests (written at activation)

- `test_llm_rungs.py::LetterChatTests` — `messages` build: a `system` message carrying the posting,
  the current body, and the tailored CV; transcript turns mapped to `{role, content}`; `stream()`
  yields deltas (mock the client stream); the `REVISED BODY:` marker still splits.
- `test_job_application.py` chat endpoint — the response is `text/event-stream` and carries
  `delta`/`done` events; `_chat_problem` still 400s bad transcripts before any LLM call; the
  throttle 429s past the scoped rate (override the rate in the test so it doesn't need 20
  requests).
- `test_adapters.py` — ollama/custom `stream(messages=[...multi-turn...])` yields chunks and sends
  the roles through (the previously-unexercised path this rework leans on).
- `frontend/tests/lib/letter-chat.test.ts` — request-body builder, SSE-line → delta parse, and the
  finished-reply `REVISED BODY:` split.

## Verification

With a **small local ollama model** configured (proving the "any model, their choice" point):
open an application's assistant, ask a job-hunting question → tokens stream in live; ask it to
tighten the letter → it proposes a revision and "apply to letter" swaps the body. `tsc -b` +
vitest + the backend modules green.

## Results

<!-- Human fills this in. -->
