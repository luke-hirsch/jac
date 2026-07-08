# [frontend] Tailored CV + cover-letter render

> **SUPERSEDED (2026-07-08).** Shipped in a different shape during the volatile
> model-restructure session: the posting-text-first `/cv/tailored` tab became an
> **application-centric `/applications` section** (list + detail) after `JobApplication`/
> `ApplicationLayout` landed and `GenerationRun` moved under the application. The reusable
> pieces of this guide (WS proxy, `ws.ts`, `generations.ts` helpers/badges/reducer, the
> result render + badges) were implemented as specified; the page/flow parts were replaced by
> `routes/_authenticated/applications/` + `lib/queries/applications.ts` (apply-run PATCH,
> editable cover letter, run history). Kept for reference only.

> Guide 3 of 3 for **roadmap #1**. Branch: `frontend/tailored-render` (cut off `main` after the
> backend guides merge). Depends on both `[backend]` guides.

## Context / goal

The backend now exposes async generation: `POST /api/jac/generations/` creates a run, a WebSocket
streams progress, `GET /api/jac/generations/<id>/` rehydrates a snapshot. This guide adds the
`/cv/tailored` tab: paste a posting, pick grade/alias + toggles, **Generate**, watch progress, then
read the tailored CV + cover letter with `ai_share` / `grounding` / personal-paragraph badges.
**No layout editor** — render the information only.

Conventions to reuse: the `api()` wrapper (`src/lib/api.ts`), TanStack Query hooks (mirror
`src/lib/queries/llm.ts`), `useLLMConfigs()` for the alias picker, and shadcn primitives
(`Card`/`Badge`/`Select`/`Checkbox`/`Textarea`/`Popover`/`Separator`). Pure logic is split out of
the component into `lib/` so it's unit-testable (per the frontend-test-layout convention).

## Affected files

| File | Change |
| --- | --- |
| `frontend/vite.config.ts` | edit — proxy `/ws` to the backend with `ws: true` |
| `frontend/src/lib/ws.ts` | **new** — `openGenerationSocket(id, onEvent)` |
| `frontend/src/lib/queries/generations.ts` | **new** — types, hooks, + pure helpers |
| `frontend/src/routes/_authenticated/cv/tailored.tsx` | **new** — the page |
| `frontend/src/routes/_authenticated/cv.tsx` | edit — add the `Tailored` tab |

## The code

### 1. `frontend/vite.config.ts` — add the WS proxy

```ts
  server: {
    proxy: {
      "/api": BACKEND,
      "/_allauth": BACKEND,
      "/admin": BACKEND,
      "/media": BACKEND,
      "/static": BACKEND,
      "/ws": { target: BACKEND, ws: true },
    },
  },
```

### 2. `frontend/src/lib/queries/generations.ts` (new)

