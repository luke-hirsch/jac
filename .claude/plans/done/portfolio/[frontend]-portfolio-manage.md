# [frontend] portfolio manage — owner UI for blocks, links & the featured picker

> **Portfolio phase, guide 5 of 5.** Roadmap: #1 portfolio generator (plan:
> `~/.claude/plans/fizzy-cooking-sparrow.md`, approved 2026-07-19). Requires guides 1–4 merged
> (models + manage API + public routes + QR). Queued **behind the active SPA-phase stack** — do
> not start while the `[fullstack]-llm-config-rework` … `[fullstack]-chat-assistant-rework`
> guides are open.
>
> **Step 0 — activation pass (AI, when this guide goes active):** cut branch
> `frontend/portfolio-manage` off `main`, re-verify every file:line anchor below against the
> post-SPA-stack code, and land the red test file listed in **Tests**. Only then does Lukas type.

## Context / goal

Guide 1 gave the owner a manage API (`/api/spa/portfolio/manage/blocks/`, `…/links/`) but only
the Django admin to drive it. This guide replaces admin with a real authenticated section at
`/portfolio`: a **links** tab (list every link — manual + application, revoked shown as history —
create/edit a manual link with a **featured-content picker** that mixes career-DB entries and
portfolio blocks, see visit totals, copy/revoke/delete) and a **blocks** tab (CRUD the
text/image blocks, tag them with Domains, upload images).

The one piece of genuinely new, testable logic is the **featured picker's list algebra**: the
`content.featured` array is an ordered list of mixed ids (`"job:12"`, `"block:7"`) chosen from a
pool built out of the live career DB plus the owner's blocks. That lives in a pure
`lib/portfolio/link-form.ts` (the `frontend/tests/` sweet spot), reusing the id grammar and the
`labelFor`/`moveEntry` idioms already in `lib/cv-doc.ts`. Everything else is wiring over the
guide-1 API with the query-hook + dialog patterns already in the app.

Decisions this code embodies (from the approved plan + guides 1–4):

- **Featured is ids, joined at render.** The picker stores `"<type>:<pk>"` ids (the same grammar
  guide 1's `FEATURED_ID_RE` validates and guide 2's `build_payload` resolves); deleted rows drop
  silently, exactly the cv-doc philosophy. The picker never sends labels — those are display-only.
- **Manual links only, here.** Application links are minted by jac's `portfolio-link` action
  (guide 4's export card); this UI creates `manual` links (kind is server-read-only) and lets the
  owner _edit_ any link's title/intro/content — including a frozen application link, because the
  freeze guards against _pipeline_ rewrites, not owner edits (guide 1 serializer note).
- **Domains: names on links, pks on blocks.** A link's `content.domains` is a list of Domain
  _names_ (guide 1 `validate_content`); a block's `domains` M2M is pks (guide 1 serializer). The
  two pickers differ accordingly — don't unify them.
- **Blocks upload multipart; the image is optional except for image blocks.** `api()` gains a
  one-line `FormData` guard so the existing CSRF/credentials path carries the upload.

Routing note — no collision with the public page: guide 3's **flat** `routes/portfolio.$slug.tsx`
(`/portfolio/$slug`, public) and this guide's `_authenticated/portfolio.tsx` layout (`/portfolio`,
authed, children `index`/`links`/`blocks`) are independent route nodes. Static segments outrank
the dynamic one, so `/portfolio/links` resolves to the authed tab and `/portfolio/<slug>` to the
public page; the public route's parent chain never includes `_authenticated`, so it stays public.
**Reserved slugs:** a manual link named `links` or `blocks` would be shadowed by the tabs — the
link editor rejects those two slugs (trivial, in `validate`), and it's a non-issue in practice.

## Affected files

