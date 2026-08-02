# [frontend] portfolio flow rework — flat-form questionnaire (the only remaining piece)

> **Superseded except for one piece.** The portfolio-multiuser guide
> `[frontend]-host-aware-routing` **absorbed and finished** this guide's routing rework — `/me`
> folded into `/` (host-branch home), `explore.tsx`/`me.tsx` deleted, `explore-result.tsx` filled,
> `stamp.ts` carries `focus`/`tone`, and the query hooks landed as `useNativeMeta`/`useNativeIntro`.
> All of that is done. The AI intro flows through `payload.intro` (host-aware), so the old
> `aiIntro` prop on `PortfolioPage` is **not** needed.
>
> **What host-aware-routing deliberately left to this guide: the flat-form questionnaire rewrite.**
> `src/lib/portfolio/questionnaire.ts` and `src/components/portfolio/questionnaire.tsx` are still the
> old hardcoded branch-tree version (`QUESTIONNAIRE`/`walk`/`stateToSearch`). That single gap is why:
>
> - the 8 red tests in `frontend/tests/lib/portfolio/flow.test.ts` fail (they import the flat-form
>   API that doesn't exist yet — `formToSearch`/`hasAnswer`/`searchToForm`/`luckySearch`/
>   `DEFAULT_FOCUS`/`DEFAULT_TONE`), and
> - `npx tsc -b` fails with one error: `routes/index.tsx` imports `hasAnswer`, which the old lib
>   doesn't export (the "known mid-rework breakage" the host-aware guide flagged).
>
> The tests are correct and stay as-is — they're the acceptance criteria for the two files below.
> Same branch: **`portfolio-flow-rework`**.

## The remaining work — two files to type

Type step 1 first (the lib the tests + `index.tsx` import), then step 2 (the component).

### 1. `frontend/src/lib/portfolio/questionnaire.ts` — full rewrite (verbatim)

Replace the whole file. Drops the branch tree; pure form↔search helpers only (the `tests/` regime's
sweet spot). This turns the 8 red tests green and clears the `tsc` error.

```ts
/** The native-visitor questionnaire — a flat form now (was a hardcoded branch tree).
 *  Domains come from the owner's real taxonomy at runtime (GET /native/meta/), so this
 *  file holds only the pure form↔search mapping — the tests/ regime's sweet spot. */

export type ExploreSearch = {
  d?: string[];
  lucky?: boolean;
  q?: string;
  focus?: string;
  tone?: string;
};

export const DEFAULT_FOCUS = "balanced";
export const DEFAULT_TONE = "neutral";

export type QuestForm = {
  domains: string[];
  focus: string;
  tone: string;
  query: string;
};

export const EMPTY_FORM: QuestForm = {
  domains: [],
  focus: DEFAULT_FOCUS,
  tone: DEFAULT_TONE,
  query: "",
};

/** The answered questionnaire → the result search. ALWAYS carries focus+tone, so the
 *  result URL is never param-empty — that's how `/` (the handle-host home) distinguishes
 *  "answered" (show the result) from "ask me" (show the questionnaire). */
export function formToSearch(form: QuestForm): ExploreSearch {
  const search: ExploreSearch = { focus: form.focus, tone: form.tone };
  if (form.domains.length) search.d = form.domains;
  const q = form.query.trim();
  if (q) search.q = q;
  return search;
}

/** "I feel lucky" — random picks, no style/domains. */
export function luckySearch(): ExploreSearch {
  return { lucky: true };
}

/** Has the visitor answered? The home route shows the result when true, the questionnaire
 *  when false. focus/tone are always present on a real answer; lucky/d/q also count. */
export function hasAnswer(search: ExploreSearch): boolean {
  return Boolean(
    search.lucky ||
      search.focus ||
      search.tone ||
      (search.d && search.d.length) ||
      search.q,
  );
}

/** Prefill the form from a search (e.g. re-opening the questionnaire on an answer). */
export function searchToForm(search: ExploreSearch): QuestForm {
  return {
    domains: search.d ?? [],
    focus: search.focus ?? DEFAULT_FOCUS,
    tone: search.tone ?? DEFAULT_TONE,
    query: search.q ?? "",
  };
}
```

### 2. `frontend/src/components/portfolio/questionnaire.tsx` — full rewrite

Replace the whole file. Flat form: dynamic domain chips from `useNativeMeta()` + the style axis
(Angle/Tone) + free-text finale + "I feel lucky" + a signup CTA.

> **One deviation from the original guide:** import **`useNativeMeta`** (not `usePortfolioMeta`) —
> the hook was renamed when host-aware-routing landed. The `NativeMeta` shape
> (`{ domains, tones, focuses }`) is identical, so nothing else changes.

