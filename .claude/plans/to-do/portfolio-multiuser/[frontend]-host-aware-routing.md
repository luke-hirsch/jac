# [frontend] Host-aware routing — handle from hostname, portfolio at `/<slug>`

**Branch:** `frontend/host-aware-routing` (off `main`).
**Phase:** portfolio-multiuser (guide 3 of 4). Depends on guides 1 (`resolve_owner`,
per-user slugs, handle-based `url`) and 2 (`VITE_BASE_DOMAIN`, `*.localhost` dev).

> This guide **absorbs and finishes** the in-flight single-owner frontend rework
> (`to-do/portfolio-rework/[frontend]-portfolio-flow.md` — the uncommitted `me.tsx`,
> `explore-result.tsx`, and the flat-form `questionnaire.ts`). The flat-form questionnaire
> component spec still lives in that guide; here it is **mounted at `/` on a handle host**
> instead of at `/me`, and the whole flow becomes host-aware.

## Context / goal

One SPA build is served on three kinds of host (apex, `app.`, `<handle>.`). The SPA reads the
handle from `window.location.hostname` and behaves accordingly:

- `<handle>.<domain>/` → that owner's questionnaire (or a return-visitor's result). This is
  the repurposed `/me` flow. Answered state lives in the search: `/?d=&focus=&tone=&q=`.
- `<handle>.<domain>/<slug>` → that owner's specific portfolio (application or custom).
- `app.<domain>/` → the authed tool (redirect to the app home / login).
- apex `/` → in dev, redirect to the app host; in prod it's Django, the SPA never loads.

Because localStorage is **per-origin**, each owner's subdomain remembers its own visitor
independently — the multi-owner "which portfolio did I come for" problem solves itself; no
handle needs to go into the stamp.

## Affected files

| File                                                                     | Change                                                                                                                                     |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `frontend/src/lib/host.ts`                                               | **New.** Pure host parsing: `parseHost`, `siteHost`, `appOrigin`, `currentHandle`.                                                         |
| `frontend/src/routes/index.tsx`                                          | **Rewrite.** Host-branch: handle→questionnaire/result, app→redirect home, apex→redirect app. Absorbs `/me` + `/explore`.                   |
| `frontend/src/routes/$slug.tsx`                                          | **New.** Portfolio view at the subdomain root (host-scoped resolve).                                                                       |
| `frontend/src/components/portfolio/explore-result.tsx`                   | **Fill** (currently empty): the native result view (ports `explore.tsx` + adds focus/tone + AI intro).                                     |
| `frontend/src/lib/portfolio/stamp.ts`                                    | Native stamp carries `focus`/`tone`; drop the handle idea (origin-scoped).                                                                 |
| `frontend/src/lib/queries/portfolio.ts`                                  | `useNativePortfolio` sends `focus`/`tone`; add `useNativeMeta` + `useNativeIntro`; `usePortfolioLink` unchanged (host-scoped server-side). |
| `frontend/src/routes/explore.tsx`, `frontend/src/routes/me.tsx`          | **Delete** (folded into `/`).                                                                                                              |
| `frontend/src/routes/auth.tsx`, `frontend/src/routes/_authenticated.tsx` | Optional guard: redirect to the app origin when not on the app host.                                                                       |
| `frontend/src/routes/portfolio.$slug.tsx`                                | Keep as an in-SPA alias of `$slug` (or delete + repoint callers).                                                                          |
| `frontend/.env`                                                          | `VITE_BASE_DOMAIN` (from guide 2).                                                                                                         |

---

## The code

### 1. `lib/host.ts` — the pure parser (new, tested)

```ts
/** Which host is this SPA instance serving? Derived from the hostname + VITE_BASE_DOMAIN.
 *  localStorage is per-origin, so a handle host's stamp is naturally that owner's alone. */
export type SiteHost =
  | { kind: "apex" }
  | { kind: "app" }
  | { kind: "handle"; handle: string };

const BASE =
  (import.meta.env.VITE_BASE_DOMAIN as string | undefined) ?? "localhost";

export function parseHost(hostname: string, base: string = BASE): SiteHost {
  const h = hostname.toLowerCase().replace(/\.$/, "");
  if (h === base || h === `www.${base}`) return { kind: "apex" };
  const suffix = `.${base}`;
  if (!h.endsWith(suffix)) return { kind: "apex" }; // unknown host → safe apex default
  const sub = h.slice(0, -suffix.length);
  if (sub === "app") return { kind: "app" };
  if (!sub || sub.includes(".")) return { kind: "apex" };
  return { kind: "handle", handle: sub };
}

export function siteHost(): SiteHost {
  return parseHost(window.location.hostname);
}

export function currentHandle(): string | null {
  const h = siteHost();
  return h.kind === "handle" ? h.handle : null;
}

/** The app host's origin, preserving scheme + port (dev: http://app.localhost:5173). */
export function appOrigin(base: string = BASE): string {
  const { protocol, port } = window.location;
  const p = port ? `:${port}` : "";
  return `${protocol}//app.${base}${p}`;
}
```

### 2. `routes/index.tsx` — host-branch home (rewrite)

Replace the whole file. The `exploreSearch` schema moves here (was in `explore.tsx`).

```tsx
import { useEffect, useState } from "react";
import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { z } from "zod";
import { Questionnaire } from "@/components/portfolio/questionnaire";
import { ExploreResult } from "@/components/portfolio/explore-result";
import { hasAnswer } from "@/lib/portfolio/questionnaire";
import { appOrigin, siteHost } from "@/lib/host";
import { nativeStamp, readStamp, writeStamp } from "@/lib/portfolio/stamp";

