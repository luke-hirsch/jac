# [frontend] personality questionnaire — the missing half of the personal paragraph

> **Mode note:** default-strict — Lukas types the non-test source. Tests land first
> (red). **No own branch**: this phase shares `fullstack/letter-quality` (the working
> tree still carries the uncommitted pipeline-v2 work, so a second branch would be
> fiction — see that guide's mode note).

## why

The pipeline-v2 Results: "the personality paragraph is missing in all instances,
because the frontend has not implemented this." Precisely: the generate panel _does_
send `personal_paragraph` (the checkbox exists, default off) — what's missing is any
UI for the **personality questionnaire**. The spa backend is fully in place
(`/api/spa/personality/` RetrieveUpdate + `/api/spa/personality/rebuild/`,
`PERSONALITY_QUESTIONS` in `backend/spa/personality_questions.py`, `ensure_dossier`
on the model), but nothing under `frontend/src` touches it — so `answers` stays `{}`,
`_personality_dossier()` returns `""`, and `CoverLetter._personal_paragraph` stubs
**before** research even runs, on every grade and every alias.

Goal: an Account → Personality page to answer the 12 oblique questions (~5 answered
is the intended use, ≤280 chars each), a dossier preview/rebuild, and a generate-panel
hint when "Personal paragraph" is ticked with zero answers.

## backend contract (already live — nothing to change)

- `GET /api/spa/personality/` → `{id, answers: {qid: text}, dossier, questions:
[{id, prompt}], answers_updated_at, dossier_built_at, updated_at}`. The `questions`
  array is server-owned — render from it, never hardcode the pool.
- `PATCH /api/spa/personality/` body `{answers: {...}}` — **replaces the whole dict**
  (send every kept answer, not a delta). Serializer drops blank answers and 400s over
  280 chars/answer; it also bumps `answers_updated_at`.
- `POST /api/spa/personality/rebuild/` → `{dossier}` — forces a re-distil (one LLM
  call on the `default` alias; `?alias=` optional).

## affected files

| file                                                               | change                                                          |
| ------------------------------------------------------------------ | --------------------------------------------------------------- |
| `frontend/src/lib/queries/personality.ts` (new)                    | types, pure helpers (unit-tested), `usePersonality` / mutations |
| `frontend/src/routes/_authenticated/account.tsx`                   | nav item                                                        |
| `frontend/src/routes/_authenticated/account/personality.tsx` (new) | the questionnaire page                                          |
| `frontend/src/components/applications/generate-panel.tsx`          | zero-answers hint under the Personal-paragraph checkbox         |
| `frontend/tests/lib/queries/personality.test.ts` (new)             | (AI, on disk) pure-helper tests                                 |

## the code

### 1. `frontend/src/lib/queries/personality.ts` (new)

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

/** `/api/spa/personality/` — the questionnaire the personal paragraph grounds "you" in. */
export type PersonalityQuestion = { id: string; prompt: string };

export type PersonalityRow = {
  id: number;
  answers: Record<string, string>;
  dossier: string;
  /** Server-owned question pool — render from this, never hardcode it. */
  questions: PersonalityQuestion[];
  answers_updated_at: string | null;
  dossier_built_at: string | null;
  updated_at: string;
};

const URL = "/api/spa/personality/";
const KEY = ["personality"];

/** Mirrors the serializer's per-answer cap (spa/personality_questions.py). */
export const MAX_ANSWER_LEN = 280;

/* ---------- pure helpers (unit-tested) ---------- */

/** Trim + drop blanks — exactly what the backend will store from a PATCH. */
export function cleanAnswers(
  draft: Record<string, string>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [id, text] of Object.entries(draft)) {
    const t = (text ?? "").trim();
    if (t) out[id] = t;
  }
  return out;
}

export function answeredCount(answers: Record<string, string>): number {
  return Object.keys(cleanAnswers(answers)).length;
}

/** Ids whose draft answer exceeds the cap — save stays disabled while non-empty. */
export function overlongAnswers(draft: Record<string, string>): string[] {
  return Object.entries(draft)
    .filter(([, text]) => (text ?? "").trim().length > MAX_ANSWER_LEN)
    .map(([id]) => id);
}

