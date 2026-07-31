# [frontend] portfolio public — routes, questionnaire, stamp, renderer

> **Portfolio phase, guide 3 of 5.** Roadmap: #1 portfolio generator (plan:
> `~/.claude/plans/fizzy-cooking-sparrow.md`). Requires guides 1–2 (backend) merged. Queued
> behind the active SPA-phase stack.
>
> **Step 0 — activation pass (AI):** cut branch `frontend/portfolio-public` off `main`,
> re-verify anchors, land the red tests listed in **Tests**.

## Context / goal

The public face: `/portfolio/$slug` renders a personalised link's payload, `/explore` renders
the native (questionnaire-driven) view, and `/` becomes a dispatcher — authenticated users see
today's welcome, anonymous **stamped** visitors are soft-redirected to "their" view, anonymous
fresh visitors get the questionnaire. Scenario 1 (manual links, authored via admin until
guide 5) and scenario 3 go live with this guide.

Mechanics locked in the plan:

- **The stamp is localStorage, never an HTTP 301** (browsers cache 301s uncorrectably). Key
  `portfolio.stamp.v1`, zod-parsed — corrupt/unknown shapes read as absent. Written on a
  successful **anonymous** load (owner previews must not self-stamp); cleared by the escape
  hatch or by a stamped slug 404ing (revoked → visitor falls through to the native flow).
  Multi-slug on one browser: last-writer-wins, by design.
- **Questionnaire is hardcoded TS config** (`lib/portfolio/questionnaire.ts`) — pure `walk()`,
  deterministic, exactly what `frontend/tests/` unit-tests best. Question edits = deploy.
- **The result is a URL** (`/explore?d=…&lucky=…&q=…`) — shareable, stateless server-side; the
  free-text finale travels as `?q=` and triggers the throttled rank endpoint, whose result
  reorders the "more" list only (featured order untouched). A 429/failed rank degrades to
  natural order with a quiet note.
- **noindex** via router `head` meta on both public routes + `<HeadContent/>` in the root
  (verified available in `@tanstack/react-router` 1.170.11).

## Affected files

| file | why |
| --- | --- |
| `frontend/src/lib/portfolio/stamp.ts` | **new** — zod-parsed localStorage stamp (pure, injectable storage) |
| `frontend/src/lib/portfolio/questionnaire.ts` | **new** — question config + `walk()` + search-param mapping |
| `frontend/src/lib/portfolio/content.ts` | **new** — `reorderByRank` merge helper |
| `frontend/src/lib/queries/portfolio.ts` | **new** — payload types + link/native queries + rank call (toast-free, per convention) |
| `frontend/src/components/portfolio/portfolio-page.tsx` | **new** — payload renderer (hero → featured → explore) |
| `frontend/src/components/portfolio/item-card.tsx` | **new** — per-type cards incl. blocks |
| `frontend/src/components/portfolio/questionnaire.tsx` | **new** — stepper over the config + free-text finale |
| `frontend/src/components/portfolio/escape-hatch.tsx` | **new** — "personalised view" banner, clears stamp |
| `frontend/src/routes/portfolio.$slug.tsx` | **new** — public (outside `_authenticated/`) |
| `frontend/src/routes/explore.tsx` | **new** — public native view |
| `frontend/src/routes/index.tsx` | dispatcher rework |
| `frontend/src/routes/__root.tsx` | add `<HeadContent/>` |

## The code

### 1. `frontend/src/lib/portfolio/stamp.ts`

```ts
import { z } from "zod";

/** Search params a native stamp restores (mirrors /explore's validateSearch). */
const nativeSearchSchema = z.object({
  d: z.array(z.string()).optional(),
  lucky: z.boolean().optional(),
});

const stampSchema = z.union([
  z.object({ kind: z.literal("link"), slug: z.string().min(1) }),
  z.object({ kind: z.literal("native"), search: nativeSearchSchema }),
]);

export type Stamp = z.infer<typeof stampSchema>;

export const STAMP_KEY = "portfolio.stamp.v1";

/** Storage is injectable so tests never stub globals. Corrupt JSON, foreign shapes,
 *  and storage exceptions (Safari private mode) all read as "no stamp". */
export function readStamp(
  storage: Pick<Storage, "getItem"> = localStorage,
): Stamp | null {
  try {
    const raw = storage.getItem(STAMP_KEY);
    if (!raw) return null;
    const parsed = stampSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

export function writeStamp(
  stamp: Stamp,
  storage: Pick<Storage, "setItem"> = localStorage,
): void {
  try {
    storage.setItem(STAMP_KEY, JSON.stringify(stamp));
  } catch {
    /* storage unavailable — personalisation just won't persist */
  }
}

export function clearStamp(
  storage: Pick<Storage, "removeItem"> = localStorage,
): void {
  try {
    storage.removeItem(STAMP_KEY);
  } catch {
    /* ditto */
  }
}
```

