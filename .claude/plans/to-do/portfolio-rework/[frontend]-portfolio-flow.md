# [frontend] portfolio flow rework — `/me` questionnaire, style axis, AI intro

> **Portfolio-rework phase, guide 2 of 3.** SAME branch as guide 1 — **`portfolio-flow-rework`**
> (guides 1+2 share it per the two-branch split; do NOT cut a new branch). Builds on guide 1's
> landed API. Guide 3 (`[frontend]-portfolio-ui`, branch `portfolio-ui-rework`) reworks the render
> layout on top of this.

## Context / goal

Guide 1 fixed the backend (owner-resolution 404, dynamic-domain `meta`, style-aware native payload,
AI-intro endpoint, Django `/` landing). This guide reworks the **SPA flow** to match:

1. **`/` stops being the questionnaire.** Django now owns `/` (the SEO landing). The SPA's `/`
   redirects to **`/me`**, the new home of the interactive "get to know me" flow.
2. **One route, `/me`, is the whole native experience.** No params → the questionnaire (or a
   return-visitor stamp redirect); answer params (`?d=&focus=&tone=&q=` / `?lucky=1`) → the result.
   `/explore` is deleted and folded in.
3. **The questionnaire is a flat form, not a hardcoded branch tree.** Domains come from the owner's
   **real** taxonomy (`GET /native/meta/`); the CV-matrix **style axis** (focus technical↔soft,
   tone personal↔formal — the `PersonalityProfile` vocab) is two segmented pickers; the free-text
   finale stays. "I feel lucky" → random. A **"create your own CV"** signup CTA sits under it.