```ts
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type Grade = "light" | "standard" | "strong";
export type Status = "pending" | "running" | "done" | "failed";

export type Grounding = { count: number | null; claims: string[] };

export type CoverLetterResult = {
  language: string;
  subject: string;
  salutation: string;
  body: string;
  sender: Record<string, string>;
  recipient: Record<string, string>;
  date: string;
  snippets_used: string[];
  snippet_provenance: { native: string[]; translated: string[] };
  ai_share: number;
  grounding: Grounding;
  personal_paragraph: string;
  personal_paragraph_is_stub: boolean;
  personal_paragraph_sources: string[];
  personal_paragraph_grounding: Grounding;
  text: string;
};

export type CvEntry = { id: string; label: string; relevance_score: number | null };
export type TailoredResult = {
  meta: { grade: string; alias: string };
  cv: Record<string, CvEntry[]>;
  cover_letter: CoverLetterResult;
};

export type GenerationRun = {
  id: number;
  status: Status;
  stage: string;
  error: string;
  result: TailoredResult | null;
  grade: string;
  alias: string;
  personal_paragraph: boolean;
  verify_grounding: boolean;
  job_posting_title: string;
  created_at: string;
  updated_at: string;
};

export type GenerationForm = {
  posting_text: string;
  grade: Grade | "";        // "" => let the server auto-detect
  alias: string;
  verify_grounding: boolean;
  personal_paragraph: boolean;
};

export type GenerationPayload = {
  posting_text: string;
  alias: string;
  verify_grounding: boolean;
  personal_paragraph: boolean;
  grade?: Grade;
};

const URL = "/api/jac/generations/";

/* ---------- pure helpers (unit-tested) ---------- */

export function toPayload(f: GenerationForm): GenerationPayload {
  const p: GenerationPayload = {
    posting_text: f.posting_text.trim(),
    alias: f.alias,
    verify_grounding: f.verify_grounding,
    personal_paragraph: f.personal_paragraph,
  };
  if (f.grade) p.grade = f.grade;   // omit when "" so the server auto-detects
  return p;
}

export type Badge = { tone: "green" | "amber" | "muted"; label: string };

export function aiShareBadge(share: number): Badge {
  const pct = Math.round(share * 100);
  // Low AI share = mostly the candidate's own words; high = heavily machine-written.
  return { tone: pct <= 25 ? "green" : "amber", label: `${pct}% AI` };
}

export function groundingBadge(g: Grounding): Badge {
  if (g.count === null) return { tone: "muted", label: "not checked" };
  if (g.count === 0) return { tone: "green", label: "grounded" };
  return { tone: "amber", label: `${g.count} claim${g.count === 1 ? "" : "s"}` };
}

/** Fold a WS event (or a REST snapshot reshaped as one) into run state. */
export type WsEvent =
  | { event: "snapshot"; status: Status; stage: string; result: TailoredResult | null; error: string }
  | { event: "progress"; status: Status; stage: string }
  | { event: "done"; status: Status; result: TailoredResult }
  | { event: "failed"; status: Status; error: string };

export type RunState = {
  status: Status;
  stage: string;
  result: TailoredResult | null;
  error: string;
};

export function runReducer(state: RunState, e: WsEvent): RunState {
  switch (e.event) {
    case "snapshot":
      return { status: e.status, stage: e.stage, result: e.result, error: e.error };
    case "progress":
      return { ...state, status: e.status, stage: e.stage };
    case "done":
      return { ...state, status: e.status, result: e.result, stage: "done" };
    case "failed":
      return { ...state, status: e.status, error: e.error };
    default:
      return state;
  }
}

/* ---------- query hooks ---------- */

export function useCreateGeneration() {
  return useMutation({
    mutationFn: (form: GenerationForm) =>
      api<GenerationRun>(URL, { method: "POST", body: JSON.stringify(toPayload(form)) }),
  });
}

export function useGeneration(id: number | null) {
  return useQuery({
    queryKey: ["jac", "generations", id],
    queryFn: () => api<GenerationRun>(`${URL}${id}/`),
    enabled: id != null,
  });
}
```

### 3. `frontend/src/lib/ws.ts` (new)

```ts
/** Open the per-run progress socket. Auth rides the same-origin session cookie on the
 *  handshake (no token). Returns a close fn. `onEvent` gets each parsed JSON message. */
export function openGenerationSocket(
  id: number,
  onEvent: (data: unknown) => void,
): () => void {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${scheme}://${location.host}/ws/jac/generations/${id}/`);
  ws.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch {
      /* ignore non-JSON frames */
    }
  };
  return () => ws.close();
}
```

### 4. `frontend/src/routes/_authenticated/cv/tailored.tsx` (new)

Skeleton — wire `runReducer` to both the REST snapshot (on mount, when a run id exists) and the
live socket. Render structure shown; flesh out the markup with the existing shadcn components.

```tsx
import { useEffect, useReducer, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useLLMConfigs } from "@/lib/queries/llm";
import {
  useCreateGeneration, useGeneration, runReducer, aiShareBadge, groundingBadge,
  type RunState, type WsEvent, type Grade,
} from "@/lib/queries/generations";
import { openGenerationSocket } from "@/lib/ws";

export const Route = createFileRoute("/_authenticated/cv/tailored")({
  component: TailoredPage,
});

const INITIAL: RunState = { status: "pending", stage: "", result: null, error: "" };

