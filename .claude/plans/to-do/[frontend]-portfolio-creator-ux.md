# [frontend] portfolio creator UX

**Branch:** `frontend/portfolio-creator-ux`
**Roadmap:** portfolio phase follow-up (creator ergonomics). Guide 1 of two — the shared,
searchable **`ContentPicker`** built here is what guide 2 (`[fullstack]-block-links`) reuses for the
block-editor's link picker.

## Context / goal

The "portfolio creator" is the **`LinkEditor`** dialog (`components/portfolio/link-editor.tsx`),
which embeds the **`FeaturedPicker`** (`components/portfolio/featured-picker.tsx`) to curate an
ordered mix of career entries + blocks (`content.featured`, the `"job:12"`/`"block:7"` grammar).
Three problems, all owner-facing:

1. **Overlay overflows.** `DialogContent` (`components/ui/dialog.tsx:62`) is
   `fixed top-1/2 left-1/2 -translate-*` with **no `max-height` and no `overflow`**. The tall
   LinkEditor spills off the top/bottom of the viewport and the Save/Cancel footer becomes
   unreachable.
2. **The content pool is a flat list.** `FeaturedPicker`'s "All content" pane dumps every career
   entry + block into one `max-h-80` scroll. It needs a **search box** and a **web-shop-style facet
   system** (filter by type and by domain/category).
3. **No inline placeholder block.** To feature a not-yet-written block you must leave the creator,
   author the block fully in the Blocks tab, and come back. We want a **"+ Add placeholder"** button
   that mints a real-but-hidden draft block and features it in place, so you can rough out the layout
   and fill the copy later.

Outcome: the creator is a scrollable dialog with a searchable/faceted picker and an inline
placeholder affordance. `FeaturedPicker` is replaced by a reusable **`ContentPicker`** so guide 2
gets the same widget for free. All new list logic is pure functions in `link-form.ts` (the
`frontend/tests/` sweet spot); the component/dialog work is click-through (per
[[frontend-test-layout]] — components deferred until styling settles).

**Defaults locked in this guide** (see the chat that preceded it):
- Placeholder block uses **no new migration** — a seeded `is_active:false` text block. The public
  renderer already filters `is_active=True` (`spa/portfolio.py` `resolve_items`/`_blocks`), so a
  draft never leaks; it still shows in the owner's editor pool.
- Reorder arrows stay (both featured lists are ordered), so `ContentPicker` keeps the two-pane
  chosen/pool shape — it just upgrades the pool pane.

## Affected files