### 2. `frontend/src/lib/portfolio/questionnaire.ts`

```ts
/** The native-visitor questionnaire. Hardcoded by design: ~4 nodes on a single-owner
 *  site don't earn a DB model, and a pure config is what the tests/ regime covers best.
 *
 *  ⚠ Domain names below must match the owner's jac Domain tags (case-insensitive —
 *  the backend joins forgivingly, but an unknown name silently widens to the full
 *  portfolio). Alignment checklist lives in this guide's Verification.
 */

export type QOption = {
  id: string;
  label: string;
  /** Chosen domains REPLACE the accumulated set — each step narrows, never unions. */
  domains?: string[];
  lucky?: true;
  /** Next node id; omitted = questionnaire done. */
  next?: string;
};

export type QNode = { id: string; prompt: string; options: QOption[] };

export const QUESTIONNAIRE: QNode[] = [
  {
    id: "start",
    prompt: "What do you want to learn about me?",
    options: [
      { id: "music", label: "Music", domains: ["music"], next: "music-angle" },
      {
        id: "software",
        label: "Software development",
        domains: ["software development", "IT", "web development"],
        next: "software-angle",
      },
      { id: "fashion", label: "Fashion", domains: ["fashion"] },
      { id: "lucky", label: "I feel lucky", lucky: true },
    ],
  },
  {
    id: "software-angle",
    prompt: "Which side of it?",
    options: [
      {
        id: "sw-all",
        label: "The whole picture",
        domains: ["software development", "IT", "web development"],
      },
      { id: "sw-web", label: "Web & product work", domains: ["web development"] },
      { id: "sw-ai", label: "AI & data", domains: ["AI"] },
    ],
  },
  {
    id: "music-angle",
    prompt: "Listening or making?",
    options: [
      { id: "mu-all", label: "Everything music", domains: ["music"] },
      { id: "mu-live", label: "On stage", domains: ["music", "performance"] },
    ],
  },
];

export type QuestState = { domains: string[]; lucky: boolean };

/** Deterministically replay `answers` (option ids, in order) through the config.
 *  Returns the accumulated state and the node still awaiting an answer (null = done).
 *  Unknown ids stop the walk — state so far survives. */
export function walk(
  nodes: QNode[],
  answers: string[],
): { state: QuestState; next: QNode | null } {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  let node: QNode | null = nodes[0] ?? null;
  const state: QuestState = { domains: [], lucky: false };
  for (const answer of answers) {
    if (!node) break;
    const opt = node.options.find((o) => o.id === answer);
    if (!opt) break;
    if (opt.lucky) state.lucky = true;
    if (opt.domains) state.domains = [...opt.domains];
    node = opt.next ? (byId.get(opt.next) ?? null) : null;
  }
  return { state, next: node };
}

/** /explore search params — also what a native stamp stores (+ the finale's q). */
export type ExploreSearch = { d?: string[]; lucky?: boolean; q?: string };

export function stateToSearch(state: QuestState, query?: string): ExploreSearch {
  const search: ExploreSearch = {};
  if (state.lucky) search.lucky = true;
  else if (state.domains.length) search.d = state.domains;
  const q = query?.trim();
  if (q) search.q = q;
  return search;
}

export function searchToState(search: ExploreSearch): QuestState {
  return { domains: search.d ?? [], lucky: search.lucky ?? false };
}
```

### 3. `frontend/src/lib/portfolio/content.ts`

```ts
/** Reorder `items` by the rank result: ranked ids first (rank order), the rest keep
 *  their relative order behind. Ids the client doesn't hold drop silently. */
export function reorderByRank<T extends { id: string }>(
  items: T[],
  ranked: { id: string; score: number }[],
): T[] {
  const pos = new Map(ranked.map((r, i) => [r.id, i]));
  const hit = items
    .filter((i) => pos.has(i.id))
    .sort((a, b) => pos.get(a.id)! - pos.get(b.id)!);
  return [...hit, ...items.filter((i) => !pos.has(i.id))];
}
```

### 4. `frontend/src/lib/queries/portfolio.ts`