const exploreSearch = z.object({
  d: z.array(z.string()).optional(),
  lucky: z.boolean().optional(),
  q: z.string().optional(),
  focus: z.string().optional(),
  tone: z.string().optional(),
});

export const Route = createFileRoute("/")({
  validateSearch: exploreSearch,
  head: () => ({ meta: [{ name: "robots", content: "noindex" }] }),
  beforeLoad: () => {
    const host = siteHost();
    if (host.kind === "app") throw redirect({ to: "/applications" });
    if (host.kind === "apex") {
      // Dev only — prod apex is Django-rendered and never loads the SPA.
      window.location.replace(appOrigin());
      throw redirect({ to: "/" }); // unreachable; satisfies the type
    }
    // handle host → render the questionnaire/result below
  },
  component: HandleHome,
});

function HandleHome() {
  const search = Route.useSearch();
  const navigate = useNavigate();
  const [checked, setChecked] = useState(false);

  // Return-visitor dispatch (only when there's no answer in the URL yet). Origin-scoped
  // stamp: a native stamp restores the result; a link stamp jumps to that slug.
  useEffect(() => {
    if (hasAnswer(search)) {
      setChecked(true);
      return;
    }
    const stamp = readStamp();
    if (stamp?.kind === "link") {
      navigate({ to: "/$slug", params: { slug: stamp.slug }, replace: true });
    } else if (stamp?.kind === "native" && hasAnswer(stamp.search)) {
      navigate({ to: "/", search: stamp.search, replace: true });
    } else {
      setChecked(true);
    }
  }, [search, navigate]);

  if (!checked) return null;
  if (hasAnswer(search)) return <ExploreResult search={search} />;

  return (
    <Questionnaire
      onDone={(s) => {
        writeStamp(nativeStamp(s)); // written ONCE, at answer time
        navigate({ to: "/", search: s });
      }}
    />
  );
}
```

> `Questionnaire` is the flat-form component from the portfolio-rework frontend guide — it
> loads domains from `useNativeMeta()` and returns an `ExploreSearch` via `formToSearch` /
> `luckySearch`. If it's still importing the deleted `QUESTIONNAIRE`/`walk` exports (the
> known mid-rework breakage), finish that rewrite here per that guide's spec.

### 3. `routes/$slug.tsx` — portfolio at the subdomain root (new)

Ports `portfolio.$slug.tsx`. Same `usePortfolioLink(slug)` — server-side it now resolves
within the host owner (guide 1), so `jane.host/acme` fetches jane's `acme`.

```tsx
import { useEffect } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { EscapeHatch } from "@/components/portfolio/escape-hatch";
import { PortfolioPage } from "@/components/portfolio/portfolio-page";
import { ApiError } from "@/lib/api";
import { clearStamp, readStamp, writeStamp } from "@/lib/portfolio/stamp";
import { usePortfolioLink } from "@/lib/queries/portfolio";

export const Route = createFileRoute("/$slug")({
  head: () => ({ meta: [{ name: "robots", content: "noindex" }] }),
  component: PortfolioSlug,
});

function PortfolioSlug() {
  const { slug } = Route.useParams();
  const q = usePortfolioLink(slug);
  const navigate = useNavigate();

  useEffect(() => {
    if (q.data) writeStamp({ kind: "link", slug }); // anonymous by construction on a handle host
  }, [q.data, slug]);

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
  if (!q.data) return null;
  return (
    <>
      <EscapeHatch />
      <PortfolioPage payload={q.data} />
    </>
  );
}
```

> On a handle host, the owner is never logged in (the session cookie is host-scoped to
> `app.`), so the old `useAuth`/`status === "anonymous"` self-stamp guard from
> `portfolio.$slug.tsx` is unnecessary here — a handle-host visitor is always anonymous.

### 4. `components/portfolio/explore-result.tsx` — fill it

Ports `explore.tsx`'s result view, adds the AI intro (`useNativeIntro`, skipped for lucky)
and passes `focus`/`tone` through. Signature: `ExploreResult({ search }: { search: ExploreSearch })`.

```tsx
import { useNavigate } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { EscapeHatch } from "@/components/portfolio/escape-hatch";
import { PortfolioPage } from "@/components/portfolio/portfolio-page";
import { reorderByRank } from "@/lib/portfolio/content";
import { clearStamp } from "@/lib/portfolio/stamp";
import type { ExploreSearch } from "@/lib/portfolio/questionnaire";
import {
  useNativeIntro,
  useNativePortfolio,
  usePortfolioRank,
} from "@/lib/queries/portfolio";