| file                                                      | why                                                                                    |
| --------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `frontend/src/lib/api.ts`                                 | one-line `FormData` guard so multipart uploads skip the auto-JSON `Content-Type`       |
| `frontend/src/lib/portfolio/link-form.ts`                 | **new** — pure featured-picker list algebra (the tested surface)                       |
| `frontend/src/lib/queries/portfolio.ts`                   | + `PortfolioBlockRow`, owner block/link CRUD hooks (extends the file guides 3–4 built) |
| `frontend/src/components/portfolio/featured-picker.tsx`   | **new** — mixed career+block picker (uses `link-form.ts`)                              |
| `frontend/src/components/portfolio/link-editor.tsx`       | **new** — manual-link create/edit dialog                                               |
| `frontend/src/components/portfolio/block-editor.tsx`      | **new** — block create/edit dialog (domains + image)                                   |
| `frontend/src/routes/_authenticated/portfolio.tsx`        | **new** — tab layout (Links / Blocks + `Outlet`), mirrors `account.tsx`                |
| `frontend/src/routes/_authenticated/portfolio/index.tsx`  | **new** — redirect `/portfolio` → `/portfolio/links`                                   |
| `frontend/src/routes/_authenticated/portfolio/links.tsx`  | **new** — link list + editor + visits                                                  |
| `frontend/src/routes/_authenticated/portfolio/blocks.tsx` | **new** — block list + editor                                                          |
| `frontend/src/routes/_authenticated.tsx`                  | + "Portfolio" nav link (L58-73 nav block)                                              |

## The code

### 1. `frontend/src/lib/api.ts` — FormData guard

The helper force-sets `Content-Type: application/json` on any body (L25-27). A `FormData` body
must instead let the browser set `multipart/form-data; boundary=…` itself, so add one clause:

```ts
if (
  init.body !== undefined &&
  !headers.has("Content-Type") &&
  !(init.body instanceof FormData)
) {
  headers.set("Content-Type", "application/json");
}
```

Nothing else changes — CSRF (`X-CSRFToken`) and `credentials: "same-origin"` already apply to the
multipart request.

### 2. `frontend/src/lib/portfolio/link-form.ts` — featured-picker algebra

```ts
/** Pure list algebra for the manual-link "featured content" picker.
 *
 * `content.featured` is an ordered array of mixed ids — career-DB entries
 * ("job:12", the `lib/cv-doc` grammar) and portfolio blocks ("block:7"). The pool of
 * pickable items is the live career DB plus the owner's blocks; the featured array is
 * an ordered subset of their ids. Everything here is immutable and HTTP-free — the
 * `frontend/tests/` regime's sweet spot (mixed-id list ops).
 */
import {
  SECTION_ORDER,
  entryId,
  labelFor,
  parseEntryId,
  type AnyRow,
} from "@/lib/cv-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import type { PortfolioBlockRow } from "@/lib/queries/portfolio";

export type FeaturedCandidate = {
  id: string; // "job:12" | "block:7"
  type: string; // career singular ("job", "skill", …) or "block"
  label: string;
};

/** Group heading a candidate falls under in the picker (matches cv-doc section titles
 *  where it can; blocks get their own group). */
export const CANDIDATE_GROUPS = [
  "jobs",
  "educations",
  "projects",
  "skills",
  "certifications",
  "languages",
  "blocks",
] as const;

/** The full pool: every career entry (section order, cv-doc labels) then every block.
 *  A block's label is its title, falling back to "<kind> block" for untitled ones. */
export function candidates(
  db: CvEntriesResponse | undefined,
  blocks: PortfolioBlockRow[],
): FeaturedCandidate[] {
  const out: FeaturedCandidate[] = [];
  if (db) {
    for (const section of SECTION_ORDER) {
      for (const row of db[section] as AnyRow[]) {
        const id = entryId(section, row.id);
        out.push({
          id,
          type: parseEntryId(id)!.type,
          label: labelFor(section, row),
        });
      }
    }
  }
  for (const b of blocks) {
    out.push({
      id: `block:${b.id}`,
      type: "block",
      label: b.title || `${b.kind} block`,
    });
  }
  return out;
}

/** The featured ids resolved to candidates, in featured order. Ids no longer in the
 *  pool (a deleted career row / block) drop silently — the cv-doc philosophy. */
export function resolveFeatured(
  featured: string[],
  pool: FeaturedCandidate[],
): FeaturedCandidate[] {
  const byId = new Map(pool.map((c) => [c.id, c]));
  return featured
    .map((id) => byId.get(id))
    .filter((c): c is FeaturedCandidate => c !== undefined);
}

/** Add an id to the end, or remove it if already featured. */
export function toggleFeatured(featured: string[], id: string): string[] {
  return featured.includes(id)
    ? featured.filter((f) => f !== id)
    : [...featured, id];
}

/** Swap the entry at `index` with its neighbour (mirrors cv-doc `moveEntry`, flat). */
export function moveFeatured(
  featured: string[],
  index: number,
  delta: -1 | 1,
): string[] {
  const target = index + delta;
  if (
    index < 0 ||
    index >= featured.length ||
    target < 0 ||
    target >= featured.length
  ) {
    return featured;
  }
  const next = [...featured];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

/** Drop any featured id whose row/block no longer exists — call on load so a saved
 *  link doesn't carry ghosts the picker can't render. Order preserved. */
export function pruneFeatured(
  featured: string[],
  pool: FeaturedCandidate[],
): string[] {
  const ids = new Set(pool.map((c) => c.id));
  return featured.filter((id) => ids.has(id));
}

/** Toggle a plain string in a list — the link's domain-*name* picker (blocks use pks,
 *  a different picker). Kept here so both are unit-covered. */
export function toggleName(names: string[], name: string): string[] {
  return names.includes(name)
    ? names.filter((n) => n !== name)
    : [...names, name];
}
```