/** True when saving would change what the server stores (blank edits don't count). */
export function answersDirty(
  saved: Record<string, string>,
  draft: Record<string, string>,
): boolean {
  const a = cleanAnswers(saved);
  const b = cleanAnswers(draft);
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) if (a[k] !== b[k]) return true;
  return false;
}

/** Dossier freshness, mirroring PersonalityProfile.dossier_stale server-side.
 *  "stale" is informational — generation rebuilds via ensure_dossier on its own. */
export function dossierState(
  row: Pick<
    PersonalityRow,
    "answers" | "dossier" | "answers_updated_at" | "dossier_built_at"
  >,
): "none" | "stale" | "fresh" {
  if (answeredCount(row.answers) === 0) return "none"; // nothing to distil
  if (!row.dossier || !row.dossier_built_at) return "stale";
  if (
    row.answers_updated_at &&
    Date.parse(row.answers_updated_at) > Date.parse(row.dossier_built_at)
  )
    return "stale";
  return "fresh";
}

/** Generate-panel nag: ticking "Personal paragraph" with zero personality answers
 *  guarantees a stub — say so before the run, not after. Silent while loading. */
export function personalityHint(
  personalParagraph: boolean,
  row: PersonalityRow | undefined,
): string | null {
  if (!personalParagraph || !row) return null;
  if (answeredCount(row.answers) > 0) return null;
  return (
    "No personality answers yet — the personal paragraph will come out as a stub. " +
    "Fill the questionnaire under Account → Personality."
  );
}

/* ---------- query hooks ---------- */

export function usePersonality(enabled = true) {
  return useQuery({
    queryKey: KEY,
    queryFn: () => api<PersonalityRow>(URL),
    enabled,
  });
}