4. **AI intro.** The result page calls `POST /native/intro/` (throttled, HirschAI) and renders the
   paragraph above the highlights; an empty/failed intro just renders the standard portfolio — no
   dead-end (the Hybrid engine's graceful degrade).
5. **Escape hatch → `/me`** (was `/`), so "Start over" lands on the questionnaire.

Design notes locked from reading the current code:

- **How `/me` tells "answered" from "ask me": `focus`+`tone` are ALWAYS present on a real answer**
  (`formToSearch` sets them), so `hasAnswer(search)` is unambiguous even for a "show me everything"
  submit with no domains. `lucky`/`d`/`q` also count. Truly empty search → the questionnaire.
- **The stamp now carries `focus`/`tone`** so a return visitor keeps their style. `q` is still
  dropped (the reset-fix budget rule — a stale query must not re-fire the throttled rank on every
  return). `nativeStamp` stays the single write point (reset-fix invariant).
- **Intro is enabled only for non-lucky answers** — lucky is a quick reshuffle; firing the 6/h intro
  on every reshuffle would 429 for nothing. It degrades to no-intro silently.
- Reuse everything that already works: `reorderByRank`/`isEmptyPayload` (`content.ts`), the
  `EscapeHatch` shuffle, `PortfolioPage`, `usePortfolioRank`. Hooks stay toast-free
  (`lib/queries/` convention).

## Affected files

| file | why |
| --- | --- |
| `frontend/src/lib/portfolio/questionnaire.ts` | **rewrite** — drop the branch tree; pure form↔search helpers (`formToSearch`, `luckySearch`, `hasAnswer`, `searchToForm`) + extended `ExploreSearch` (`focus`/`tone`) |
| `frontend/src/lib/portfolio/stamp.ts` | native stamp schema + `nativeStamp` carry `focus`/`tone` |
| `frontend/src/lib/queries/portfolio.ts` | `useNativePortfolio` sends `focus`/`tone`; new `usePortfolioMeta`, `usePortfolioIntro` |
| `frontend/src/components/portfolio/portfolio-page.tsx` | optional `aiIntro` prop rendered above highlights |
| `frontend/src/components/portfolio/questionnaire.tsx` | **rewrite** — flat form (dynamic domains + style axis + finale + lucky + signup CTA) |
| `frontend/src/components/portfolio/explore-result.tsx` | **new** — the result view (native + rank + intro + escape hatch), ported from `explore.tsx` |
| `frontend/src/components/portfolio/escape-hatch.tsx` | "Start over" → `/me` (was `/`) |
| `frontend/src/routes/me.tsx` | **new** — the `/me` route: dispatcher + questionnaire/result switch |
| `frontend/src/routes/index.tsx` | **replace** — redirect `/` → `/me` |
| `frontend/src/routes/explore.tsx` | **delete** (`rm`) — folded into `/me` |
| `frontend/src/routes/portfolio.$slug.tsx` | revoked-slug fallback nav `/` → `/me` |
| `config/nginx.conf` | documentation-only prod routing snippet (below) |
| `frontend/tests/lib/portfolio/flow.test.ts` | **new** — red tests for the form/stamp helpers |

`frontend/src/routeTree.gen.ts` is **auto-generated** by the router plugin — don't hand-edit it; it
regenerates on `npm run dev` once `me.tsx` exists and `explore.tsx` is gone.

## The code

Type in this order (helpers → queries → components → routes, so each compiles against the last).

### 1. `frontend/src/lib/portfolio/questionnaire.ts` — full rewrite

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

/** The answered questionnaire → the /me result search. ALWAYS carries focus+tone, so the
 *  result URL is never param-empty — that's how `/me` distinguishes "answered" (show the
 *  result) from "ask me" (show the questionnaire). */
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

/** Has the visitor answered? `/me` shows the result when true, the questionnaire when
 *  false. focus/tone are always present on a real answer; lucky/d/q also count. */
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

### 2. `frontend/src/lib/portfolio/stamp.ts` — carry focus/tone

Extend `nativeSearchSchema` (L5-8) and `nativeStamp` (L59-61):

```ts
/** Search params a native stamp restores (mirrors /me's validateSearch, minus q). */
const nativeSearchSchema = z.object({
  d: z.array(z.string()).optional(),
  lucky: z.boolean().optional(),
  focus: z.string().optional(),
  tone: z.string().optional(),
});
```

```ts
/** The stamp to persist for a completed native questionnaire. The free-text `q` is
 *  deliberately dropped — a stale query re-ranking on every return visit would burn the
 *  6/h rank budget for nothing — but the style axis (focus/tone) IS kept so a returning
 *  visitor sees their chosen framing. The ONE place a native stamp is built. */
export function nativeStamp(search: ExploreSearch): Stamp {
  return {
    kind: "native",
    search: {
      d: search.d,
      lucky: search.lucky,
      focus: search.focus,
      tone: search.tone,
    },
  };
}
```

(`JSON.stringify` drops `undefined` keys, so a lucky-only stamp still serialises to
`{d,lucky}` — the existing `reset.test.ts` stays green.)

### 3. `frontend/src/lib/queries/portfolio.ts` — style params, meta, intro

Extend the `questionnaire` import (L3) to pull `hasAnswer`:

```ts
import { hasAnswer, type ExploreSearch } from "@/lib/portfolio/questionnaire";
```

Replace `useNativePortfolio` (L56-67) so it forwards the style axis:

```ts
export function useNativePortfolio(search: ExploreSearch) {
  const params = new URLSearchParams();
  if (search.d?.length) params.set("domains", search.d.join(","));
  if (search.lucky) params.set("lucky", "1");
  if (search.focus) params.set("focus", search.focus);
  if (search.tone) params.set("tone", search.tone);
  const qs = params.toString();
  return useQuery({
    queryKey: ["portfolio", "native", qs],
    queryFn: () =>
      api<PortfolioPayload>(`/api/spa/portfolio/native/${qs ? `?${qs}` : ""}`),
    retry: false,
  });
}
```

Append the meta + intro hooks (near `usePortfolioRank`):

```ts
/** The questionnaire's building blocks — the owner's REAL domains (only ones with
 *  content) + the style-axis vocab. Cached a few minutes; a 404 (owner unset) is an
 *  answer, not a flake. */
export type PortfolioMeta = {
  domains: string[];
  tones: { value: string; label: string }[];
  focuses: { value: string; label: string }[];
};

export function usePortfolioMeta() {
  return useQuery({
    queryKey: ["portfolio", "meta"],
    queryFn: () => api<PortfolioMeta>("/api/spa/portfolio/native/meta/"),
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/** The AI intro — POST but semantically a read. HirschAI-only + 6/h throttled server-
 *  side, so never retry; disabled for lucky (a reshuffle mustn't burn the budget) and
 *  cached per (domains, style, q). An error/"" just means "no intro" (graceful). */
export function usePortfolioIntro(search: ExploreSearch) {
  return useQuery({
    queryKey: [
      "portfolio",
      "intro",
      search.d ?? [],
      search.focus,
      search.tone,
      search.q ?? "",
    ],
    queryFn: () =>
      api<{ intro: string }>("/api/spa/portfolio/native/intro/", {
        method: "POST",
        body: JSON.stringify({
          domains: search.d ?? [],
          query: search.q ?? "",
          focus: search.focus ?? "balanced",
          tone: search.tone ?? "neutral",
        }),
      }),
    enabled: hasAnswer(search) && !search.lucky,
    staleTime: Infinity,
    retry: false,
  });
}
```

### 4. `frontend/src/components/portfolio/portfolio-page.tsx` — `aiIntro` prop

Add the prop and render it between the header and Highlights. Change the signature (L5-14) and
insert one block after `</header>` (before the `payload.featured` section):

```tsx
export function PortfolioPage({
  payload,
  moreOverride,
  aiIntro,
}: {
  payload: PortfolioPayload;
  /** /me passes a rank-reordered "more" list; default is the server order. */
  moreOverride?: PortfolioPayload["more"];
  /** The personalised AI welcome paragraph (native flow). Absent/"" → nothing shown. */
  aiIntro?: string;
}) {
```

```tsx
      </header>

      {aiIntro ? (
        <section className="rounded-lg border bg-muted/40 p-4">
          <p className="text-sm leading-relaxed">{aiIntro}</p>
        </section>
      ) : null}

      {payload.featured.length > 0 && (
```

### 5. `frontend/src/components/portfolio/questionnaire.tsx` — full rewrite

```tsx
import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { usePortfolioMeta } from "@/lib/queries/portfolio";
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
  const meta = usePortfolioMeta();
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

### 6. `frontend/src/components/portfolio/explore-result.tsx` — **new** (ported from `explore.tsx`)

```tsx
import { useNavigate } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { EscapeHatch } from "@/components/portfolio/escape-hatch";
import { PortfolioPage } from "@/components/portfolio/portfolio-page";
import { reorderByRank } from "@/lib/portfolio/content";
import type { ExploreSearch } from "@/lib/portfolio/questionnaire";
import { clearStamp } from "@/lib/portfolio/stamp";
import {
  useNativePortfolio,
  usePortfolioIntro,
  usePortfolioRank,
} from "@/lib/queries/portfolio";

/** The native result for an answered questionnaire. Native (selection) + rank (the `?q=`
 *  reorder of "more") + intro (the AI welcome paragraph) — each degrades independently. */
export function ExploreResult({ search }: { search: ExploreSearch }) {
  const navigate = useNavigate();
  const portfolio = useNativePortfolio(search);
  const rank = usePortfolioRank(search);
  const intro = usePortfolioIntro(search);

  if (portfolio.isPending) {
    return (
      <main className="min-h-screen grid place-items-center text-muted-foreground">
        Loading…
      </main>
    );
  }
  if (!portfolio.data) {
    // Owner unset / transient failure — never a dead end. Clear the stamp so `/me`
    // doesn't bounce the visitor straight back here.
    return (
      <main className="min-h-screen grid place-items-center">
        <div className="space-y-3 text-center">
          <p className="text-muted-foreground">
            The portfolio isn't available right now.
          </p>
          <Button
            variant="outline"
            onClick={() => {
              clearStamp();
              navigate({ to: "/me" });
            }}
          >
            Back to start
          </Button>
        </div>
      </main>
    );
  }

  const more = rank.data
    ? reorderByRank(portfolio.data.more, rank.data.ranked)
    : portfolio.data.more;
  return (
    <>
      <EscapeHatch
        onShuffle={search.lucky ? () => portfolio.refetch() : undefined}
      />
      {search.q && rank.isError ? (
        <p className="text-center text-xs text-muted-foreground pt-2">
          Couldn't rank by your interest just now — showing the natural order.
        </p>
      ) : null}
      <PortfolioPage
        payload={portfolio.data}
        moreOverride={more}
        aiIntro={intro.data?.intro}
      />
    </>
  );
}
```

### 7. `frontend/src/components/portfolio/escape-hatch.tsx` — "Start over" → `/me`

Change the doc comment and the navigate target (L5-7, L29-31):

```tsx
/** Shown on every personalised view so the visitor is never trapped. "Start over"
 *  clears the stamp and returns to the questionnaire at "/me"; the optional
 *  "Feeling lucky again" reshuffles a lucky view in place (parent passes onShuffle). */
```

```tsx
        onClick={() => {
          clearStamp();
          navigate({ to: "/me" });
        }}
```

### 8. `frontend/src/routes/me.tsx` — **new**

```tsx
import { useEffect } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { z } from "zod";
import { ExploreResult } from "@/components/portfolio/explore-result";
import { Questionnaire } from "@/components/portfolio/questionnaire";
import { useAuth } from "@/lib/auth";
import { hasAnswer } from "@/lib/portfolio/questionnaire";
import { nativeStamp, readStamp, writeStamp } from "@/lib/portfolio/stamp";

const meSearch = z.object({
  d: z.array(z.string()).optional(),
  lucky: z.boolean().optional(),
  q: z.string().optional(),
  focus: z.string().optional(),
  tone: z.string().optional(),
});

export const Route = createFileRoute("/me")({
  validateSearch: meSearch,
  head: () => ({ meta: [{ name: "robots", content: "noindex" }] }),
  component: MeRoute,
});

function MeRoute() {
  const search = Route.useSearch();
  const { status, isPending } = useAuth();
  const navigate = useNavigate();
  const answered = hasAnswer(search);

  // Return-visitor dispatch — only when NOT answered and anonymous. A stamped visitor is
  // bounced to their remembered view; a degenerate native stamp (no real answer) falls
  // through to the questionnaire, so there's no redirect loop.
  useEffect(() => {
    if (answered || isPending || status !== "anonymous") return;
    const stamp = readStamp();
    if (stamp?.kind === "link") {
      navigate({
        to: "/portfolio/$slug",
        params: { slug: stamp.slug },
        replace: true,
      });
    } else if (stamp?.kind === "native" && hasAnswer(stamp.search)) {
      navigate({ to: "/me", search: stamp.search, replace: true });
    }
  }, [answered, isPending, status, navigate]);

  if (answered) return <ExploreResult search={search} />;

  // The stamp is written ONCE here, at the moment of answering (reset-fix invariant).
  return (
    <Questionnaire
      onDone={(s) => {
        writeStamp(nativeStamp(s));
        navigate({ to: "/me", search: s });
      }}
    />
  );
}
```

### 9. `frontend/src/routes/index.tsx` — redirect `/` → `/me`

Full replacement (the old dispatcher/questionnaire moves to `/me`; in prod `/` is served by
Django, so this route only runs under the Vite dev server):

```tsx
import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/")({
  // `/` is the Django-rendered landing in prod (nginx routes it there). Under the Vite
  // dev server the SPA still owns `/`, so send it to the interactive flow.
  beforeLoad: () => {
    throw redirect({ to: "/me" });
  },
});
```

### 10. `frontend/src/routes/explore.tsx` — delete

```bash
rm frontend/src/routes/explore.tsx
```

### 11. `frontend/src/routes/portfolio.$slug.tsx` — revoked-slug fallback → `/me`

In the 404 effect (L29-35), change the two `to: "/"` navigations… there is only one; change it:

```tsx
      navigate({ to: "/me", replace: true });