function TailoredPage() {
  const [form, setForm] = useState({
    posting_text: "", grade: "" as Grade | "", alias: "default",
    verify_grounding: false, personal_paragraph: false,
  });
  const [runId, setRunId] = useState<number | null>(null);
  const [state, dispatch] = useReducer(runReducer, INITIAL);

  const configs = useLLMConfigs();
  const create = useCreateGeneration();
  const snapshot = useGeneration(runId);      // REST rehydrate (refresh-safe)

  // Seed from the REST snapshot whenever it (re)loads.
  useEffect(() => {
    if (snapshot.data) {
      dispatch({ event: "snapshot", ...snapshot.data } as WsEvent);
    }
  }, [snapshot.data]);

  // Live socket while a run is active.
  useEffect(() => {
    if (runId == null) return;
    return openGenerationSocket(runId, (d) => dispatch(d as WsEvent));
  }, [runId]);

  async function onGenerate() {
    try {
      const run = await create.mutateAsync(form);
      setRunId(run.id);
      dispatch({ event: "snapshot", status: run.status, stage: run.stage,
        result: run.result, error: run.error });
    } catch {
      toast.error("Could not start generation");
    }
  }

  const result = state.result;
  const running = state.status === "pending" || state.status === "running";

  return (
    <div className="space-y-6">
      {/* --- form --- */}
      <Card>
        <CardHeader><CardTitle>Tailor an application</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <Textarea rows={10} placeholder="Paste the job posting…"
            value={form.posting_text}
            onChange={(e) => setForm({ ...form, posting_text: e.target.value })} />
          {/* grade Select, alias Select (from configs.data), verify_grounding + personal_paragraph Checkboxes */}
          <Button onClick={onGenerate} disabled={running || !form.posting_text.trim()}>
            {running ? `Generating… ${state.stage}` : "Generate"}
          </Button>
        </CardContent>
      </Card>

      {state.status === "failed" && (
        <p className="text-sm text-destructive">Generation failed: {state.error}</p>
      )}

      {/* --- tailored CV --- */}
      {result && (
        <Card>
          <CardHeader><CardTitle>Tailored CV</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            {Object.entries(result.cv).map(([section, entries]) =>
              entries.length === 0 ? null : (
                <div key={section}>
                  <h3 className="text-sm font-semibold capitalize">{section}</h3>
                  <ul className="space-y-1">
                    {entries.map((e) => (
                      <li key={e.id} className="flex items-center justify-between gap-2 text-sm">
                        <span>{e.label}</span>
                        {e.relevance_score != null && (
                          <Badge variant="outline">{e.relevance_score.toFixed(2)}</Badge>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ),
            )}
          </CardContent>
        </Card>
      )}

      {/* --- cover letter --- */}
      {result && <CoverLetterCard letter={result.cover_letter} />}
    </div>
  );
}

function toneClass(tone: "green" | "amber" | "muted") {
  return tone === "green"
    ? "bg-green-100 text-green-800"
    : tone === "amber"
      ? "bg-amber-100 text-amber-900"
      : "bg-muted text-muted-foreground";
}

function CoverLetterCard({ letter }: { letter: import("@/lib/queries/generations").CoverLetterResult }) {
  const ai = aiShareBadge(letter.ai_share);
  const g = groundingBadge(letter.grounding);
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Cover letter</CardTitle>
        <div className="flex gap-2">
          <span className={`rounded px-2 py-0.5 text-xs ${toneClass(ai.tone)}`}>{ai.label}</span>
          <span className={`rounded px-2 py-0.5 text-xs ${toneClass(g.tone)}`} title={letter.grounding.claims.join("\n")}>
            {g.label}
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <pre className="whitespace-pre-wrap font-sans text-sm">{letter.text}</pre>
        {(letter.personal_paragraph_is_stub || letter.personal_paragraph) && (
          <div className={letter.personal_paragraph_is_stub
            ? "rounded border border-destructive/50 bg-destructive/10 p-3"
            : "rounded border bg-muted/40 p-3"}>
            <p className="text-sm">{letter.personal_paragraph}</p>
            {!letter.personal_paragraph_is_stub && letter.personal_paragraph_sources.length > 0 && (
              <ul className="mt-2 text-xs text-muted-foreground">
                {letter.personal_paragraph_sources.map((s) => <li key={s}>{s}</li>)}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
```

### 5. `frontend/src/routes/_authenticated/cv.tsx` — add the tab

```tsx
const TABS = [
  { to: "/cv", label: "Overview" },
  { to: "/cv/jobs", label: "Jobs" },
  { to: "/cv/education", label: "Education" },
  { to: "/cv/skills", label: "Skills" },
  { to: "/cv/certifications", label: "Certifications" },
  { to: "/cv/projects", label: "Projects" },
  { to: "/cv/languages", label: "Languages" },
  { to: "/cv/tailored", label: "Tailored" },
] as const;
```

## Tests (already written, start red)

- `frontend/tests/lib/generations.test.ts` (vitest, node env, `@/` alias): `toPayload` (trims,
  omits `grade` when `""`), `aiShareBadge` (green ≤25%, amber above), `groundingBadge`
  (null→muted, 0→green, N→amber with singular/plural), and `runReducer` (snapshot seeds; progress
  updates stage; done attaches result; failed sets error). Red until `lib/generations.ts` exists.

Run: `cd frontend && npm run test`

## Verification (human)

1. Backend running (Redis + worker + daphne), `npm run dev`.
2. Open `/cv/tailored`, paste a posting, pick `light` + an alias, **Generate**. The button shows
   the live stage; on completion the tailored CV + cover letter render.
3. Badges: `ai_share` shows `NN% AI` (green/amber), grounding shows grounded / "N claims" / "not
   checked"; hover the grounding badge → claim list.
4. `standard` + personal paragraph on a web-search alias → personal block renders with sources; on
   `light` / non-capable alias → the loud stub block (distinct destructive styling).
5. **Refresh mid-run**: the page rehydrates from the REST `GET` (and re-subscribes if still
   running), then shows the result. Done = the info is in the frontend, no editor.