```ts
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ExploreSearch } from "@/lib/portfolio/questionnaire";

/** Mirrors spa/portfolio.py build_payload — server-joined, redacted, self-contained. */
export type PortfolioItem = {
  id: string; // "job:12" | "block:7" …
  type:
    | "job"
    | "project"
    | "skill"
    | "education"
    | "certification"
    | "language"
    | "block";
  title: string;
  subtitle?: string;
  description?: string;
  started?: string | null;
  ended?: string | null;
  url?: string;
  domains: string[];
  // block-only:
  kind?: "text" | "image";
  body?: string;
  image_url?: string | null;
  alt_text?: string;
};

export type PortfolioPayload = {
  kind: "manual" | "application" | "native";
  title: string;
  intro: string;
  owner: {
    display_name: string;
    bio: string;
    avatar_url: string | null;
    website?: string;
    linkedin_url?: string;
    github_url?: string;
  };
  featured: PortfolioItem[];
  more: PortfolioItem[];
};

export type RankedId = { id: string; score: number };

export function usePortfolioLink(slug: string) {
  return useQuery({
    queryKey: ["portfolio", "link", slug],
    queryFn: () => api<PortfolioPayload>(`/api/spa/portfolio/links/${slug}/`),
    retry: false, // a 404 is an answer (revoked/unknown), not a flake
  });
}

export function useNativePortfolio(search: ExploreSearch) {
  const params = new URLSearchParams();
  if (search.d?.length) params.set("domains", search.d.join(","));
  if (search.lucky) params.set("lucky", "1");
  const qs = params.toString();
  return useQuery({
    queryKey: ["portfolio", "native", qs],
    queryFn: () =>
      api<PortfolioPayload>(`/api/spa/portfolio/native/${qs ? `?${qs}` : ""}`),
    retry: false,
  });
}

/** The embed finale — POST but semantically a read; cached per (q, d) and never
 *  retried: the 6/h throttle makes retries actively harmful. */
export function usePortfolioRank(search: ExploreSearch) {
  const q = search.q?.trim() ?? "";
  return useQuery({
    queryKey: ["portfolio", "rank", q, search.d ?? []],
    queryFn: () =>
      api<{ ranked: RankedId[] }>("/api/spa/portfolio/native/rank/", {
        method: "POST",
        body: JSON.stringify({ query: q, domains: search.d ?? [] }),
      }),
    enabled: q.length > 0,
    staleTime: Infinity,
    retry: false,
  });
}
```

### 5. `frontend/src/components/portfolio/item-card.tsx`

```tsx
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { PortfolioItem } from "@/lib/queries/portfolio";

function dates(item: PortfolioItem): string {
  if (!item.started && !item.ended) return "";
  const from = item.started?.slice(0, 4) ?? "";
  const to = item.ended ? item.ended.slice(0, 4) : "today";
  return from ? `${from} – ${to}` : "";
}

export function ItemCard({ item }: { item: PortfolioItem }) {
  if (item.type === "block" && item.kind === "image") {
    return (
      <Card className="overflow-hidden">
        {item.image_url ? (
          <img
            src={item.image_url}
            alt={item.alt_text || item.title}
            className="w-full object-cover"
          />
        ) : null}
        {item.title || item.body ? (
          <CardContent className="pt-4 space-y-1">
            {item.title ? <p className="font-medium">{item.title}</p> : null}
            {item.body ? (
              <p className="text-sm text-muted-foreground">{item.body}</p>
            ) : null}
          </CardContent>
        ) : null}
      </Card>
    );
  }

  const when = dates(item);
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{item.title}</CardTitle>
        {(item.subtitle || when) && (
          <p className="text-sm text-muted-foreground">
            {[item.subtitle, when].filter(Boolean).join(" · ")}
          </p>
        )}
      </CardHeader>
      <CardContent className="space-y-2">
        {/* Block bodies are markdown-authored but render as plain text for now —
            upgrade to a markdown renderer once the public styling settles. */}
        {(item.body || item.description) && (
          <p className="text-sm whitespace-pre-wrap">
            {item.body || item.description}
          </p>
        )}
        {item.url ? (
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer"
            className="text-sm underline"
          >
            {item.url}
          </a>
        ) : null}
        {item.domains.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {item.domains.map((d) => (
              <Badge key={d} variant="secondary">
                {d}
              </Badge>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
```

### 6. `frontend/src/components/portfolio/portfolio-page.tsx`

