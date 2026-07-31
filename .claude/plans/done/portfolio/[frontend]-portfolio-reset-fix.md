# [frontend] portfolio reset fix — no more localStorage dead-end

> **Portfolio phase, hardening guide (sits between guide 3 and guide 4).** Roadmap: #1 portfolio
> generator. This is a bug-fix guide for shipped guide-3 code (`[frontend]-portfolio-public`),
> not a new feature.
>
> **Branch:** `frontend/portfolio-reset-fix`, cut off **`portfolio`** (not `main`) — guides 1–3
> are not merged to `main` yet, so the files this touches (`explore.tsx`, `escape-hatch.tsx`, …)
> only exist on `portfolio`. `/wrap-up` merges back into `portfolio`.

## Context / goal

Clicking **"I feel lucky"** (or any questionnaire path) drops an anonymous visitor onto
`/explore` and **traps them there** — the only escape observed in testing was clearing all
localStorage. Three defects in the guide-3 public flow combine to cause it:

1. **The stamp is rewritten on every `/explore` load.** `explore.tsx` has a `useEffect`
   (current L31-38) that calls `writeStamp({kind:"native", …})` whenever `portfolio.data`
   arrives. So even when the escape hatch clears the stamp and navigates away, the destination
   re-stamps the visitor, and `/` (`index.tsx` L26-36) redirects them straight back. Only wiping
   localStorage breaks the loop. **This is the trap.**
2. **The escape hatch goes to the wrong place.** `escape-hatch.tsx` navigates to `/explore`
   (another native view of the same owner), never back to `/` where the questionnaire lives — so
   "go back to the real questionnaire" is impossible.
3. **Failure/empty states have no escape at all.** When the native call 404s (owner unset /
   transient) the `!portfolio.data` branch (L47-54) renders a dead message with no button. When
   the call returns **200 but empty** (owner has no `favourite=True` rows / sparse career DB),
   `PortfolioPage` renders just a header — the "empty page" from the report — because both its
   sections guard on `length > 0` (`portfolio-page.tsx` L51, L62) and there's no empty-state.

This guide fixes all three and adds the requested **"Feeling lucky again"** reshuffle:

