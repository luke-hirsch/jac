# [fullstack] letter refine — selection popover + ephemeral chat

> **Mode note:** volatile phase — Claude implements (Lukas's delegation, 2026-07-11);
> tests land first; Lukas owns live verification.

## why

Post-generation refinement today is one buried "Rewrite selection" bar that (a) always ran on
the `"default"` alias — the frontend never sent one, so every rewrite hit the 1B model — and
(b) offers no conversation. Lukas wants Apple-Pages-style writing tools: highlight a passage →
a small floating panel with common rewrite styles, a model picker, and a way to _talk_ about
the letter with a capable model.

## decisions (cleared with Lukas, 2026-07-11)

- **Chat is ephemeral**: client-held transcript + a sync endpoint (like `rewrite`); nothing
  persisted, gone on reload. Offered for **standard+ aliases only** (light can't chat usefully;
  backend 400s as backstop).
- **Popover replaces the rewrite bar**: three preset styles — _Shorter · More formal · More
  natural_ — plus a free instruction, a model picker (defaults to the latest done run's alias
  when it is standard+), and "Discuss in chat" which seeds the chat with the selection.
- The chat can propose a full replacement body via a line-anchored `REVISED BODY:` marker
  (no JSON — see [[no-json-llm-io]]); the UI applies it with one click.

## design

### backend

- `jac/llm_prompts.py` → `class LetterChat`: single-prompt rendering (the adapters have never
  been exercised with multi-turn `messages=`, and every other rung is single-prompt): rules +
  `JOB POSTING (context only)` + `CURRENT LETTER BODY` + `CONVERSATION` transcript
  (`USER:`/`ASSISTANT:` lines) + trailing `ASSISTANT:`. Same fabrication rule as the writer.
  `reply() -> {"reply": str, "revision": str | None}`; the revision is split on the first
  line matching `REVISED BODY:` (same-line content accepted); any LLM failure →
  `{"reply": "", "revision": None}`.
  Caps: `_MAX_TRANSCRIPT_CHARS = 6000`, `_MAX_BODY_CHARS = 8000` (the view 400s above).
- `jac/views.py` → `chat` action on `JobApplicationViewSet` (`POST …/applications/<pk>/chat/`):
  request `{alias, body, messages: [{role: user|assistant, content}]}` — body is the _client
  draft_ (may be unsaved). 400 on: empty/malformed messages, last message not `user`, caps
  exceeded, or a **light-strength alias** (`get_alias_strength` backstop; the UI already
  filters). 502 when the model returns neither reply nor revision. Nothing persisted.

### frontend

- `lib/letter-chat.ts` (new, pure — unit-tested): `ChatMessage`, `REWRITE_STYLES` (the three
  presets with their instructions), `chatAliases()` (standard+ only),
  `preferredRefineAlias(aliases, runs)` (latest **done** run's alias if standard+, else first
  standard+ alias, else null), `seedDiscussion(selection)`, `chatPayload(...)`.
- `lib/queries/applications.ts`: `useRewriteParagraph` now sends `alias` (**the bug fix**);
  new `useLetterChat` mutation.
- `lib/queries/generations.ts`: `Grounding.repaired?`, `CoverLetterResult.snippet_ranking?`;
  `groundingBadge` labels a repaired-clean letter `grounded · repaired` / repaired-dirty
  `n claims · repaired`.
- `components/applications/rewrite-popover.tsx` (new): floating panel positioned at the
  textarea selection via the hidden mirror-div caret measurement; style buttons + instruction
  input + alias picker + Discuss + close.
- `components/applications/refine-chat.tsx` (new): collapsible chat under the letter editor —
  transcript state, send (current body travels along), assistant replies, and an
  "Apply revised body" button when a `revision` comes back (routes through `onBody`, so Save
  still gates persistence). Hidden entirely when no standard+ alias is configured.
- `components/applications/letter-editor.tsx`: selection tracking on the textarea
  (select/keyup/mouseup) drives the popover; old rewrite bar removed; `runs` prop added for
  the alias default; renders `RefineChat` at the bottom.
- `components/applications/generate-panel.tsx`: result badges gain `snippet_ranking`
  (muted "snippets: embedding|structural") and the repaired grounding label rides free.

## test map (land first, red)

| file                                                              | covers                                                                                                                                                                                       |
| ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend jac/tests/test_llm_rungs.py` → `LetterChatTests`         | prompt carries rules/body/posting/transcript + trailing `ASSISTANT:`; revision split (own-line, same-line, absent); LLM failure → empty result, muted                                        |
| `backend jac/tests/test_job_application.py` → `ChatEndpointTests` | 200 happy path (reply only); revision extracted; 400 empty messages / last-not-user / light alias / overlong transcript (LLM never called); 502 empty model reply; 404 foreign app           |
| `frontend tests/lib/letter-chat.test.ts`                          | REWRITE_STYLES shape; preferredRefineAlias (done-run alias wins, light run alias skipped, fallback, null); chatAliases filters light; seedDiscussion embeds the selection; chatPayload shape |
| `frontend tests/lib/generations.test.ts` (extended)               | groundingBadge repaired variants                                                                                                                                                             |

Run: `python manage.py test jac.tests.test_llm_rungs jac.tests.test_job_application` ·
`npx vitest run tests/lib/letter-chat.test.ts tests/lib/generations.test.ts`

## Verification (Lukas)

1. Suites green + `npx tsc -b` clean.
2. Click-through (dev stack up): select letter text → popover appears at the selection; the
   three styles rewrite via the _picked_ model (watch `/api/llm/request-logs/` — no more
   silent `default`); Discuss seeds the chat; chat replies; a proposed revision shows an
   Apply button and lands in the draft (Save persists).
3. Confirm chat is absent when only the light server default is configured.

## Results

_(filled by Lukas after testing — raw test output, observed issues, what works)_

- chat window disappears outside of the letter div. so sometimes you can only see half of the chat window.

### Follow-up fix (Claude, 2026-07-12)

The clipped panel is the **rewrite popover** (the floating writing-tools window):
it is absolutely positioned inside the letter card, and the `Card` primitive has
`overflow-hidden` — a selection in the lower half of the textarea dropped the
~190px panel below the card edge, cutting it off. Fix: `letter-editor.tsx` now
computes `flip` (caret in the lower half of the textarea) and `rewrite-popover.tsx`
renders flipped panels *above* the caret via `translateY(-100%)` (no height
measurement needed). Re-verify: select text on the last line of a long letter body
→ the popover opens fully visible above the selection.