### 3. `frontend/src/lib/queries/portfolio.ts` — owner CRUD

Append to the file guides 3–4 built (it already exports the public payload types + `PortfolioLinkRow`

- `createApplicationLink` + `revokePortfolioLink`). Extend the imports first:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
```

(the file currently imports only `useQuery` — add `useMutation`, `useQueryClient`)

Then append:

```ts
/* ---------- owner-side management (guide 5) ---------- */

const BLOCKS_URL = "/api/spa/portfolio/manage/blocks/";
const LINKS_URL = "/api/spa/portfolio/manage/links/";

/** Mirrors spa PortfolioBlockSerializer. `image` is a media URL (or "" / null); domains
 *  are pks (the block M2M — links store domain *names* instead). */
export type PortfolioBlockRow = {
  id: number;
  kind: "text" | "image";
  title: string;
  body: string;
  image: string | null;
  alt_text: string;
  domains: number[];
  favourite: boolean;
  order: number;
  is_active: boolean;
  updated_at: string;
};

export type BlockInput = {
  kind: "text" | "image";
  title: string;
  body: string;
  alt_text: string;
  domains: number[];
  favourite: boolean;
  order: number;
  is_active: boolean;
};

export type LinkInput = {
  slug: string;
  title: string;
  intro: string;
  content: { featured: string[]; domains: string[]; hide_explore: boolean };
};

/* blocks */

export function usePortfolioBlocks() {
  return useQuery({
    queryKey: ["portfolio", "blocks"],
    queryFn: () => api<PortfolioBlockRow[]>(BLOCKS_URL),
  });
}

function blockMultipart(input: BlockInput, image: File): FormData {
  const fd = new FormData();
  fd.set("kind", input.kind);
  fd.set("title", input.title);
  fd.set("body", input.body);
  fd.set("alt_text", input.alt_text);
  fd.set("favourite", String(input.favourite));
  fd.set("order", String(input.order));
  fd.set("is_active", String(input.is_active));
  for (const d of input.domains) fd.append("domains", String(d));
  fd.set("image", image);
  return fd;
}

/** Create or update a block. With a new `image` File it goes multipart (required to
 *  create an image block — the serializer rejects kind=image without a file); otherwise
 *  plain JSON, which keeps domain edits (incl. clearing) clean. Caveat: clearing all
 *  domains *in the same save as an image swap* isn't expressible over multipart — do the
 *  tag change and the image change in separate saves if you hit it. */
export function useSaveBlock() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      input,
      image,
    }: {
      id?: number;
      input: BlockInput;
      image?: File | null;
    }) => {
      const url = id ? `${BLOCKS_URL}${id}/` : BLOCKS_URL;
      const method = id ? "PATCH" : "POST";
      const body = image ? blockMultipart(input, image) : JSON.stringify(input);
      return api<PortfolioBlockRow>(url, { method, body });
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["portfolio", "blocks"] }),
  });
}