```tsx
import { ItemCard } from "@/components/portfolio/item-card";
import type { PortfolioPayload } from "@/lib/queries/portfolio";

export function PortfolioPage({
  payload,
  moreOverride,
}: {
  payload: PortfolioPayload;
  /** /explore passes a rank-reordered "more" list; default is the server order. */
  moreOverride?: PortfolioPayload["more"];
}) {
  const { owner } = payload;
  const more = moreOverride ?? payload.more;
  return (
    <main className="max-w-4xl mx-auto p-6 space-y-10">
      <header className="flex items-center gap-6">
        {owner.avatar_url ? (
          <img
            src={owner.avatar_url}
            alt={owner.display_name}
            className="size-24 rounded-full object-cover"
          />
        ) : null}
        <div className="space-y-1">
          <h1 className="text-3xl font-bold tracking-tight">
            {payload.title || owner.display_name}
          </h1>
          {payload.intro || owner.bio ? (
            <p className="text-muted-foreground max-w-prose">
              {payload.intro || owner.bio}
            </p>
          ) : null}
          <div className="flex gap-3 text-sm">
            {[owner.website, owner.linkedin_url, owner.github_url]
              .filter((u): u is string => Boolean(u))
              .map((u) => (
                <a key={u} href={u} target="_blank" rel="noreferrer" className="underline">
                  {new URL(u).hostname.replace(/^www\./, "")}
                </a>
              ))}
          </div>
        </div>
      </header>

      {payload.featured.length > 0 && (
        <section className="space-y-4">
          <h2 className="text-xl font-semibold">Highlights</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {payload.featured.map((item) => (
              <ItemCard key={item.id} item={item} />
            ))}
          </div>
        </section>
      )}

      {more.length > 0 && (
        <section className="space-y-4">
          <h2 className="text-xl font-semibold">More to explore</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {more.map((item) => (
              <ItemCard key={item.id} item={item} />
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
```

### 7. `frontend/src/components/portfolio/escape-hatch.tsx`

```tsx
import { useNavigate } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { clearStamp } from "@/lib/portfolio/stamp";

/** Shown on every personalised view: the visitor can always step out to the general
 *  site. Clearing the stamp is what stops "/" from redirecting them back. */
export function EscapeHatch() {
  const navigate = useNavigate();
  return (
    <div className="flex items-center justify-center gap-3 border-b bg-muted/50 px-4 py-1.5 text-sm">
      <span className="text-muted-foreground">
        You're seeing a personalised page.
      </span>
      <Button
        variant="link"
        size="sm"
        className="h-auto p-0"
        onClick={() => {
          clearStamp();
          navigate({ to: "/explore", search: {} });
        }}
      >
        View the general site
      </Button>
    </div>
  );
}
```

### 8. `frontend/src/components/portfolio/questionnaire.tsx`

```tsx
import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  QUESTIONNAIRE,
  stateToSearch,
  walk,
  type ExploreSearch,
} from "@/lib/portfolio/questionnaire";

const MAX_QUERY_LEN = 280; // mirrors the rank serializer's cap

export function Questionnaire({
  onDone,
}: {
  onDone: (search: ExploreSearch) => void;
}) {
  const [answers, setAnswers] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const { state, next } = useMemo(
    () => walk(QUESTIONNAIRE, answers),
    [answers],
  );

  if (next) {
    return (
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>{next.prompt}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {next.options.map((o) => (
            <Button
              key={o.id}
              variant="outline"
              className="justify-start"
              onClick={() => setAnswers((a) => [...a, o.id])}
            >
              {o.label}
            </Button>
          ))}
        </CardContent>
      </Card>
    );
  }

  // Finale: optional free text ("i feel lucky" skips straight to the result).
  if (state.lucky) {
    onDone(stateToSearch(state));
    return null;
  }
  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle>Anything specific you're curious about?</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Input
          value={query}
          maxLength={MAX_QUERY_LEN}
          placeholder="e.g. building things with local AI models"
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="flex gap-2">
          <Button onClick={() => onDone(stateToSearch(state, query))}>
            Show me
          </Button>
          <Button variant="ghost" onClick={() => onDone(stateToSearch(state))}>
            Skip
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
```

Subtle: calling `onDone` during render for the lucky path is fine here because the parent
navigates away in response — but if you prefer purity, wrap it in a `useEffect`. The tests only
cover `walk`/`stateToSearch`; the component is click-through-verified.

### 9. `frontend/src/routes/__root.tsx` — head support