export function useUpdateAnswers() {
  const qc = useQueryClient();
  return useMutation({
    // PATCH replaces the whole answers dict — always send the full cleaned draft.
    mutationFn: (answers: Record<string, string>) =>
      api<PersonalityRow>(URL, {
        method: "PATCH",
        body: JSON.stringify({ answers }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useRebuildDossier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<{ dossier: string }>(`${URL}rebuild/`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
```

### 2. `frontend/src/routes/_authenticated/account.tsx`

Add to `ITEMS`, after "LLM providers":

```ts
  { to: "/account/personality", label: "Personality" },
```

### 3. `frontend/src/routes/_authenticated/account/personality.tsx` (new)

```tsx
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import {
  MAX_ANSWER_LEN,
  answeredCount,
  answersDirty,
  cleanAnswers,
  dossierState,
  overlongAnswers,
  usePersonality,
  useRebuildDossier,
  useUpdateAnswers,
} from "@/lib/queries/personality";

export const Route = createFileRoute("/_authenticated/account/personality")({
  component: PersonalityPage,
});

const STATE_LABEL = {
  none: "no dossier yet",
  stale: "rebuilds on the next generation",
  fresh: "up to date",
} as const;

function PersonalityPage() {
  const personality = usePersonality();
  const update = useUpdateAnswers();
  const rebuild = useRebuildDossier();
  // Seeded from the server once; refetches must not clobber edits (adjust-state-
  // during-render, same pattern as the content card's server re-seed).
  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  if (personality.data && draft === null) setDraft(personality.data.answers);

  if (!personality.data || draft === null)
    return <p className="text-sm">loading…</p>;
  const row = personality.data;

  const overlong = overlongAnswers(draft);
  const dirty = answersDirty(row.answers, draft);
  const state = dossierState(row);
  const answered = answeredCount(draft);

  function onSave() {
    update.mutate(cleanAnswers(draft!), {
      onSuccess: () => toast.success("Answers saved"),
      onError: () => toast.error("Could not save the answers"),
    });
  }

  function onRebuild() {
    rebuild.mutate(undefined, {
      onSuccess: () => toast.success("Dossier rebuilt"),
      onError: () => toast.error("Could not rebuild the dossier"),
    });
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-medium">Personality</h2>
        <p className="text-sm text-muted-foreground">
          Oblique questions, on purpose — answer the ones that spark something
          (about five is plenty, one tweet each). A small model distils them
          into the dossier the cover letter's personal paragraph grounds "you"
          in.
        </p>
      </div>

      <div className="space-y-4">
        {row.questions.map((q) => {
          const value = draft[q.id] ?? "";
          const over = value.trim().length > MAX_ANSWER_LEN;
          return (
            <div key={q.id} className="space-y-1">
              <Label htmlFor={`q-${q.id}`}>{q.prompt}</Label>
              <Textarea
                id={`q-${q.id}`}
                value={value}
                rows={2}
                onChange={(e) => setDraft({ ...draft, [q.id]: e.target.value })}
              />
              <p
                className={`text-xs ${over ? "text-destructive" : "text-muted-foreground"}`}
              >
                {value.trim().length}/{MAX_ANSWER_LEN}
              </p>
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-3">
        <Button
          onClick={onSave}
          disabled={!dirty || overlong.length > 0 || update.isPending}
        >
          {update.isPending ? "Saving…" : "Save answers"}
        </Button>
        <span className="text-xs text-muted-foreground">
          {answered} of {row.questions.length} answered
        </span>
      </div>

      <Separator />

      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-medium">Dossier</h3>
          <Badge variant="outline">{STATE_LABEL[state]}</Badge>
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            onClick={onRebuild}
            disabled={state === "none" || rebuild.isPending}
          >
            {rebuild.isPending ? "Rebuilding…" : "Rebuild now"}
          </Button>
        </div>
        {row.dossier ? (
          <p className="whitespace-pre-wrap rounded border bg-muted/40 p-3 text-sm">
            {row.dossier}
          </p>
        ) : (
          <p className="text-sm text-muted-foreground">
            No dossier yet — save some answers first. It is built automatically
            on the next generation, or on demand here (one LLM call).
          </p>
        )}
      </div>
    </div>
  );
}
```

> Subtle: save before rebuild — the rebuild distils the **saved** answers, so an
> unsaved draft is invisible to it. If you want, disable "Rebuild now" while `dirty`
> too; the guide leaves it enabled because the badge already explains staleness.

### 4. `frontend/src/components/applications/generate-panel.tsx`

Import:

```ts
import { personalityHint, usePersonality } from "@/lib/queries/personality";
```

Inside `GeneratePanel`, next to the other hooks (fetch only when the box is ticked —
no personality request on plain page views):

```ts
const personality = usePersonality(personalParagraph);
const hint = personalityHint(personalParagraph, personality.data);
```

Render right after the existing "cannot web-search" hint paragraph:

```tsx
{
  hint && <p className="text-xs text-amber-700">{hint}</p>;
}
```

(Amber, not muted: unlike the web-search case this one is fixable before burning a
run.)

## tests (already on disk, land red)

| file                                             | covers                                                                                                                                                                                                                |
| ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `frontend/tests/lib/queries/personality.test.ts` | `cleanAnswers` trim/drop; `answeredCount`; `overlongAnswers` boundary at 280; `answersDirty` incl. blank-only edits; `dossierState` none/stale/fresh matrix; `personalityHint` unticked/loading/zero-answers/answered |

The module doesn't exist yet, so the whole (new) file is red on import — no
collateral, it touches nothing shared.

Run: `cd frontend && npx vitest run tests/lib/queries/personality.test.ts`

## Verification (Lukas)

1. Test file green, `npx tsc -b` clean.
2. Click-through (dev stack up): Account → Personality shows the 12 server questions;
   answer ~5, watch the counters; Save → PATCH lands (answers survive a reload; a
   blank answer disappears server-side). "Rebuild now" returns a distilled dossier
   and the badge flips to "up to date"; editing an answer + saving flips it to
   "rebuilds on the next generation".
3. Applications page: tick "Personal paragraph" with an empty questionnaire → amber
   hint appears; after answering, the hint is gone.
4. End-to-end payoff: a strong/web-capable generation with the box ticked now yields
   a **real** personal paragraph (no ⚠️ stub) with sources — the first time the whole
   personal-paragraph pipeline runs live. Log the quality verdict in Results.

## Results

- thinking about extending the questionair.
  - in the frist run it has been very coporate
  - now it is very non coporate leading to very non corporate dossiers
  - maybe middle ground is good. the more question the better.
- in the frontedn while creating the dossier in the profile, there is not clear what model is generating it. let the user pick and choose like in the application