export function useDeleteBlock() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<void>(`${BLOCKS_URL}${id}/`, { method: "DELETE" }),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["portfolio", "blocks"] }),
  });
}

/* links */

export function usePortfolioLinks() {
  return useQuery({
    queryKey: ["portfolio", "links"],
    queryFn: () => api<PortfolioLinkRow[]>(LINKS_URL),
  });
}

export function useCreateLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: LinkInput) =>
      api<PortfolioLinkRow>(LINKS_URL, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio", "links"] }),
  });
}

export function useUpdateLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: number; input: Partial<LinkInput> }) =>
      api<PortfolioLinkRow>(`${LINKS_URL}${id}/`, {
        method: "PATCH",
        body: JSON.stringify(input),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio", "links"] }),
  });
}

export function useDeleteLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<void>(`${LINKS_URL}${id}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio", "links"] }),
  });
}

/** Soft-kill (guide 4's `revokePortfolioLink`) with list invalidation. */
export function useRevokeLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => revokePortfolioLink(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolio", "links"] }),
  });
}
```

### 4. `frontend/src/components/portfolio/featured-picker.tsx`

```tsx
import { useMemo } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { useCvEntries } from "@/lib/queries/jac";
import { usePortfolioBlocks } from "@/lib/queries/portfolio";
import {
  candidates,
  moveFeatured,
  resolveFeatured,
  toggleFeatured,
  type FeaturedCandidate,
} from "@/lib/portfolio/link-form";

/** Two panes: the ordered featured list (reorder / remove) on the left, the full pool
 *  of career entries + blocks (toggle in/out) on the right. Pure ops from link-form.ts;
 *  this component only maps them to clicks. */
export function FeaturedPicker({
  featured,
  onChange,
}: {
  featured: string[];
  onChange: (next: string[]) => void;
}) {
  const db = useCvEntries();
  const blocks = usePortfolioBlocks();
  const pool = useMemo(
    () => candidates(db.data, blocks.data ?? []),
    [db.data, blocks.data],
  );
  const chosen = resolveFeatured(featured, pool);
  const chosenIds = new Set(featured);

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="space-y-2">
        <p className="text-sm font-medium">Featured ({chosen.length})</p>
        {chosen.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing featured yet — pick from the right. Empty = the public page
            falls back to favourites + blocks.
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
                  onClick={() => onChange(moveFeatured(featured, i, -1))}
                >
                  ↑
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={i === chosen.length - 1}
                  onClick={() => onChange(moveFeatured(featured, i, 1))}
                >
                  ↓
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onChange(toggleFeatured(featured, c.id))}
                >
                  ✕
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-1 max-h-80 overflow-y-auto pr-1">
        <p className="text-sm font-medium">All content</p>
        {pool.map((c: FeaturedCandidate) => (
          <label
            key={c.id}
            className="flex items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-muted"
          >
            <Checkbox
              checked={chosenIds.has(c.id)}
              onCheckedChange={() => onChange(toggleFeatured(featured, c.id))}
            />
            <span className="flex-1 truncate">{c.label}</span>
            <Badge variant="outline">{c.type}</Badge>
          </label>
        ))}
        {pool.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No career entries or blocks yet.
          </p>
        ) : null}
      </div>
    </div>
  );
}
```

### 5. `frontend/src/components/portfolio/link-editor.tsx`

```tsx
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { FeaturedPicker } from "@/components/portfolio/featured-picker";
import { useFullList, type DomainRow } from "@/lib/queries/jac";
import {
  useCreateLink,
  useUpdateLink,
  type PortfolioLinkRow,
} from "@/lib/queries/portfolio";
import { toggleName } from "@/lib/portfolio/link-form";

const RESERVED = new Set(["links", "blocks"]); // the /portfolio tab paths

type Draft = {
  slug: string;
  title: string;
  intro: string;
  featured: string[];
  domains: string[];
  hide_explore: boolean;
};

function draftFrom(link: PortfolioLinkRow | null): Draft {
  return {
    slug: link?.slug ?? "",
    title: link?.title ?? "",
    intro: link?.intro ?? "",
    featured: link?.content.featured ?? [],
    domains: link?.content.domains ?? [],
    hide_explore: link?.content.hide_explore ?? false,
  };
}

/** Create (link=null) or edit a manual link. Application links are editable too — the
 *  slug field is read-only for them (server-owned). */
export function LinkEditor({
  open,
  link,
  onOpenChange,
}: {
  open: boolean;
  link: PortfolioLinkRow | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftFrom(link));
  const domains = useFullList<DomainRow>("domains");
  const create = useCreateLink();
  const update = useUpdateLink();
  const isApplication = link?.kind === "application";
  const busy = create.isPending || update.isPending;

  function set<K extends keyof Draft>(k: K, v: Draft[K]) {
    setDraft((d) => ({ ...d, [k]: v }));
  }

  function submit() {
    const slug = draft.slug.trim();
    if (!isApplication && RESERVED.has(slug)) {
      toast.error(`"${slug}" is reserved — pick another slug.`);
      return;
    }
    const content = {
      featured: draft.featured,
      domains: draft.domains,
      hide_explore: draft.hide_explore,
    };
    const onSuccess = () => {
      toast.success(link ? "Link updated" : "Link created");
      onOpenChange(false);
    };
    const onError = () => toast.error("Couldn't save the link");
    if (link) {
      // A frozen application link keeps its slug; only send editable fields.
      const input = isApplication
        ? { title: draft.title, intro: draft.intro, content }
        : { slug, title: draft.title, intro: draft.intro, content };
      update.mutate({ id: link.id, input }, { onSuccess, onError });
    } else {
      create.mutate(
        { slug, title: draft.title, intro: draft.intro, content },
        { onSuccess, onError },
      );
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{link ? "Edit link" : "New portfolio link"}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid gap-2">
            <Label htmlFor="slug">Slug</Label>
            <Input
              id="slug"
              value={draft.slug}
              disabled={isApplication}
              placeholder="for-jane"
              onChange={(e) => set("slug", e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Public at /portfolio/{draft.slug || "…"}
            </p>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="title">Title</Label>
            <Input
              id="title"
              value={draft.title}
              onChange={(e) => set("title", e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="intro">Intro</Label>
            <Textarea
              id="intro"
              value={draft.intro}
              onChange={(e) => set("intro", e.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label>Featured content</Label>
            <FeaturedPicker
              featured={draft.featured}
              onChange={(f) => set("featured", f)}
            />
          </div>

          <div className="grid gap-2">
            <Label>Explore domains (deepen the "more" section)</Label>
            <div className="flex flex-wrap gap-2">
              {(domains.data ?? []).map((d) => (
                <label
                  key={d.id}
                  className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-sm"
                >
                  <Checkbox
                    checked={draft.domains.includes(d.name)}
                    onCheckedChange={() =>
                      set("domains", toggleName(draft.domains, d.name))
                    }
                  />
                  {d.name}
                </label>
              ))}
            </div>
          </div>

          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={draft.hide_explore}
              onCheckedChange={(v) => set("hide_explore", v === true)}
            />
            Hide the "more to explore" section (featured only)
          </label>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={busy} onClick={submit}>
            {link ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

### 6. `frontend/src/components/portfolio/block-editor.tsx`

```tsx
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useFullList, type DomainRow } from "@/lib/queries/jac";
import {
  useSaveBlock,
  type BlockInput,
  type PortfolioBlockRow,
} from "@/lib/queries/portfolio";