```tsx
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useNativeMeta } from "@/lib/queries/portfolio";
import {
  DEFAULT_FOCUS,
  DEFAULT_TONE,
  EMPTY_FORM,
  formToSearch,
  luckySearch,
  type ExploreSearch,
  type QuestForm,
} from "@/lib/portfolio/questionnaire";

const MAX_QUERY_LEN = 280; // mirrors the rank/intro serializer cap

/** One segmented row of the style axis. Options come from /native/meta/; a fallback
 *  keeps the control usable if meta is still loading. */
function StyleAxis({
  label,
  options,
  value,
  onChange,
  fallback,
}: {
  label: string;
  options?: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
  fallback: string;
}) {
  const opts = options?.length ? options : [{ value: fallback, label: fallback }];
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">{label}</p>
      <div className="flex flex-wrap gap-2">
        {opts.map((o) => (
          <Button
            key={o.value}
            size="sm"
            variant={value === o.value ? "default" : "outline"}
            onClick={() => onChange(o.value)}
          >
            {o.label}
          </Button>
        ))}
      </div>
    </div>
  );
}

export function Questionnaire({
  onDone,
}: {
  onDone: (search: ExploreSearch) => void;
}) {
  const meta = useNativeMeta();
  const [form, setForm] = useState<QuestForm>(EMPTY_FORM);

  function toggleDomain(name: string) {
    setForm((f) => ({
      ...f,
      domains: f.domains.includes(name)
        ? f.domains.filter((d) => d !== name)
        : [...f.domains, name],
    }));
  }

  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-6 px-4 py-10">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle>What do you want to see?</CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="space-y-2">
            <p className="text-sm font-medium">What are you interested in?</p>
            <div className="flex flex-wrap gap-2">
              {(meta.data?.domains ?? []).map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => toggleDomain(d)}
                  className="cursor-pointer"
                >
                  <Badge variant={form.domains.includes(d) ? "default" : "outline"}>
                    {d}
                  </Badge>
                </button>
              ))}
              {meta.data && meta.data.domains.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No topics to pick yet — try “I feel lucky”.
                </p>
              ) : null}
            </div>
          </div>

          <StyleAxis
            label="Angle"
            options={meta.data?.focuses}
            value={form.focus}
            onChange={(v) => setForm((f) => ({ ...f, focus: v }))}
            fallback={DEFAULT_FOCUS}
          />
          <StyleAxis
            label="Tone"
            options={meta.data?.tones}
            value={form.tone}
            onChange={(v) => setForm((f) => ({ ...f, tone: v }))}
            fallback={DEFAULT_TONE}
          />

          <div className="space-y-2">
            <p className="text-sm font-medium">Anything specific? (optional)</p>
            <Input
              value={form.query}
              maxLength={MAX_QUERY_LEN}
              placeholder="e.g. building things with local AI models"
              onChange={(e) => setForm((f) => ({ ...f, query: e.target.value }))}
            />
          </div>

          <div className="flex flex-wrap gap-2">
            <Button onClick={() => onDone(formToSearch(form))}>Show me</Button>
            <Button variant="outline" onClick={() => onDone(luckySearch())}>
              I feel lucky
            </Button>
          </div>
        </CardContent>
      </Card>

      <p className="text-sm text-muted-foreground">
        Want your own tailored CV?{" "}
        <Link to="/auth/signup" className="underline">
          Create one here
        </Link>
      </p>
    </main>
  );
}
```

## Tests (already on disk, red — the acceptance criteria)

`frontend/tests/lib/portfolio/flow.test.ts` — no changes needed. It covers `formToSearch` (always
focus+tone; `d` only when domains; trims/drops `q`), `luckySearch`, `hasAnswer` (false for `{}` and
`{ d: [] }`; true for lucky/focus/tone/d/q), `searchToForm` (round-trip + defaults), and `nativeStamp`
(the 2 stamp tests already pass — `stamp.ts` landed). Step 1 turns the other 8 green.

Run: `cd frontend && npx vitest run tests/lib/portfolio/flow.test.ts`

## Verification

1. `npx tsc -b` clean (the `index.tsx → hasAnswer` error is gone).
2. `npx vitest run` fully green (was 8 red in `flow.test.ts`).
3. `npm run dev`, open `http://lukas.localhost:5173/` → the flat questionnaire renders with your
   **real** domain chips (from `/native/meta/`), the Angle + Tone pickers, the free-text box, an
   "I feel lucky" button, and the "Create one here" signup link. Pick a domain + Angle + query →
   **Show me** → `/?d=…&focus=…&tone=…&q=…` renders the result (intro above highlights when the
   tower is up). "I feel lucky" → `/?lucky=1`, no intro call.

## Results

_(human fills after testing)_