| path | change |
| --- | --- |
| `frontend/src/lib/portfolio/link-form.ts` | add `domainIds` to `FeaturedCandidate`; populate it in `candidates()`; add pure `filterCandidates()` |
| `frontend/src/components/portfolio/content-picker.tsx` | **new** — the reusable two-pane picker (search + type/domain facets + placeholder button). Replaces `featured-picker.tsx`. |
| `frontend/src/components/portfolio/featured-picker.tsx` | **delete** — folded into `content-picker.tsx` |
| `frontend/src/components/portfolio/link-editor.tsx` | import + render `ContentPicker` instead of `FeaturedPicker`; pinned-footer dialog layout |
| `frontend/src/components/portfolio/block-editor.tsx` | pinned-footer dialog layout (same overflow fix; no picker yet — that's guide 2) |
| `frontend/src/components/ui/dialog.tsx` | `DialogContent` gains a `max-h` + `overflow-y-auto` safety net (fixes every dialog) |
| `frontend/tests/lib/portfolio-link-form.test.ts` | extend: `candidates` now carries `domainIds`; new `filterCandidates` suite |

---

## The code

### 1. `frontend/src/lib/portfolio/link-form.ts` — candidate domains + filter

Two edits. First, extend the candidate type and populate `domainIds` in `candidates()`. Career rows
carry `domains: number[]` except `LanguageRow` (no domains M2M) — guard with a soft cast + `?? []`
so the untyped test fixtures (which omit `domains`) don't crash.

Replace the `FeaturedCandidate` type and the `candidates()` body:

```ts
export type FeaturedCandidate = {
  id: string; // "job:12" | "block:7"
  type: string; // career singular ("job", "skill", …) or "block"
  label: string;
  domainIds: number[]; // Domain pks this candidate is tagged with (languages: [])
};
```

```ts
/** The full pool: every career entry (section order, cv-doc labels) then every block.
 *  A block's label is its title, falling back to "<kind> block" for untitled ones.
 *  `domainIds` = the row's Domain-pk tags (empty for languages, which have no domains M2M);
 *  they drive the picker's category facet. */
export function candidates(
  db: CvEntriesResponse | undefined,
  blocks: PortfolioBlockRow[],
): FeaturedCandidate[] {
  const out: FeaturedCandidate[] = [];
  if (db) {
    for (const section of SECTION_ORDER) {
      for (const row of db[section] as AnyRow[]) {
        const id = entryId(section, row.id);
        // Languages have no domains M2M; other rows do. Soft-cast keeps the untyped
        // test fixtures (which omit `domains`) safe.
        const domainIds =
          section === "languages"
            ? []
            : ((row as { domains?: number[] }).domains ?? []);
        out.push({
          id,
          type: parseEntryId(id)!.type,
          label: labelFor(section, row),
          domainIds,
        });
      }
    }
  }
  for (const b of blocks) {
    out.push({
      id: `block:${b.id}`,
      type: "block",
      label: b.title || `${b.kind} block`,
      domainIds: b.domains,
    });
  }
  return out;
}
```

Then append the new pure filter at the end of the file (after `toggleName`):

```ts
/** Narrow the pool for the picker's search + facets. All three filters AND together;
 *  within `domainIds` the match is OR (a candidate passes if it carries ANY selected
 *  domain). An empty filter is a no-op — so `{}` returns the pool untouched. Pure and
 *  immutable, like the rest of this module. */
export function filterCandidates(
  pool: FeaturedCandidate[],
  f: { search?: string; types?: string[]; domainIds?: number[] } = {},
): FeaturedCandidate[] {
  const q = (f.search ?? "").trim().toLowerCase();
  const typeSet = new Set(f.types ?? []);
  const domSet = new Set(f.domainIds ?? []);
  return pool.filter((c) => {
    if (q && !c.label.toLowerCase().includes(q)) return false;
    if (typeSet.size && !typeSet.has(c.type)) return false;
    if (domSet.size && !c.domainIds.some((d) => domSet.has(d))) return false;
    return true;
  });
}
```

> `CANDIDATE_GROUPS` stays as-is (unused elsewhere; harmless). The picker derives its type facets
> from a local order constant below rather than that plural-keyed list.

### 2. `frontend/src/components/portfolio/content-picker.tsx` — new reusable picker

This replaces `FeaturedPicker`. Same two panes (ordered "chosen" on the left, pool on the right), but
the pool pane gains a search input, type facets, domain facets (web-shop style, with live counts),
and the placeholder button. Props are renamed generic (`selected`/`onChange`) so guide 2 can reuse it
verbatim for block links.

```tsx
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { useCvEntries, useFullList, type DomainRow } from "@/lib/queries/jac";
import { usePortfolioBlocks, useSaveBlock } from "@/lib/queries/portfolio";
import {
  candidates,
  filterCandidates,
  moveFeatured,
  resolveFeatured,
  toggleFeatured,
  type FeaturedCandidate,
} from "@/lib/portfolio/link-form";

/** Canonical facet order + labels (candidate.type is the career *singular* or "block"). */
const TYPE_FACETS: { type: string; label: string }[] = [
  { type: "job", label: "Jobs" },
  { type: "project", label: "Projects" },
  { type: "skill", label: "Skills" },
  { type: "education", label: "Education" },
  { type: "certification", label: "Certifications" },
  { type: "language", label: "Languages" },
  { type: "block", label: "Blocks" },
];

/** A dismissable filter chip that shows a count and toggles active styling. */
function FacetChip({
  active,
  count,
  onClick,
  children,
}: {
  active: boolean;
  count: number;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={count === 0 && !active}
      className={
        "rounded-full border px-2.5 py-0.5 text-xs transition disabled:opacity-40 " +
        (active
          ? "border-primary bg-primary text-primary-foreground"
          : "hover:bg-muted")
      }
    >
      {children}
      <span className="ml-1 opacity-70">{count}</span>
    </button>
  );
}

/** Two panes: the ordered chosen list (reorder / remove) on the left; a searchable,
 *  facet-filtered pool (toggle in/out) on the right. Pure ops from link-form.ts. Reused
 *  by the manual-link editor (featured content) and — guide 2 — the block editor (links). */
export function ContentPicker({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const db = useCvEntries();
  const blocks = usePortfolioBlocks();
  const domains = useFullList<DomainRow>("domains");
  const saveBlock = useSaveBlock();

  const pool = useMemo(
    () => candidates(db.data, blocks.data ?? []),
    [db.data, blocks.data],
  );
  const chosen = resolveFeatured(selected, pool);
  const chosenIds = new Set(selected);

  const [search, setSearch] = useState("");
  const [types, setTypes] = useState<string[]>([]);
  const [domainIds, setDomainIds] = useState<number[]>([]);

  // Facet counts reflect *the other* active filters (shop convention), so toggling one
  // facet never zeroes the siblings you might switch to next.
  const typeCounts = useMemo(() => {
    const base = filterCandidates(pool, { search, domainIds });
    const m: Record<string, number> = {};
    for (const c of base) m[c.type] = (m[c.type] ?? 0) + 1;
    return m;
  }, [pool, search, domainIds]);

  const domainCounts = useMemo(() => {
    const base = filterCandidates(pool, { search, types });
    const m: Record<number, number> = {};
    for (const c of base) for (const d of c.domainIds) m[d] = (m[d] ?? 0) + 1;
    return m;
  }, [pool, search, types]);

  // Only offer domain facets that actually tag something in the pool.
  const domainFacets = useMemo(() => {
    const used = new Set(pool.flatMap((c) => c.domainIds));
    return (domains.data ?? []).filter((d) => used.has(d.id));
  }, [pool, domains.data]);

  const visible = useMemo(
    () => filterCandidates(pool, { search, types, domainIds }),
    [pool, search, types, domainIds],
  );

  function toggleType(t: string) {
    setTypes((cur) => (cur.includes(t) ? cur.filter((x) => x !== t) : [...cur, t]));
  }
  function toggleDomain(id: number) {
    setDomainIds((cur) =>
      cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id],
    );
  }

  function addPlaceholder() {
    // A hidden draft block: valid payload (clean() needs a non-empty body), is_active
    // false so the public renderer skips it until it's finished in the Blocks tab.
    saveBlock.mutate(
      {
        input: {
          kind: "text",
          title: "Untitled block",
          body: "(placeholder — edit later)",
          alt_text: "",
          domains: [],
          favourite: false,
          order: 0,
          is_active: false,
        },
      },
      {
        onSuccess: (row) => {
          onChange([...selected, `block:${row.id}`]);
          toast.success("Placeholder added — write it in the Blocks tab");
        },
        onError: () => toast.error("Couldn't add a placeholder block"),
      },
    );
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {/* chosen (ordered) */}
      <div className="space-y-2">
        <p className="text-sm font-medium">Selected ({chosen.length})</p>
        {chosen.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing selected yet — search and pick from the right.
          </p>
        ) : (
          <ul className="space-y-1">
            {chosen.map((c, i) => (
              <li
                key={c.id}
                className="flex items-center gap-2 rounded-md border px-2 py-1 text-sm"
              >
                <span className="flex-1 truncate">{c.label}</span>
                <Badge variant="secondary">{c.type}</Badge>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={i === 0}
                  onClick={() => onChange(moveFeatured(selected, i, -1))}
                >
                  ↑
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={i === chosen.length - 1}
                  onClick={() => onChange(moveFeatured(selected, i, 1))}
                >
                  ↓
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onChange(toggleFeatured(selected, c.id))}
                >
                  ✕
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* searchable + faceted pool */}
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Input
            placeholder="Search content…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8"
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={saveBlock.isPending}
            onClick={addPlaceholder}
          >
            + Placeholder
          </Button>
        </div>

        <div className="space-y-1">
          <div className="flex flex-wrap gap-1">
            {TYPE_FACETS.map((f) => (
              <FacetChip
                key={f.type}
                active={types.includes(f.type)}
                count={typeCounts[f.type] ?? 0}
                onClick={() => toggleType(f.type)}
              >
                {f.label}
              </FacetChip>
            ))}
          </div>
          {domainFacets.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {domainFacets.map((d) => (
                <FacetChip
                  key={d.id}
                  active={domainIds.includes(d.id)}
                  count={domainCounts[d.id] ?? 0}
                  onClick={() => toggleDomain(d.id)}
                >
                  {d.name}
                </FacetChip>
              ))}
            </div>
          )}
        </div>

        <div className="max-h-72 space-y-1 overflow-y-auto pr-1">
          {visible.map((c: FeaturedCandidate) => (
            <label
              key={c.id}
              className="flex items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-muted"
            >
              <Checkbox
                checked={chosenIds.has(c.id)}
                onCheckedChange={() => onChange(toggleFeatured(selected, c.id))}
              />
              <span className="flex-1 truncate">{c.label}</span>
              <Badge variant="outline">{c.type}</Badge>
            </label>
          ))}
          {visible.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {pool.length === 0
                ? "No career entries or blocks yet."
                : "No content matches these filters."}
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
```

> Note the `import React` isn't added — `FacetChip`'s `React.ReactNode` needs the type. Either add
> `import type * as React from "react";` at the top, or type the prop as `children: import("react").ReactNode`.
> Simplest: add `import type { ReactNode } from "react";` and use `children: ReactNode`. Adjust the
> `useMemo`/`useState` import line accordingly (`import { useMemo, useState } from "react";` stays).

### 3. Delete `frontend/src/components/portfolio/featured-picker.tsx`

Its two-pane logic now lives in `content-picker.tsx`. Remove the file:

```bash
git rm frontend/src/components/portfolio/featured-picker.tsx
```

### 4. `frontend/src/components/portfolio/link-editor.tsx` — use the picker + pinned layout

Swap the import and the render, and fix the dialog overflow with a pinned-footer layout (header +
footer stay put, only the middle body scrolls).

- Change the import:

```tsx
import { ContentPicker } from "@/components/portfolio/content-picker";
```

(delete the `FeaturedPicker` import line)

- Change the `DialogContent` opening tag and wrap the body so only it scrolls. The current markup is
  `<DialogContent className="max-w-2xl">`, then `<DialogHeader>…`, then `<div className="space-y-4">…`,
  then `<DialogFooter>…`. Replace the content wrapper:

```tsx
      <DialogContent className="grid max-h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-4 overflow-hidden sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{link ? "Edit link" : "New portfolio link"}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 overflow-y-auto px-1">
          {/* …all the existing fields, unchanged… */}
        </div>

        <DialogFooter>
          {/* …unchanged… */}
        </DialogFooter>
      </DialogContent>
```

Why each class: `overflow-hidden` overrides the primitive's new `overflow-y-auto` (the outer box must
NOT scroll — its inner body does); `grid-rows-[auto_minmax(0,1fr)_auto]` pins header/footer and lets
the middle row flex + shrink to zero (so it scrolls instead of shoving the footer off-screen);
`sm:max-w-2xl` overrides the primitive's `sm:max-w-md` (plain `max-w-2xl` loses to the responsive
base — see the dialog gotcha below); `gap-4` tightens the row gaps.