type Draft = BlockInput;

function draftFrom(block: PortfolioBlockRow | null): Draft {
  return {
    kind: block?.kind ?? "text",
    title: block?.title ?? "",
    body: block?.body ?? "",
    alt_text: block?.alt_text ?? "",
    domains: block?.domains ?? [],
    favourite: block?.favourite ?? false,
    order: block?.order ?? 0,
    is_active: block?.is_active ?? true,
  };
}

export function BlockEditor({
  open,
  block,
  onOpenChange,
}: {
  open: boolean;
  block: PortfolioBlockRow | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftFrom(block));
  const [image, setImage] = useState<File | null>(null);
  const domains = useFullList<DomainRow>("domains");
  const save = useSaveBlock();

  function set<K extends keyof Draft>(k: K, v: Draft[K]) {
    setDraft((d) => ({ ...d, [k]: v }));
  }

  function toggleDomain(id: number) {
    set(
      "domains",
      draft.domains.includes(id)
        ? draft.domains.filter((d) => d !== id)
        : [...draft.domains, id],
    );
  }

  function submit() {
    if (draft.kind === "image" && !image && !block?.image) {
      toast.error("An image block needs an image.");
      return;
    }
    if (draft.kind === "text" && !draft.body.trim()) {
      toast.error("A text block needs a body.");
      return;
    }
    save.mutate(
      { id: block?.id, input: draft, image },
      {
        onSuccess: () => {
          toast.success(block ? "Block saved" : "Block created");
          onOpenChange(false);
        },
        onError: () => toast.error("Couldn't save the block"),
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{block ? "Edit block" : "New block"}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid gap-2">
            <Label>Kind</Label>
            <Select
              value={draft.kind}
              onValueChange={(v) => set("kind", v as Draft["kind"])}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="text">Text</SelectItem>
                <SelectItem value="image">Image</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="b-title">Title</Label>
            <Input
              id="b-title"
              value={draft.title}
              onChange={(e) => set("title", e.target.value)}
            />
          </div>

          {draft.kind === "text" ? (
            <div className="grid gap-2">
              <Label htmlFor="b-body">Body (markdown)</Label>
              <Textarea
                id="b-body"
                rows={6}
                value={draft.body}
                onChange={(e) => set("body", e.target.value)}
              />
            </div>
          ) : (
            <>
              <div className="grid gap-2">
                <Label htmlFor="b-image">Image</Label>
                <input
                  id="b-image"
                  type="file"
                  accept="image/*"
                  onChange={(e) => setImage(e.target.files?.[0] ?? null)}
                />
                {block?.image && !image ? (
                  <img
                    src={block.image}
                    alt={block.alt_text}
                    className="max-h-40 rounded-md object-cover"
                  />
                ) : null}
              </div>
              <div className="grid gap-2">
                <Label htmlFor="b-alt">Alt text</Label>
                <Input
                  id="b-alt"
                  value={draft.alt_text}
                  onChange={(e) => set("alt_text", e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="b-caption">Caption</Label>
                <Textarea
                  id="b-caption"
                  rows={2}
                  value={draft.body}
                  onChange={(e) => set("body", e.target.value)}
                />
              </div>
            </>
          )}

          <div className="grid gap-2">
            <Label>Domains</Label>
            <div className="flex flex-wrap gap-2">
              {(domains.data ?? []).map((d) => (
                <label
                  key={d.id}
                  className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-sm"
                >
                  <Checkbox
                    checked={draft.domains.includes(d.id)}
                    onCheckedChange={() => toggleDomain(d.id)}
                  />
                  {d.name}
                </label>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-6">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={draft.favourite}
                onCheckedChange={(v) => set("favourite", v === true)}
              />
              Featured by default
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={draft.is_active}
                onCheckedChange={(v) => set("is_active", v === true)}
              />
              Active
            </label>
            <div className="flex items-center gap-2 text-sm">
              <Label htmlFor="b-order">Order</Label>
              <Input
                id="b-order"
                type="number"
                className="w-20"
                value={draft.order}
                onChange={(e) => set("order", Number(e.target.value) || 0)}
              />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={save.isPending} onClick={submit}>
            {block ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

### 7. `frontend/src/routes/_authenticated/portfolio.tsx` — tab layout

```tsx
import { createFileRoute, Link, Outlet } from "@tanstack/react-router";

const TABS = [
  { to: "/portfolio/links", label: "Links" },
  { to: "/portfolio/blocks", label: "Blocks" },
] as const;

export const Route = createFileRoute("/_authenticated/portfolio")({
  component: PortfolioLayout,
});

function PortfolioLayout() {
  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Portfolio</h1>
        <p className="text-sm text-muted-foreground">
          The personalised pages recruiters and visitors see.
        </p>
      </div>
      <nav className="flex gap-2 border-b">
        {TABS.map((t) => (
          <Link
            key={t.to}
            to={t.to}
            className="px-3 py-2 text-sm -mb-px border-b-2 border-transparent hover:border-muted-foreground/40"
            activeProps={{ className: "border-primary font-medium" }}
          >
            {t.label}
          </Link>
        ))}
      </nav>
      <Outlet />
    </div>
  );
}
```

### 8. `frontend/src/routes/_authenticated/portfolio/index.tsx` — default tab

```tsx
import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/_authenticated/portfolio/")({
  beforeLoad: () => {
    throw redirect({ to: "/portfolio/links" });
  },
});
```

### 9. `frontend/src/routes/_authenticated/portfolio/links.tsx`

```tsx
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LinkEditor } from "@/components/portfolio/link-editor";
import {
  usePortfolioLinks,
  useDeleteLink,
  useRevokeLink,
  type PortfolioLinkRow,
} from "@/lib/queries/portfolio";

export const Route = createFileRoute("/_authenticated/portfolio/links")({
  component: LinksTab,
});

function LinksTab() {
  const links = usePortfolioLinks();
  const revoke = useRevokeLink();
  const del = useDeleteLink();
  const [editing, setEditing] = useState<PortfolioLinkRow | null>(null);
  const [open, setOpen] = useState(false);

  function openNew() {
    setEditing(null);
    setOpen(true);
  }
  function openEdit(link: PortfolioLinkRow) {
    setEditing(link);
    setOpen(true);
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button onClick={openNew}>New manual link</Button>
      </div>

      {links.isPending ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (links.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No links yet. Create a manual link, or add one from an application's
          export card.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Slug</TableHead>
              <TableHead>Kind</TableHead>
              <TableHead>Visits</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(links.data ?? []).map((link) => (
              <TableRow
                key={link.id}
                className={link.revoked_at ? "opacity-50" : ""}
              >
                <TableCell className="font-mono">
                  <a
                    href={link.url}
                    target="_blank"
                    rel="noreferrer"
                    className="hover:underline"
                  >
                    /{link.slug}
                  </a>
                </TableCell>
                <TableCell>
                  <Badge variant="secondary">{link.kind}</Badge>
                </TableCell>
                <TableCell>{link.visits}</TableCell>
                <TableCell>
                  {link.revoked_at ? (
                    <span className="text-destructive">revoked</span>
                  ) : (
                    "active"
                  )}
                </TableCell>
                <TableCell className="text-right space-x-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      navigator.clipboard.writeText(link.url);
                      toast.success("Link copied");
                    }}
                  >
                    Copy
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => openEdit(link)}
                  >
                    Edit
                  </Button>
                  {link.revoked_at ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive"
                      onClick={() => del.mutate(link.id)}
                    >
                      Delete
                    </Button>
                  ) : (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive"
                      onClick={() => revoke.mutate(link.id)}
                    >
                      Revoke
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {open ? (
        <LinkEditor open={open} link={editing} onOpenChange={setOpen} />
      ) : null}
    </div>
  );
}
```

Subtle: `{open ? <LinkEditor …/> : null}` remounts the dialog per open, so the editor's
`useState(() => draftFrom(link))` seeds from the row being edited (a dialog kept always-mounted
would keep stale draft state between edits).

### 10. `frontend/src/routes/_authenticated/portfolio/blocks.tsx`

```tsx
import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { BlockEditor } from "@/components/portfolio/block-editor";
import {
  usePortfolioBlocks,
  useDeleteBlock,
  type PortfolioBlockRow,
} from "@/lib/queries/portfolio";

export const Route = createFileRoute("/_authenticated/portfolio/blocks")({
  component: BlocksTab,
});

function BlocksTab() {
  const blocks = usePortfolioBlocks();
  const del = useDeleteBlock();
  const [editing, setEditing] = useState<PortfolioBlockRow | null>(null);
  const [open, setOpen] = useState(false);

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button
          onClick={() => {
            setEditing(null);
            setOpen(true);
          }}
        >
          New block
        </Button>
      </div>

      {blocks.isPending ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (blocks.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No blocks yet — add text groups or images the career DB can't hold.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {(blocks.data ?? []).map((b) => (
            <Card key={b.id}>
              <CardContent className="p-4 space-y-2">
                <div className="flex items-center gap-2">
                  <Badge variant="secondary">{b.kind}</Badge>
                  {b.favourite ? <Badge>featured</Badge> : null}
                  {!b.is_active ? (
                    <Badge variant="outline">hidden</Badge>
                  ) : null}
                  <span className="font-medium truncate">
                    {b.title || `${b.kind} block`}
                  </span>
                </div>
                {b.kind === "image" && b.image ? (
                  <img
                    src={b.image}
                    alt={b.alt_text}
                    className="max-h-32 rounded-md object-cover"
                  />
                ) : (
                  <p className="text-sm text-muted-foreground line-clamp-3">
                    {b.body}
                  </p>
                )}
                <div className="flex justify-end gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setEditing(b);
                      setOpen(true);
                    }}
                  >
                    Edit
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-destructive"
                    onClick={() => del.mutate(b.id)}
                  >
                    Delete
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {open ? (
        <BlockEditor open={open} block={editing} onOpenChange={setOpen} />
      ) : null}
    </div>
  );
}
```

### 11. `frontend/src/routes/_authenticated.tsx` — nav link

In the `<nav>` block (L58-73), add a Portfolio link beside CV / Applications:

```tsx
<Link
  to="/portfolio/links"
  className="hover:underline"
  activeProps={{ className: "font-medium underline" }}
>
  Portfolio
</Link>
```

(`to="/portfolio/links"` rather than `/portfolio` so the click lands on a real tab, not the
redirect; the layout's own tab nav highlights the active one.)

## Tests

Landed **red at activation** (step 0), per the approved plan. Pure-lib only — the featured
picker's list algebra is the whole testable surface; the dialogs/routes are click-through-verified
(the `frontend/tests/` regime defers components until jsdom lands, `frontend-test-layout` memory):

- `frontend/tests/lib/portfolio-link-form.test.ts` — **new**:
  - `candidates`: builds career ids in `SECTION_ORDER` with cv-doc labels + block ids
    (`block:<pk>`, title-or-fallback label); `undefined` db → blocks only; empty everything → `[]`.
  - `resolveFeatured`: returns candidates in _featured_ order (not pool order); ids absent from
    the pool (deleted row / block) drop silently; empty featured → `[]`.
  - `toggleFeatured`: adds an absent id to the tail, removes a present one, leaves others' order.
  - `moveFeatured`: swaps with the neighbour; no-ops at either boundary and on out-of-range index;
    returns a new array (immutability).
  - `pruneFeatured`: drops ghosts, preserves the surviving order.
  - `toggleName`: add/remove a domain name.

Run: `cd frontend && npx vitest run tests/lib/portfolio-link-form.test.ts`

The test file imports `PortfolioBlockRow` from `@/lib/queries/portfolio` and `CvEntriesResponse`
from `@/lib/queries/jac` — build minimal fixtures (a couple of `jobs`/`skills` rows + two blocks);
no network, no React.

## Verification

1. Backend up with guides 1–4 merged; `npm run dev`, logged in.
2. Header shows **Portfolio**; clicking it lands on `/portfolio/links` with the two tabs.
3. **Blocks tab** → New block → a text block (body required) and an image block (image required,
   preview shows), tag each with a Domain, mark one favourite → both list; edit one (swap the
   image, change tags) → persists; delete → gone.
4. **Links tab** → New manual link → slug `for-jane`, pick 3 featured items across sections +
   a block, reorder them, tick two explore domains → Create. Row shows slug/visits(0)/active.
   Copy → open in a private window → the featured items render in your chosen order (guide 3's
   public page); the "more" section reflects the explore domains. Try slug `links` → rejected.
5. Edit the link → remove a featured item, toggle "hide explore" → public page updates.
6. Revoke → public 404 (+ stamp self-clears per guide 3); the row shows _revoked_ and offers
   Delete; Delete removes it.
7. Open an application's export card → Add portfolio link (guide 4) → it appears in this list as
   an `application` link; edit it here → slug field read-only, title/intro/featured editable.
8. `npx vitest run tests/lib/portfolio-link-form.test.ts` green; `npx tsc -b` clean.

## Results