```

### 12. `config/nginx.conf` — documentation-only (prod routing split)

The file is still empty; when prod serving is configured it must route `/` (and `/health/`) to
Django and everything else to the built SPA. Recorded so it isn't forgotten:

```nginx
# Root + health: the Django-rendered SEO landing and the liveness check.
location = /        { proxy_pass http://app; }
location = /health/ { proxy_pass http://app; }

# API + admin + auth → Django.
location /api/     { proxy_pass http://app; }
location /admin/   { proxy_pass http://app; }
location /_allauth/{ proxy_pass http://app; }

# media (block images + avatars) — Django only serves media in DEBUG.
location /media/ { alias /path/to/backend/media/; }

# Everything else → the SPA (client-routed: /me, /portfolio/*, the authed app).
# noindex belt for personalised pages (the SPA meta tag is the primary signal).
location / {
    add_header X-Robots-Tag "noindex" always;
    try_files $uri /index.html;
}
```

(Dev needs none of this: Vite serves the SPA on `:5173` and proxies `/api` to Django `:8000`; the
Django landing is at `:8000/` directly.)

## Tests

AI-written, on disk, **red before you code**. Pure-lib only, node env, injected storage — the
`frontend/tests/` regime (`[[frontend-test-layout]]`); the routes/components are click-through
verified (no jsdom yet), same as the reset-fix.

- **`frontend/tests/lib/portfolio/flow.test.ts`** — **new**:
  - `formToSearch` — always sets `focus`+`tone`; adds `d` only when domains non-empty; trims `q`
    and drops it when blank.
  - `luckySearch` — `{ lucky: true }`, nothing else.
  - `hasAnswer` — true for lucky / focus / tone / non-empty d / q; **false for `{}`** (the
    "show the questionnaire" case) and for `{ d: [] }`.
  - `searchToForm` — round-trips a `formToSearch` result; fills `DEFAULT_FOCUS`/`DEFAULT_TONE` when
    absent.
  - `nativeStamp` — now **keeps `focus`/`tone`**, still drops `q`, and round-trips through
    `writeStamp`/`readStamp` + the zod schema (injected in-memory storage).

The existing `tests/lib/portfolio/reset.test.ts` stays green (Vitest `toEqual` ignores the new
`undefined` focus/tone keys) — don't touch it.

Run: `cd frontend && npx vitest run tests/lib/portfolio/`

## Verification

1. Backend on (guide 1 landed, tower up), `PORTFOLIO_OWNER_USERNAME` resolving; `npm run dev`
   (the router regenerates `routeTree.gen.ts` — `explore` gone, `me` present).
2. `npx tsc -b` clean; `npx vitest run` green.
3. **Fresh private window → `/`** (Vite `:5173`) → redirects to `/me` → the questionnaire with your
   **real** domain chips (from `/native/meta/`), the Angle + Tone pickers, the free-text box, an
   "I feel lucky" button, and the "Create one here" signup link (→ `/auth/signup`).
4. Pick a domain + Angle=technical + a query → **Show me** → `/me?d=…&focus=technical&tone=…&q=…`
   renders: the AI intro paragraph (tower up) above **Highlights**, a skills-first order, and a
   rank-reordered "More". Change Angle to soft on a re-answer → order visibly shifts.
5. **I feel lucky** → `/me?lucky=1` → random selection, **no** intro call (check the network tab —
   no `/intro/` POST), "Feeling lucky again" reshuffles.
6. **Escape hatch → Start over** → `/me` questionnaire (stamp cleared, stays cleared on reload).
7. **Return memory** → answer (Angle=technical, a domain), leave, hit `/me` again in the same tab →
   soft-redirected back to your answered result **with the style preserved** (focus/tone from the
   stamp). Open a manual `/portfolio/<slug>`, revoke it in admin, reload → lands on `/me`
   questionnaire (stamp self-cleared).
8. **Graceful intro** → stop the tower → answer again → the result renders with **no** intro block
   (not an error). Owner unset (`PORTFOLIO_OWNER_USERNAME=` empty, restart) → `/me` result shows
   "isn't available" + **Back to start**.
9. Django landing still at `:8000/` (guide 1) with its "Explore my work →" pointing at
   `FRONTEND_URL/me`.

## Results

_(human fills after testing)_