```tsx
import { HeadContent, Outlet, createRootRoute } from "@tanstack/react-router";
import { Toaster } from "@/components/ui/sonner";

export const Route = createRootRoute({
  component: () => (
    <div className="min-h-screen">
      <HeadContent />
      <Outlet />
      <Toaster richColors position="top-right" />
    </div>
  ),
});
```

### 10. `frontend/src/routes/portfolio.$slug.tsx`

```tsx
import { useEffect } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { EscapeHatch } from "@/components/portfolio/escape-hatch";
import { PortfolioPage } from "@/components/portfolio/portfolio-page";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { clearStamp, readStamp, writeStamp } from "@/lib/portfolio/stamp";
import { usePortfolioLink } from "@/lib/queries/portfolio";

export const Route = createFileRoute("/portfolio/$slug")({
  head: () => ({ meta: [{ name: "robots", content: "noindex" }] }),
  component: PortfolioLinkRoute,
});

function PortfolioLinkRoute() {
  const { slug } = Route.useParams();
  const { status, isPending } = useAuth();
  const q = usePortfolioLink(slug);
  const navigate = useNavigate();

  // Successful anonymous load → remember this view. Owner previews never self-stamp.
  useEffect(() => {
    if (q.data && !isPending && status === "anonymous") {
      writeStamp({ kind: "link", slug });
    }
  }, [q.data, isPending, status, slug]);

  // Revoked/unknown slug → drop a matching stamp and fall through to the native flow.
  useEffect(() => {
    if (q.error instanceof ApiError && q.error.status === 404) {
      const stamp = readStamp();
      if (stamp?.kind === "link" && stamp.slug === slug) clearStamp();
      navigate({ to: "/", replace: true });
    }
  }, [q.error, slug, navigate]);

  if (q.isPending) {
    return (
      <main className="min-h-screen grid place-items-center text-muted-foreground">
        Loading…
      </main>
    );
  }
  if (!q.data) return null; // 404 effect above is navigating away
  return (
    <>
      <EscapeHatch />
      <PortfolioPage payload={q.data} />
    </>
  );
}
```

### 11. `frontend/src/routes/explore.tsx`

```tsx
import { useEffect } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";
import { EscapeHatch } from "@/components/portfolio/escape-hatch";
import { PortfolioPage } from "@/components/portfolio/portfolio-page";
import { useAuth } from "@/lib/auth";
import { reorderByRank } from "@/lib/portfolio/content";
import { writeStamp } from "@/lib/portfolio/stamp";
import { useNativePortfolio, usePortfolioRank } from "@/lib/queries/portfolio";

const exploreSearch = z.object({
  d: z.array(z.string()).optional(),
  lucky: z.boolean().optional(),
  q: z.string().optional(),
});

export const Route = createFileRoute("/explore")({
  validateSearch: exploreSearch,
  head: () => ({ meta: [{ name: "robots", content: "noindex" }] }),
  component: ExploreRoute,
});

function ExploreRoute() {
  const search = Route.useSearch();
  const { status, isPending } = useAuth();
  const portfolio = useNativePortfolio(search);
  const rank = usePortfolioRank(search);

  // Remember the answers (not the free-text q — a stale query re-ranking on every
  // return visit would burn the 6/h budget for nothing).
  useEffect(() => {
    if (portfolio.data && !isPending && status === "anonymous") {
      writeStamp({
        kind: "native",
        search: { d: search.d, lucky: search.lucky },
      });
    }
  }, [portfolio.data, isPending, status, search.d, search.lucky]);

  if (portfolio.isPending) {
    return (
      <main className="min-h-screen grid place-items-center text-muted-foreground">
        Loading…
      </main>
    );
  }
  if (!portfolio.data) {
    // Owner unset (native flow off) or transient failure — no dead end.
    return (
      <main className="min-h-screen grid place-items-center text-muted-foreground">
        The portfolio isn't available right now.
      </main>
    );
  }

  const more = rank.data
    ? reorderByRank(portfolio.data.more, rank.data.ranked)
    : portfolio.data.more;
  return (
    <>
      <EscapeHatch />
      {search.q && rank.isError ? (
        <p className="text-center text-xs text-muted-foreground pt-2">
          Couldn't rank by your interest just now — showing the natural order.
        </p>
      ) : null}
      <PortfolioPage payload={portfolio.data} moreOverride={more} />
    </>
  );
}
```

### 12. `frontend/src/routes/index.tsx` — dispatcher