- Replace the featured render:

```tsx
          <div className="grid gap-2">
            <Label>Featured content</Label>
            <ContentPicker
              selected={draft.featured}
              onChange={(f) => set("featured", f)}
            />
          </div>
```

### 5. `frontend/src/components/portfolio/block-editor.tsx` — pinned layout

Same overflow fix; no picker here (that's guide 2). Change only the `DialogContent` open tag + wrap
the body div:

```tsx
      <DialogContent className="grid max-h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-4 overflow-hidden sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{block ? "Edit block" : "New block"}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 overflow-y-auto px-1">
          {/* …all existing fields unchanged… */}
        </div>

        <DialogFooter>
          {/* …unchanged… */}
        </DialogFooter>
      </DialogContent>
```

### 6. `frontend/src/components/ui/dialog.tsx` — global overflow safety net

So no dialog can ever run off-screen again (even ones that don't adopt the pinned layout). Add
`max-h-[calc(100dvh-2rem)] overflow-y-auto` to the `DialogContent` base className (line 62). The two
editors above opt OUT of this whole-box scroll with their `overflow-hidden` override and scroll their
inner body instead.

```tsx
        className={cn(
          "fixed top-1/2 left-1/2 z-50 grid w-full max-w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 gap-6 rounded-none bg-popover p-6 text-sm text-popover-foreground shadow-md ring-1 ring-foreground/10 duration-100 outline-none max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-md data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
          className
        )}
```

> **Dialog gotcha (why `sm:max-w-2xl`, not `max-w-2xl`):** the base carries `sm:max-w-md`. Under
> `tailwind-merge`, a plain `max-w-2xl` at the call site does NOT displace the responsive
> `sm:max-w-md` (different variant group), so ≥640px you'd still get `md`. The two editors must use
> the `sm:`-prefixed width to actually win. (The old `LinkEditor` used `max-w-2xl` — this quietly
> corrects it.)

---

## Tests

Framework: **vitest**, Node env, `@/` alias, in the standalone `frontend/tests/` tree (see
[[frontend-test-layout]]). Only the pure `link-form.ts` logic is covered — the picker component and
the dialogs are click-through (Verification below).

- `frontend/tests/lib/portfolio-link-form.test.ts` — **extended** (not new):
  - `candidates` now attaches `domainIds` (career rows from `row.domains`, languages `[]`, blocks
    from `b.domains`).
  - new `filterCandidates` suite: search substring (case-insensitive), type facet, domain facet
    (OR within, AND across), empty filter = identity, combined filters.

Run:

```bash
cd frontend && npx vitest run tests/lib/portfolio-link-form.test.ts
```

These land **red**: `filterCandidates` isn't exported yet and `candidates` doesn't yet set
`domainIds`. Green once section 1 is typed.

---

## Verification

1. `cd frontend && npx vitest run tests/lib/portfolio-link-form.test.ts` → green after section 1.
2. `cd frontend && npx tsc -b` → no type errors (the `ContentPicker` `ReactNode` import, the deleted
   `featured-picker` import).
3. Dev app, log in, open **Portfolio → Links → New / Edit**:
   - **Overflow:** on a short window the dialog no longer runs off-screen; the header/title stays
     pinned, Save/Cancel stays pinned, and the middle (including the picker) scrolls. Repeat for
     **Blocks → New block** (make the domain list long).
   - **Search:** type in the pool search — the list narrows by label, case-insensitive.
   - **Facets:** toggle a **type** chip (e.g. "Jobs") → only jobs show; counts on the other chips
     update. Toggle a **domain** chip → only entries/blocks tagged that domain show; combining a type
     + a domain ANDs them. A domain that tags nothing isn't offered.
   - **Placeholder:** click **+ Placeholder** → a toast, and `block:<id>` appears in the Selected
     list. Save the link. In **Blocks**, the new block shows with the `hidden` badge and body
     "(placeholder — edit later)". Edit it, set content + Active, save → it now renders on the public
     page; before that it was correctly absent from the public link.
4. Public page unaffected for existing links (the placeholder stays hidden until activated).

## Results

<!-- filled by Lukas after implementing + testing: raw vitest/tsc output, click-through notes, bugs -->