- The stamp is written **once, at the moment the visitor answers** (`index.tsx`'s `onDone`), and
  **never** on a passive `/explore` load. Clearing it therefore sticks.
- The escape hatch's primary action becomes **"Start over"** → `clearStamp()` + navigate to `/`
  (the questionnaire), so the visitor always has a one-click route back to the front door.
- A **"Feeling lucky again"** action appears on lucky views and refetches a fresh random sample
  (the backend already reseeds per request — `build_payload(lucky=True)` uses `seed=None`,
  `spa/portfolio.py:334`), so lucky is repeatable, as asked (rate-limiting makes this safe).
- Both the **404 failure** branch and the **empty-content** case get a visible escape + a plain
  empty-state, so a visitor is never stranded.

**Trade-off (documented):** dropping the passive-load stamp write means a visitor who arrives via
a *shared* `/explore?d=…` link (never went through `/`) is no longer remembered on return. That's
acceptable — they didn't answer anything here, and the sticky behaviour it enabled is exactly the
bug. Stamping stays tied to a deliberate questionnaire answer.

**Out of scope (flagged, not fixed here):** `lib/portfolio/content.ts`'s `reorderByRank` is a
stub (`console.log`, returns `undefined`) — the free-text `?q=` finale's rank result is silently
ignored today. That's a separate guide-3 gap; leave it for the guide-4/5 activation pass. This
guide only *adds* `isEmptyPayload` to that file and does not touch `reorderByRank`.

## Affected files

| file | why |
| --- | --- |
| `frontend/src/lib/portfolio/stamp.ts` | + pure `nativeStamp(search)` — the single stamp-build point, testable, drops `q` |
| `frontend/src/lib/portfolio/content.ts` | + pure `isEmptyPayload(featured, more)` — the empty-state predicate (leave `reorderByRank` alone) |
| `frontend/src/components/portfolio/escape-hatch.tsx` | rework: "Start over" → `/`; optional "Feeling lucky again" (`onShuffle`) |
| `frontend/src/components/portfolio/portfolio-page.tsx` | render an empty-state when `isEmptyPayload` |
| `frontend/src/routes/explore.tsx` | **remove** the passive-load `writeStamp` effect; escape in the 404 branch; wire `onShuffle` for lucky |
| `frontend/src/routes/index.tsx` | write the stamp via `nativeStamp` (the one deliberate write point) |
| `frontend/tests/lib/portfolio/reset.test.ts` | **new** — red tests for `nativeStamp` + `isEmptyPayload` (AI-written, below) |

## The code

Type in this order (helpers first, so the components/routes compile against them).

### 1. `frontend/src/lib/portfolio/stamp.ts`

Add the type import at the top (type-only — no cycle: `questionnaire.ts` doesn't import `stamp.ts`):

```ts
import { z } from "zod";
import type { ExploreSearch } from "@/lib/portfolio/questionnaire";
```

Then add this export at the end of the file (after `clearStamp`):

```ts
/** The stamp to persist for a completed native questionnaire. The free-text `q` is
 *  deliberately dropped — a stale query re-ranking on every return visit would burn the
 *  6/h rank budget for nothing. This is the ONE place a native stamp is built; the
 *  passive /explore load must never write one (that was the reset trap). */
export function nativeStamp(search: ExploreSearch): Stamp {
  return { kind: "native", search: { d: search.d, lucky: search.lucky } };
}
```

### 2. `frontend/src/lib/portfolio/content.ts`

Leave `reorderByRank` exactly as-is (out of scope). Add:

```ts
import type { PortfolioItem } from "@/lib/queries/portfolio";

/** A native/link payload with nothing to show. The visitor must never be trapped on a
 *  blank page — portfolio-page renders an empty-state + the escape hatch instead. Takes
 *  the *resolved* lists (a rank reorder never changes counts, so payload.more is fine). */
export function isEmptyPayload(
  featured: PortfolioItem[],
  more: PortfolioItem[],
): boolean {
  return featured.length === 0 && more.length === 0;
}
```

### 3. `frontend/src/components/portfolio/escape-hatch.tsx`

Full replacement:

```tsx
import { useNavigate } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { clearStamp } from "@/lib/portfolio/stamp";

/** Shown on every personalised view so the visitor is never trapped. "Start over"
 *  clears the stamp and returns to the questionnaire at "/"; the optional
 *  "Feeling lucky again" reshuffles a lucky view in place (parent passes onShuffle). */
export function EscapeHatch({ onShuffle }: { onShuffle?: () => void }) {
  const navigate = useNavigate();
  return (
    <div className="flex items-center justify-center gap-4 border-b bg-muted/50 px-4 py-1.5 text-sm">
      <span className="text-muted-foreground">
        You're seeing a personalised page.
      </span>
      {onShuffle ? (
        <Button
          variant="link"
          size="sm"
          className="h-auto p-0"
          onClick={onShuffle}
        >
          Feeling lucky again
        </Button>
      ) : null}
      <Button
        variant="link"
        size="sm"
        className="h-auto p-0"
        onClick={() => {
          clearStamp();
          navigate({ to: "/" });
        }}
      >
        Start over
      </Button>
    </div>
  );
}
```

### 4. `frontend/src/components/portfolio/portfolio-page.tsx`

Add the import:

```tsx
import { isEmptyPayload } from "@/lib/portfolio/content";
```

Then add an empty-state section as the **last child** of `<main>` (after the `more` section,
before `</main>`):

```tsx
      {isEmptyPayload(payload.featured, more) && (
        <section className="py-12 text-center text-muted-foreground">
          <p>Nothing to show for this selection yet.</p>
          <p className="text-sm">Try “Start over” above to pick a different path.</p>
        </section>
      )}
```

### 5. `frontend/src/routes/explore.tsx`

Full replacement — note the removed `useEffect`/`writeStamp`, the new `clearStamp`/`useNavigate`/
`Button` imports, the escape in the 404 branch, and `onShuffle` on the hatch:

```tsx
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { EscapeHatch } from "@/components/portfolio/escape-hatch";
import { PortfolioPage } from "@/components/portfolio/portfolio-page";
import { reorderByRank } from "@/lib/portfolio/content";
import { clearStamp } from "@/lib/portfolio/stamp";
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
  const navigate = useNavigate();
  const portfolio = useNativePortfolio(search);
  const rank = usePortfolioRank(search);

  // NOTE: no stamp is written here. The stamp is set once, when the visitor answers
  // the questionnaire (index.tsx onDone). Writing it on every passive load was the
  // reset trap — it re-stamped a visitor the moment the escape hatch cleared them.

  if (portfolio.isPending) {
    return (
      <main className="min-h-screen grid place-items-center text-muted-foreground">
        Loading…
      </main>
    );
  }
  if (!portfolio.data) {
    // Owner unset (native flow off) or transient failure — always offer a way back,
    // and clear the stamp so "/" doesn't bounce the visitor straight back here.
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
      <PortfolioPage payload={portfolio.data} moreOverride={more} />
    </>
  );
}
```

> `reorderByRank` still returns `undefined` (out-of-scope stub) — so `more` is `undefined`
> whenever a `?q=` rank succeeds, and `PortfolioPage` falls back to `payload.more` via its
> `moreOverride ?? payload.more`. Harmless today; fix belongs to the guide-4/5 activation.

### 6. `frontend/src/routes/index.tsx`

Swap the inline stamp object for the shared helper. Change the import:

```tsx
import { readStamp, writeStamp, nativeStamp } from "@/lib/portfolio/stamp";
```

and the `onDone` handler (current L70-77):

```tsx
          <Questionnaire
            onDone={(search) => {
              writeStamp(nativeStamp(search));
              navigate({ to: "/explore", search });
            }}
          />
```

## Tests

**AI-written, on disk, red before you code:** `frontend/tests/lib/portfolio/reset.test.ts`.

- `nativeStamp` — drops the free-text `q`; preserves `d` and `lucky`; carries a lucky-only
  answer; the stamp it builds round-trips through `writeStamp`/`readStamp` (injected in-memory
  storage, the `stamp.ts` idiom).
- `isEmptyPayload` — `true` for empty/empty; `false` when `featured` has items; `false` when
  `more` has items.

Run:

```bash
cd frontend && npx vitest run tests/lib/portfolio/reset.test.ts
```

**Not unit-tested (regime limitation — flagged per `[[frontend-test-layout]]`):** the three
behavioural fixes live in React components/routes (stamp *not* rewritten on passive load, escape
hatch → `/`, lucky reshuffle, 404-branch escape). The current `tests/` regime is node-env pure-
`lib` only (no jsdom / testing-library), so these are covered by the **Verification** click-through
below, not by vitest. When the component-test regime lands, they should get real tests.

## Verification

Anonymous window (no session), dev stack running (`runserver` + Vite):

1. **The trap is gone.** From `/`, answer the questionnaire → land on `/explore`. Click
   **Start over** → you're back at `/` on the **questionnaire** (not redirected back to
   `/explore`). Reload `/` → still the questionnaire (stamp cleared, stays cleared).
2. **Lucky is repeatable.** From `/`, pick **"I feel lucky"** → result page. Click **Feeling
   lucky again** → the "More to explore" set changes (fresh server sample). Repeat a few times;
   the 6/h throttle is on `rank`, not the native call, so reshuffle isn't limited.
3. **Empty content no longer strands you.** Temporarily give the owner user (`Lukas`, per
   `PORTFOLIO_OWNER_USERNAME`, `settings.py:294`) **zero** `favourite=True` rows *and* filter to a
   domain they have no entries in → `/explore` shows the **"Nothing to show…"** empty-state with
   the escape bar above it, not a blank page. Then flag one entry `favourite=True` and confirm it
   appears under "Highlights".
4. **404 branch has an escape.** Set `PORTFOLIO_OWNER_USERNAME` to an unknown username, restart,
   hit `/explore` → "isn't available" **with a "Back to start" button**; click it → `/`, stamp
   cleared. (Restore the setting after.)
5. **Return-visit memory still works.** Answer the questionnaire (a real path, e.g. Software →
   AI & data), land on `/explore?d=AI`. Navigate to `/` in the same tab → you're soft-redirected
   back to `/explore?d=AI` (the stamp from your answer). Escape via **Start over** to reset.
6. `cd frontend && npm run build` (tsc clean) and `npx vitest run tests/lib/portfolio/` green.

> If step 3's page is empty for the *real* portfolio (not the forced test), the cause is data,
> not code: the `Lukas` user needs career entries and at least one `favourite=True`. The
> questionnaire domain names must also match the owner's jac `Domain` tags (case-insensitive) —
> see the alignment note in `lib/portfolio/questionnaire.ts:4-6`.

## Results

_(human fills after testing)_