```tsx
import { useEffect, useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Questionnaire } from "@/components/portfolio/questionnaire";
import { useAuth } from "@/lib/auth";
import { readStamp, writeStamp } from "@/lib/portfolio/stamp";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/")({
  component: Home,
});

function Home() {
  const { status, isPending } = useAuth();
  const navigate = useNavigate();
  // Only render content once the stamp check has run — avoids a welcome-page flash
  // before a stamped visitor is redirected.
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (isPending) return; // session unknown — authenticated users are never redirected
    if (status !== "anonymous") {
      setChecked(true);
      return;
    }
    const stamp = readStamp();
    if (stamp?.kind === "link") {
      navigate({
        to: "/portfolio/$slug",
        params: { slug: stamp.slug },
        replace: true,
      });
    } else if (stamp?.kind === "native") {
      navigate({ to: "/explore", search: stamp.search, replace: true });
    } else {
      setChecked(true);
    }
  }, [isPending, status, navigate]);

  if (!checked) return null;

  const authenticated = status === "authenticated";
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-6 px-4 text-center">
      <div className="max-w-xl space-y-4">
        <h1 className="text-4xl font-bold tracking-tight">
          Welcome to my portfolio
        </h1>
        <p className="text-lg text-muted-foreground">
          Tell me what you're here for and I'll show you the right side of me.
        </p>
      </div>

      {authenticated ? (
        <div className="flex flex-wrap items-center justify-center gap-3">
          <Button asChild>
            <Link to="/cv">Go to your CV</Link>
          </Button>
          <Button asChild variant="outline">
            <Link to="/account/profile">Profile</Link>
          </Button>
          <Button asChild variant="outline">
            <Link to="/explore" search={{}}>
              Preview the public portfolio
            </Link>
          </Button>
        </div>
      ) : (
        <>
          <Questionnaire
            onDone={(search) => {
              writeStamp({
                kind: "native",
                search: { d: search.d, lucky: search.lucky },
              });
              navigate({ to: "/explore", search });
            }}
          />
          <p className="text-sm text-muted-foreground">
            Here for the CV tool?{" "}
            <Link to="/auth/login" className="underline">
              Sign in
            </Link>
          </p>
        </>
      )}
    </main>
  );
}
```

## Tests

Landed **red at activation** (step 0). Pure-lib only, per `frontend/tests/` convention (node
env, no jsdom — storage is injected, never stubbed):

- `frontend/tests/lib/portfolio-stamp.test.ts` — round-trip write/read both stamp kinds;
  corrupt JSON / foreign shape / missing key → null; clear removes; throwing storage (private
  mode) never throws out of the helpers.
- `frontend/tests/lib/portfolio-questionnaire.test.ts` — `walk` determinism: empty answers →
  start node; full paths land the expected domain sets; lucky flag; refinement REPLACES the
  domain list; unknown answer id stops the walk but keeps state; `stateToSearch` (lucky wins
  over domains, blank query dropped) ↔ `searchToState` round-trip.
- `frontend/tests/lib/portfolio-content.test.ts` — `reorderByRank`: ranked-first ordering,
  unranked keep relative order, ids not held by the client drop, empty rank = identity.

Run: `cd frontend && npx vitest run tests/lib/portfolio-*`

## Verification

1. Backend up with guides 1–2, `PORTFOLIO_OWNER_USERNAME` set; `npm run dev`.
2. **Domain alignment checklist** (one-time): every `domains:` name in
   `questionnaire.ts` exists as a jac Domain tag on your entries (add "music" / "fashion" /
   "performance" / "AI" tags where you want those branches to land content). Unknown names
   fall back to the full portfolio — verify each branch shows a *different* selection.
3. Fresh private window → `/` shows the questionnaire; pick software → web → free-text →
   `/explore?d=…&q=…` renders featured + reordered more (tower up). Reload `/` → soft-redirects
   back to `/explore` with your answers; escape hatch → general view, and `/` no longer
   redirects.
4. Open a manual link `/portfolio/<slug>` in the same window → stamped; revoke it in admin →
   reload → lands on `/` questionnaire (stamp self-cleared).
5. Logged in: `/` never redirects; opening your own `/portfolio/<slug>` doesn't overwrite your
   browser stamp and doesn't bump the visit counter.
6. View source / devtools: `<meta name="robots" content="noindex">` present on `/portfolio/*`
   and `/explore`, absent on `/`.
7. `npx vitest run` — green; `npx tsc -b` — clean.

## Results

_(human fills after testing)_