export function ExploreResult({ search }: { search: ExploreSearch }) {
  const navigate = useNavigate();
  const portfolio = useNativePortfolio(search);
  const rank = usePortfolioRank(search);
  const intro = useNativeIntro(search); // disabled internally when search.lucky

  if (portfolio.isPending) {
    return (
      <main className="min-h-screen grid place-items-center text-muted-foreground">
        Loading…
      </main>
    );
  }
  if (!portfolio.data) {
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
              navigate({ to: "/" });
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
  const payload = intro.data?.intro
    ? { ...portfolio.data, intro: intro.data.intro }
    : portfolio.data;
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
      <PortfolioPage payload={payload} moreOverride={more} />
    </>
  );
}
```

### 5. `lib/queries/portfolio.ts` — focus/tone + meta + intro

Extend `useNativePortfolio` to pass the style axis, and add the two missing hooks:

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

export type NativeMeta = {
  domains: string[];
  tones: { value: string; label: string }[];
  focuses: { value: string; label: string }[];
};

export function useNativeMeta() {
  return useQuery({
    queryKey: ["portfolio", "meta"],
    queryFn: () => api<NativeMeta>("/api/spa/portfolio/native/meta/"),
    staleTime: Infinity,
    retry: false,
  });
}

/** The AI intro — POST but a read; skipped for lucky ("a reshuffle mustn't burn the
 *  budget"); '' / failure degrades silently to no intro. */
export function useNativeIntro(search: ExploreSearch) {
  return useQuery({
    queryKey: [
      "portfolio",
      "intro",
      search.d ?? [],
      search.q ?? "",
      search.focus,
      search.tone,
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
    enabled: !search.lucky,
    staleTime: Infinity,
    retry: false,
  });
}
```

`PortfolioLinkRow.url` is already the handle-based absolute URL (guide 1's `get_url`), so QR
export needs no change — it encodes `row.url` verbatim.

### 6. `lib/portfolio/stamp.ts` — carry focus/tone

Widen the native search schema and `nativeStamp` (drop `q`, keep the style axis):

```ts
const nativeSearchSchema = z.object({
  d: z.array(z.string()).optional(),
  lucky: z.boolean().optional(),
  focus: z.string().optional(),
  tone: z.string().optional(),
});
```

```ts
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

### 7. Optional guard — keep auth on the app host

In `routes/auth.tsx` and `routes/_authenticated.tsx`, add to `beforeLoad` (before the
existing session logic): if `siteHost().kind !== "app"`, bounce to the app origin so login
always happens where the session cookie lives:

```ts
if (siteHost().kind !== "app") {
  window.location.replace(appOrigin() + location.pathname);
  throw redirect({ to: "/" });
}
```

Nice-to-have; skip if it fights the existing `_authenticated` guard, and revisit.

---

## Tests

Written to disk (red until `lib/host.ts` exists): **`frontend/tests/host.test.ts`** —
`parseHost` maps apex / `www` / `app` / handle / reserved-looking / unknown / trailing-dot /
case, against a fixed base domain. Pure logic, node env (the `tests/` sweet spot — no jsdom).

The existing `frontend/tests/portfolio/*` (questionnaire form↔search) already cover
`formToSearch`/`hasAnswer`/`searchToForm`; extend them only if you change those signatures.

Run: `cd frontend && npx vitest run tests/host.test.ts`

## Verification

1. `npm run dev`; open `http://lukas.localhost:5173/` → the questionnaire; answer it → URL
   becomes `/?d=…&focus=…&tone=…`, the result renders with an AI intro (or none if the tower
   is down). Refresh → the stamp restores the result. "Start over" → back to the questionnaire.
2. `http://lukas.localhost:5173/<a-real-slug>` → that portfolio; a bogus slug → bounces to `/`.
3. Two owners: `jane.localhost:5173/` shows **jane's** domains/content, `lukas.localhost:5173/`
   shows lukas's — same build, different host. Answering on one doesn't leak to the other
   (separate origins ⇒ separate stamps).
4. `http://app.localhost:5173/` → redirects into the authed app (login if signed out).
5. `http://localhost:5173/` (apex, dev) → redirects to `app.localhost:5173`.
6. `npx vitest run` — green after `host.ts` lands.

## Results

- sitehost guards in auth.tsx and \_authenticated.tsx are implemented

Side note: a lot of test fail in the frontend test suite.
