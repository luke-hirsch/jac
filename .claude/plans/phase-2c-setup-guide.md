# Phase 2c setup guide — JAC CRUD UI (six career sections)

Goal: turn the Django career DB at `/api/jac/` into a working web UI. By the end of this phase you can log in, see a dashboard of your CV at `/cv`, drill into any section (`/cv/jobs`, `/cv/skills`, …), filter/search, create/edit/delete entries through a drawer editor, attach domains + locations inline (with "create new" affordance), preview Markdown descriptions live, and multi-select rows for bulk delete + bulk domain reassign. Auth + account live under `/account/*` (Phase 2b); Phase 2c never touches `/_allauth`.

This is **Phase 2c only**. Phase 2d (LLM connector UI) follows once the CRUD surface is solid.

Run every command from `frontend/` unless stated otherwise. Backend on `http://localhost:8000`, frontend on `http://localhost:5173`. If a step's "verify" check fails, stop and fix before moving on.

---

## 0. Preflight

Phase 2b must be committed (`60e1754`). Confirm:

```bash
cd frontend
ls src/routes/_authenticated/account/profile.tsx src/lib/auth.ts src/lib/api.ts src/lib/form.ts
# all four should exist
npx tsc -b
# expect zero output
```

Backend up + suite green:

```bash
cd ../backend && python manage.py test && cd ../frontend
# expect "Ran 163 tests ... OK"
```

Make sure `/api/jac/` answers (you should see a session cookie if you logged in via the SPA at least once):

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/api/jac/jobs/
# 401 when anonymous; 200 with a session cookie. 502 = backend/proxy down.
```

If you have no career entries yet, populate two or three via `python manage.py cv_import` or the Django admin first — empty tables make the UI work but tell you nothing about whether the wiring is right.

---

## 1. The contract you're coding against

Every JAC list endpoint is paginated + filterable + searchable thanks to Phase 1's DRF defaults. Every response from `GET /api/jac/<resource>/` is:

```ts
{
  count: number,
  next: string | null,    // absolute URL, follow as-is
  previous: string | null,
  results: T[]
}
```

Detail endpoints (`/api/jac/<resource>/<id>/`) return the bare object. Writes (`POST`, `PUT`, `PATCH`, `DELETE`) follow the usual DRF contract; validation errors come back as a flat `{ field: ["message", …] }` map.

The eight viewsets exposed at `/api/jac/`:

| Resource         | Search fields                                  | Filter fields                       | Default order      |
| ---------------- | ---------------------------------------------- | ----------------------------------- | ------------------ |
| `domains`        | `name`                                         | —                                   | `name`             |
| `locations`      | `city`                                         | `country`                           | `city`             |
| `education`      | `institution`, `degree`, `field_of_study`      | —                                   | `-started`         |
| `certifications` | `name`, `issuer`                               | —                                   | `-issued_on`       |
| `skills`         | `name`                                         | `category`, `proficiency`, `domains`| `name`             |
| `jobs`           | `title`, `company`                             | `domains`, `job_type`               | `-started`         |
| `projects`       | `name`                                         | `domains`                           | `-started`, `name` |
| `languages`      | `name`                                         | `fluency`                           | `name`             |

Listing supports `?search=`, `?ordering=`, `?page=`, plus the `filterset_fields` columns as `?field=value`. DRF page size is 50; a personal CV won't paginate, but the count + next/prev shape still applies.

Three serializer quirks to internalise before writing forms:

1. **`user` is a hidden field** on every serializer. You never send it. The viewset injects `request.user` server-side. Just leave `user` out of your zod schema.
2. **`Domain` queryset is special.** `SkillSerializer` and `JobSerializer` and `ProjectSerializer` accept any `Domain` row owned by the user OR the system default user. Listing `/api/jac/domains/` returns both. Writes only succeed if the domain exists in that union.
3. **`Skill.years_of_experience` is read-only.** It's derived from `first_used` + earliest related job/project. Show it; never POST it. If a freshly-created Skill comes back without it, that's expected — the manager annotation only fires on subsequent queries.

Spec mirror: <http://localhost:8000/api/docs/> (Swagger off `drf-spectacular`). Open it in another tab — you'll consult it more than this guide.

---

## 2. Stack additions

We have what 2a/2b shipped — TanStack Router + Query + Form + Table, Tailwind v4, shadcn primitives (`button`, `card`, `dialog`, `dropdown-menu`, `input`, `label`, `select`, `separator`, `sonner`, `table`, `textarea`). Phase 2c needs four more shadcn pieces and two markdown libraries:

```bash
npx shadcn@latest add badge checkbox command popover sheet
npm install react-markdown remark-gfm
```

Why each:

- **`sheet`** — side drawer for the editor. Stays out of the way while you scan the table behind it; closes on outside click.
- **`command`** + **`popover`** — the combobox primitive Radix doesn't ship. `Domain` + `Location` pickers are searchable selects with a "create new" footer row.
- **`checkbox`** — row selection in the table.
- **`badge`** — small inline pills for domains, job_type, fluency, etc.
- **`react-markdown` + `remark-gfm`** — render the `description` field's Markdown live as the user types. GFM gets us tables + task lists + autolinks.

Sanity build:

```bash
npx tsc -b
# expect zero output
```

If `shadcn` asks about the import alias, accept the existing `@/components/ui/` convention.

---

## 3. Shared infra

Five small files do most of the heavy lifting. Write them before any page.

### 3.1. Paginated query helper

`src/lib/queries/paginated.ts`:

```ts
import { api } from "@/lib/api";

export type Page<T> = {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
};

export type ListParams = {
  search?: string;
  ordering?: string;
  page?: number;
  filters?: Record<string, string | number | undefined>;
};

export function buildQuery(params: ListParams): string {
  const u = new URLSearchParams();
  if (params.search) u.set("search", params.search);
  if (params.ordering) u.set("ordering", params.ordering);
  if (params.page && params.page > 1) u.set("page", String(params.page));
  for (const [k, v] of Object.entries(params.filters ?? {})) {
    if (v !== undefined && v !== "") u.set(k, String(v));
  }
  const q = u.toString();
  return q ? `?${q}` : "";
}

export function fetchPage<T>(url: string, params: ListParams = {}) {
  return api<Page<T>>(`${url}${buildQuery(params)}`);
}
```

### 3.2. One file per resource

`src/lib/queries/jac.ts`. Each resource gets a typed `Row`, a list-query factory, and CRUD mutation factories. Keep cache keys consistent — `["jac", resource, "list", params]` and `["jac", resource, "detail", id]`.

```ts
import {
  useQuery,
  useMutation,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { api } from "@/lib/api";
import { fetchPage, type ListParams, type Page } from "./paginated";

/* ---------- shared row types ---------- */

export type DomainRow = {
  id: number;
  name: string;
  description: string;
};

export type LocationRow = {
  id: number;
  city: string;
  country: string | null;
  street: string | null;
  zip: string | null;
  longitude: number | null;
  latitude: number | null;
};

export type EducationRow = {
  id: number;
  institution: string;
  field_of_study: string;
  started: string;
  ended: string | null;
  degree: string | null;
  grade: string | null;
  description: string;
  location: number | null;
};

export type CertificationRow = {
  id: number;
  name: string;
  issuer: string;
  issued_on: string | null;
  expires_on: string | null;
  credential_id: string;
  url: string;
  description: string;
};

export type SkillRow = {
  id: number;
  name: string;
  proficiency: "beginner" | "intermediate" | "advanced" | "expert";
  category: "technical" | "soft" | "domain" | "other";
  domains: number[];
  first_used: string | null;
  certification: number | null;
  years_of_experience: number | null;
  description: string;
};

export type JobRow = {
  id: number;
  title: string;
  company: string;
  location: number | null;
  job_type: "ft" | "pt" | "ct" | "fl" | "in" | "vl";
  skills: number[];
  domains: number[];
  started: string;
  ended: string | null;
  url: string;
  description: string;
};

export type ProjectRow = {
  id: number;
  name: string;
  skills: number[];
  domains: number[];
  location: number | null;
  started: string | null;
  ended: string | null;
  url: string;
  description: string;
};

export type LanguageRow = {
  id: number;
  name: string;
  fluency: "native" | "fluent" | "professional" | "conversational" | "basic";
  description: string;
};

/* ---------- factory ---------- */

type Resource = {
  key: string;
  url: string;
};

const R = {
  domains:        { key: "domains",        url: "/api/jac/domains/" },
  locations:      { key: "locations",      url: "/api/jac/locations/" },
  education:      { key: "education",      url: "/api/jac/education/" },
  certifications: { key: "certifications", url: "/api/jac/certifications/" },
  skills:         { key: "skills",         url: "/api/jac/skills/" },
  jobs:           { key: "jobs",           url: "/api/jac/jobs/" },
  projects:       { key: "projects",       url: "/api/jac/projects/" },
  languages:      { key: "languages",      url: "/api/jac/languages/" },
} as const satisfies Record<string, Resource>;

export type ResourceKey = keyof typeof R;

function listKey(key: ResourceKey, params: ListParams) {
  return ["jac", key, "list", params] as const;
}
function detailKey(key: ResourceKey, id: number) {
  return ["jac", key, "detail", id] as const;
}

export function useList<T>(key: ResourceKey, params: ListParams = {}) {
  return useQuery({
    queryKey: listKey(key, params),
    queryFn: () => fetchPage<T>(R[key].url, params),
    placeholderData: (prev) => prev,
  });
}

export function useDetail<T>(key: ResourceKey, id: number | undefined) {
  return useQuery({
    queryKey: id ? detailKey(key, id) : ["jac", key, "detail", "none"],
    queryFn: () => api<T>(`${R[key].url}${id}/`),
    enabled: id !== undefined,
  });
}

function invalidateResource(qc: QueryClient, key: ResourceKey) {
  return qc.invalidateQueries({ queryKey: ["jac", key] });
}

export function useCreate<T, B = Partial<T>>(key: ResourceKey) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: B) =>
      api<T>(R[key].url, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => invalidateResource(qc, key),
  });
}

export function useUpdate<T, B = Partial<T>>(key: ResourceKey) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: B }) =>
      api<T>(`${R[key].url}${id}/`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: detailKey(key, vars.id) });
      invalidateResource(qc, key);
    },
  });
}

export function useDestroy(key: ResourceKey) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) =>
      api<void>(`${R[key].url}${id}/`, { method: "DELETE" }),
    onSuccess: () => invalidateResource(qc, key),
  });
}

/* Bulk delete: fan out client-side, then invalidate once. The DRF viewsets
 * don't expose a bulk endpoint and we don't need one yet — bulk delete is the
 * only multi-row write in 2c, and N never exceeds a personal CV's row count.
 */
export function useBulkDestroy(key: ResourceKey) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (ids: number[]) => {
      await Promise.all(
        ids.map((id) =>
          api<void>(`${R[key].url}${id}/`, { method: "DELETE" }),
        ),
      );
    },
    onSuccess: () => invalidateResource(qc, key),
  });
}

/* Bulk domain assign: PATCH each row with the new domain set. Server will
 * replace the M2M wholesale. We compute the next set client-side per row
 * (existing ∪ added \ removed) by reading the cache.
 */
export function useBulkPatchDomains(key: Extract<ResourceKey, "skills" | "jobs" | "projects">) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      ids,
      add,
      remove,
    }: {
      ids: number[];
      add: number[];
      remove: number[];
    }) => {
      await Promise.all(
        ids.map(async (id) => {
          const row = await api<{ domains: number[] }>(`${R[key].url}${id}/`);
          const next = new Set(row.domains);
          for (const d of add) next.add(d);
          for (const d of remove) next.delete(d);
          await api(`${R[key].url}${id}/`, {
            method: "PATCH",
            body: JSON.stringify({ domains: [...next] }),
          });
        }),
      );
    },
    onSuccess: () => invalidateResource(qc, key),
  });
}
```

`placeholderData: (prev) => prev` is the small but important touch that keeps the table from blanking out while you debounce the search input.

### 3.3. Debounced search hook

`src/lib/use-debounced.ts`:

```ts
import { useEffect, useState } from "react";

export function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}
```

### 3.4. Markdown preview

`src/components/markdown-preview.tsx`:

```tsx
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function MarkdownPreview({ source }: { source: string }) {
  if (!source.trim()) {
    return (
      <p className="text-xs text-muted-foreground italic">
        Markdown preview appears here.
      </p>
    );
  }
  return (
    <div className="prose prose-sm max-w-none dark:prose-invert">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{source}</ReactMarkdown>
    </div>
  );
}
```

The `prose` classes need Tailwind's typography plugin. Add it to `package.json` and `src/index.css`:

```bash
npm install -D @tailwindcss/typography
```

`src/index.css` already has Tailwind v4 set up — add the plugin import next to the existing ones:

```css
@plugin "@tailwindcss/typography";
```

(Tailwind v4 reads plugins via `@plugin` in CSS rather than the v3 `tailwind.config.js` array. If the file already has plugin lines, just append.)

### 3.5. Section page chrome

Every section page is the same shape: header with title + "New" button, a debounced search input + filter dropdowns, a TanStack Table, a side `Sheet` for the editor. Pull the chrome into one component so each section is just its column config + form.

`src/components/cv/section-page.tsx`:

```tsx
import { ReactNode, useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

export function SectionPage<EditorRow>({
  title,
  description,
  search,
  onSearchChange,
  filters,
  table,
  editor,
  open,
  editing,
  onOpenChange,
  onNew,
}: {
  title: string;
  description: string;
  search: string;
  onSearchChange: (v: string) => void;
  filters?: ReactNode;
  table: ReactNode;
  editor: (row: EditorRow | null, close: () => void) => ReactNode;
  open: boolean;
  editing: EditorRow | null;
  onOpenChange: (open: boolean) => void;
  onNew: () => void;
}) {
  return (
    <div className="space-y-4">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{title}</h1>
          <p className="text-sm text-muted-foreground">{description}</p>
        </div>
        <Button onClick={onNew}>
          <Plus className="size-4" /> New
        </Button>
      </header>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          placeholder="Search…"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          className="max-w-xs"
        />
        {filters}
      </div>
      {table}
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent className="w-full sm:max-w-2xl overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{editing ? "Edit" : "New"} {title.toLowerCase()}</SheetTitle>
          </SheetHeader>
          <div className="mt-4">{editor(editing, () => onOpenChange(false))}</div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
```

Pages compose this. Tables, filters, and forms stay per-section because the column sets and editors are genuinely different.

### 3.6. CV layout

A pathless layout under `_authenticated` gives every CV route the same top tabs. `src/routes/_authenticated/cv.tsx`:

```tsx
import { createFileRoute, Link, Outlet } from "@tanstack/react-router";

const TABS = [
  { to: "/cv",                label: "Overview"       },
  { to: "/cv/jobs",           label: "Jobs"           },
  { to: "/cv/education",      label: "Education"      },
  { to: "/cv/skills",         label: "Skills"         },
  { to: "/cv/certifications", label: "Certifications" },
  { to: "/cv/projects",       label: "Projects"       },
  { to: "/cv/languages",      label: "Languages"      },
] as const;

export const Route = createFileRoute("/_authenticated/cv")({
  component: CvLayout,
});

function CvLayout() {
  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      <nav className="flex gap-1 border-b">
        {TABS.map((t) => (
          <Link
            key={t.to}
            to={t.to}
            className="px-3 py-2 text-sm rounded-t-md hover:bg-muted"
            activeProps={{ className: "bg-muted font-medium border-b-2 border-primary" }}
            activeOptions={{ exact: t.to === "/cv" }}
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

`activeOptions={{ exact: true }}` on the Overview tab matters — otherwise it stays highlighted on every nested route.

---

## 4. Domain + Location pickers (with "create new")

Domains and locations show up on six of the eight resources. Build the picker once.

`src/components/cv/domain-picker.tsx`:

```tsx
import { useState } from "react";
import { Check, Plus, X } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useList, type DomainRow } from "@/lib/queries/jac";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

export function DomainPicker({
  value,
  onChange,
}: {
  value: number[];
  onChange: (next: number[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const qc = useQueryClient();
  const list = useList<DomainRow>("domains", { search });
  const rows = list.data?.results ?? [];

  const create = useMutation({
    mutationFn: (name: string) =>
      api<DomainRow>("/api/jac/domains/", {
        method: "POST",
        body: JSON.stringify({ name, description: "" }),
      }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["jac", "domains"] });
      onChange([...value, created.id]);
      setSearch("");
    },
  });

  const selected = rows.filter((r) => value.includes(r.id));
  const exactMatch = rows.some(
    (r) => r.name.toLowerCase() === search.trim().toLowerCase(),
  );

  function toggle(id: number) {
    onChange(value.includes(id) ? value.filter((v) => v !== id) : [...value, id]);
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1">
        {selected.map((d) => (
          <Badge key={d.id} variant="secondary" className="gap-1">
            {d.name}
            <button
              type="button"
              onClick={() => toggle(d.id)}
              className="hover:text-destructive"
            >
              <X className="size-3" />
            </button>
          </Badge>
        ))}
      </div>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button type="button" variant="outline" size="sm">
            <Plus className="size-4" /> Add domain
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-72 p-0">
          <Command shouldFilter={false}>
            <CommandInput
              placeholder="Search domains…"
              value={search}
              onValueChange={setSearch}
            />
            <CommandList>
              <CommandEmpty>No matches.</CommandEmpty>
              <CommandGroup>
                {rows.map((d) => (
                  <CommandItem key={d.id} onSelect={() => toggle(d.id)}>
                    <Check
                      className={
                        "size-4 mr-2 " +
                        (value.includes(d.id) ? "opacity-100" : "opacity-0")
                      }
                    />
                    {d.name}
                  </CommandItem>
                ))}
                {search.trim() && !exactMatch && (
                  <CommandItem
                    onSelect={() => create.mutate(search.trim())}
                    disabled={create.isPending}
                  >
                    <Plus className="size-4 mr-2" />
                    Create "{search.trim()}"
                  </CommandItem>
                )}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  );
}
```

Two non-obvious choices:

- `shouldFilter={false}` — the server already filtered by `?search=`. If you leave the default on, the Command primitive double-filters and hides newly typed-but-not-yet-fetched matches.
- "Create" only renders when the typed string has no exact match. Otherwise users hit it accidentally when the row already exists.

`src/components/cv/location-picker.tsx` mirrors the same pattern but for a single FK (not many-to-many). It accepts `value: number | null` and `onChange: (next: number | null) => void`:

```tsx
import { useState } from "react";
import { Check, Plus } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useList, type LocationRow } from "@/lib/queries/jac";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

export function LocationPicker({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (next: number | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const qc = useQueryClient();
  const list = useList<LocationRow>("locations", { search });
  const rows = list.data?.results ?? [];
  const current = rows.find((r) => r.id === value);
  const exactMatch = rows.some(
    (r) => r.city.toLowerCase() === search.trim().toLowerCase(),
  );

  const create = useMutation({
    mutationFn: (city: string) =>
      api<LocationRow>("/api/jac/locations/", {
        method: "POST",
        body: JSON.stringify({ city }),
      }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["jac", "locations"] });
      onChange(created.id);
      setSearch("");
      setOpen(false);
    },
  });

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" className="justify-start w-full">
          {current ? `${current.city}${current.country ? ", " + current.country : ""}` : "Pick location…"}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0">
        <Command shouldFilter={false}>
          <CommandInput
            placeholder="Search cities…"
            value={search}
            onValueChange={setSearch}
          />
          <CommandList>
            <CommandEmpty>No matches.</CommandEmpty>
            <CommandGroup>
              {value !== null && (
                <CommandItem onSelect={() => { onChange(null); setOpen(false); }}>
                  <span className="text-muted-foreground">Clear</span>
                </CommandItem>
              )}
              {rows.map((r) => (
                <CommandItem
                  key={r.id}
                  onSelect={() => { onChange(r.id); setOpen(false); }}
                >
                  <Check
                    className={
                      "size-4 mr-2 " + (r.id === value ? "opacity-100" : "opacity-0")
                    }
                  />
                  {r.city}{r.country ? `, ${r.country}` : ""}
                </CommandItem>
              ))}
              {search.trim() && !exactMatch && (
                <CommandItem
                  onSelect={() => create.mutate(search.trim())}
                  disabled={create.isPending}
                >
                  <Plus className="size-4 mr-2" />
                  Create "{search.trim()}"
                </CommandItem>
              )}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
```

The `LocationPicker` create path makes a row with city only. The editor for Location itself (street, zip, lat/lon) lives in a dedicated `/cv/locations` page if you ever need it — for now, locations are cheap rows users only set the city on.

A `SkillPicker` (multi-select FK list) follows the exact same shape; copy `domain-picker.tsx` and swap the resource. Defer building it until Jobs/Projects step (§7c).

---

## 5. CV dashboard (`/cv`)

Landing page after auth. Counts per section, last-edited row per section, quick links into the lists.

`src/routes/_authenticated/cv/index.tsx`:

```tsx
import { createFileRoute, Link } from "@tanstack/react-router";
import { useQueries } from "@tanstack/react-query";
import { fetchPage, type Page } from "@/lib/queries/paginated";
import type { ResourceKey } from "@/lib/queries/jac";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const SECTIONS: { key: ResourceKey; label: string; to: string; url: string }[] = [
  { key: "jobs",           label: "Jobs",           to: "/cv/jobs",           url: "/api/jac/jobs/" },
  { key: "education",      label: "Education",      to: "/cv/education",      url: "/api/jac/education/" },
  { key: "skills",         label: "Skills",         to: "/cv/skills",         url: "/api/jac/skills/" },
  { key: "certifications", label: "Certifications", to: "/cv/certifications", url: "/api/jac/certifications/" },
  { key: "projects",       label: "Projects",       to: "/cv/projects",       url: "/api/jac/projects/" },
  { key: "languages",      label: "Languages",      to: "/cv/languages",      url: "/api/jac/languages/" },
];

export const Route = createFileRoute("/_authenticated/cv/")({
  component: CvDashboard,
});

function CvDashboard() {
  const queries = useQueries({
    queries: SECTIONS.map((s) => ({
      queryKey: ["jac", s.key, "list", { ordering: "-updated_at", page: 1 }],
      queryFn: () =>
        fetchPage<{ id: number; updated_at?: string }>(s.url, {
          ordering: "-updated_at",
        }),
    })),
  });

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {SECTIONS.map((s, i) => {
        const q = queries[i];
        const data = q.data as Page<{ id: number }> | undefined;
        return (
          <Link key={s.key} to={s.to} className="block">
            <Card className="hover:bg-muted/40 transition-colors">
              <CardHeader>
                <CardTitle className="text-base">{s.label}</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-semibold">
                  {q.isLoading ? "…" : (data?.count ?? 0)}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {(data?.count ?? 0) === 0 ? "No entries yet" : "Click to manage"}
                </p>
              </CardContent>
            </Card>
          </Link>
        );
      })}
    </div>
  );
}
```

`ordering=-updated_at` only works if `updated_at` is in the resource's allowed ordering list. The default `OrderingFilter` accepts any model field, so we're fine — but if you ever lock it down with `ordering_fields`, add `updated_at` explicitly.

Verify: visit `/cv` after login. Six cards. Counts match the Django admin.

---

## 6. Public landing at `/` + post-login lands on `/cv`

`/` stays a **real, public portfolio landing** — no redirect. Authenticated
flows land on `/cv` directly. Three touchpoints:

1. **Root `/` route** is a meaningful welcome page (no `beforeLoad` redirect).
   It branches on `useAuth().status`: logged-out visitors get the welcome copy,
   a "Create your own CV" CTA → `/auth/signup`, and a "Sign in" link; logged-in
   visitors get "Go to your CV" → `/cv` and "Profile" → `/account/profile`. It
   renders its own full-screen hero (outside the authed layout, so no header).
   See `src/routes/index.tsx`.

2. **Post-auth landings point at `/cv`.** Because `/` is no longer the smart
   redirect, repoint each flow explicitly:
   - `login.tsx` → `navigate({ to: redirect ?? "/cv" })`
   - `signup.tsx` → `navigate({ to: "/cv" })`
   - `mfa-challenge.tsx` → `navigate({ to: redirect ?? "/cv" })`
   - `verify-email.tsx` → was hard-coded to `/account/profile`; now `"/cv"`

   Login/MFA keep `redirect ?? "/cv"` so a deep-link redirect (e.g. the
   `_authenticated` gate's captured `location.href`) still wins.

3. **Authed layout header** — the lukehirsch wordmark on `_authenticated.tsx`
   stays a `Link to="/"`. `/` is now a genuine portfolio home, so the wordmark
   reads correctly without change.

Verify: logged out, visit `/` → welcome page with CTA. Sign up / log in → land
on `/cv`. Header wordmark → `/` (portfolio home, shows CV/Profile links while
authenticated). "Profile" header link → `/account/profile`.

---

## 7. `/cv/jobs` — the worked example

This is the section you'll copy from for the other five. Walk through it end to end.

### 7a. Zod schema + field types

`src/routes/_authenticated/cv/jobs.tsx`:

```tsx
import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useForm } from "@tanstack/react-form";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  useReactTable,
  type RowSelectionState,
} from "@tanstack/react-table";
import { toast } from "sonner";
import { Pencil, Trash2 } from "lucide-react";
import {
  useList,
  useCreate,
  useUpdate,
  useDestroy,
  useBulkDestroy,
  useBulkPatchDomains,
  type JobRow,
  type DomainRow,
} from "@/lib/queries/jac";
import { useDebounced } from "@/lib/use-debounced";
import { zodValidator, z } from "@/lib/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SectionPage } from "@/components/cv/section-page";
import { DomainPicker } from "@/components/cv/domain-picker";
import { LocationPicker } from "@/components/cv/location-picker";
import { MarkdownPreview } from "@/components/markdown-preview";
import { BulkBar } from "@/components/cv/bulk-bar";

const JOB_TYPES: { value: JobRow["job_type"]; label: string }[] = [
  { value: "ft", label: "Full-time" },
  { value: "pt", label: "Part-time" },
  { value: "ct", label: "Contract" },
  { value: "fl", label: "Freelance" },
  { value: "in", label: "Internship" },
  { value: "vl", label: "Volunteer" },
];

const schema = z.object({
  title: z.string().min(1, "Required").max(200),
  company: z.string().min(1, "Required").max(200),
  job_type: z.enum(["ft", "pt", "ct", "fl", "in", "vl"]),
  started: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "YYYY-MM-DD"),
  ended: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "YYYY-MM-DD").or(z.literal("")),
  url: z.string().url().or(z.literal("")),
  description: z.string(),
  location: z.number().nullable(),
  domains: z.array(z.number()),
  skills: z.array(z.number()),
});

type JobInput = z.infer<typeof schema>;

export const Route = createFileRoute("/_authenticated/cv/jobs")({
  component: JobsPage,
});
```

### 7b. Page component

```tsx
function JobsPage() {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search);
  const [jobType, setJobType] = useState<JobRow["job_type"] | "">("");
  const [selection, setSelection] = useState<RowSelectionState>({});
  const [editing, setEditing] = useState<JobRow | null>(null);
  const [open, setOpen] = useState(false);

  const list = useList<JobRow>("jobs", {
    search: debouncedSearch,
    filters: jobType ? { job_type: jobType } : undefined,
  });

  const destroy = useDestroy("jobs");
  const bulkDestroy = useBulkDestroy("jobs");
  const bulkDomains = useBulkPatchDomains("jobs");

  const columns = useMemo(() => buildColumns({
    onEdit: (row) => { setEditing(row); setOpen(true); },
    onDelete: (row) => {
      if (!confirm(`Delete "${row.title}" at ${row.company}?`)) return;
      destroy.mutate(row.id, {
        onSuccess: () => toast.success("Deleted"),
        onError: () => toast.error("Delete failed"),
      });
    },
  }), [destroy]);

  const rows = list.data?.results ?? [];
  const table = useReactTable({
    data: rows,
    columns,
    state: { rowSelection: selection },
    onRowSelectionChange: setSelection,
    enableRowSelection: true,
    getRowId: (r) => String(r.id),
    getCoreRowModel: getCoreRowModel(),
  });

  const selectedIds = Object.keys(selection).map(Number);

  return (
    <SectionPage<JobRow>
      title="Jobs"
      description="Employment + contract history. Started date is required."
      search={search}
      onSearchChange={setSearch}
      filters={
        <Select value={jobType || "all"} onValueChange={(v) => setJobType(v === "all" ? "" : (v as JobRow["job_type"]))}>
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All types</SelectItem>
            {JOB_TYPES.map((t) => (
              <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      }
      table={
        <>
          <BulkBar
            count={selectedIds.length}
            onDelete={() => {
              if (!confirm(`Delete ${selectedIds.length} jobs?`)) return;
              bulkDestroy.mutate(selectedIds, {
                onSuccess: () => { toast.success("Deleted"); setSelection({}); },
                onError: () => toast.error("Bulk delete failed"),
              });
            }}
            onAssignDomains={(add, remove) =>
              bulkDomains.mutate(
                { ids: selectedIds, add, remove },
                {
                  onSuccess: () => { toast.success("Domains updated"); setSelection({}); },
                  onError: () => toast.error("Bulk domain update failed"),
                },
              )
            }
          />
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                {table.getHeaderGroups().map((hg) => (
                  <TableRow key={hg.id}>
                    {hg.headers.map((h) => (
                      <TableHead key={h.id} style={{ width: h.getSize() }}>
                        {flexRender(h.column.columnDef.header, h.getContext())}
                      </TableHead>
                    ))}
                  </TableRow>
                ))}
              </TableHeader>
              <TableBody>
                {list.isLoading && (
                  <TableRow>
                    <TableCell colSpan={columns.length} className="text-center text-muted-foreground">
                      Loading…
                    </TableCell>
                  </TableRow>
                )}
                {!list.isLoading && rows.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={columns.length} className="text-center text-muted-foreground">
                      No jobs yet — click <strong>New</strong> to add one.
                    </TableCell>
                  </TableRow>
                )}
                {table.getRowModel().rows.map((row) => (
                  <TableRow key={row.id} data-state={row.getIsSelected() ? "selected" : undefined}>
                    {row.getVisibleCells().map((cell) => (
                      <TableCell key={cell.id}>
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
      }
      editor={(row, close) => <JobEditor row={row} onClose={close} />}
      open={open}
      editing={editing}
      onOpenChange={(o) => { setOpen(o); if (!o) setEditing(null); }}
      onNew={() => { setEditing(null); setOpen(true); }}
    />
  );
}
```

### 7c. Column config

```tsx
const col = createColumnHelper<JobRow>();

function buildColumns(opts: {
  onEdit: (r: JobRow) => void;
  onDelete: (r: JobRow) => void;
}) {
  return [
    col.display({
      id: "select",
      size: 32,
      header: ({ table }) => (
        <Checkbox
          checked={
            table.getIsAllRowsSelected() ||
            (table.getIsSomeRowsSelected() && "indeterminate")
          }
          onCheckedChange={(v) => table.toggleAllRowsSelected(!!v)}
        />
      ),
      cell: ({ row }) => (
        <Checkbox
          checked={row.getIsSelected()}
          onCheckedChange={(v) => row.toggleSelected(!!v)}
        />
      ),
    }),
    col.accessor("title", { header: "Title" }),
    col.accessor("company", { header: "Company" }),
    col.accessor("job_type", {
      header: "Type",
      cell: ({ getValue }) => (
        <Badge variant="outline">
          {JOB_TYPES.find((t) => t.value === getValue())?.label ?? getValue()}
        </Badge>
      ),
    }),
    col.accessor("started", { header: "From" }),
    col.accessor("ended", {
      header: "To",
      cell: ({ getValue }) => getValue() || <span className="text-muted-foreground">present</span>,
    }),
    col.display({
      id: "actions",
      header: "",
      size: 80,
      cell: ({ row }) => (
        <div className="flex gap-1 justify-end">
          <Button variant="ghost" size="icon" onClick={() => opts.onEdit(row.original)}>
            <Pencil className="size-4" />
          </Button>
          <Button variant="ghost" size="icon" onClick={() => opts.onDelete(row.original)}>
            <Trash2 className="size-4" />
          </Button>
        </div>
      ),
    }),
  ];
}
```

### 7d. Drawer editor

`JobEditor` is a child component of the page so it can pull from the same Zod schema. It opens for "new" when `row` is null, "edit" otherwise.

```tsx
function JobEditor({ row, onClose }: { row: JobRow | null; onClose: () => void }) {
  const create = useCreate<JobRow, JobInput>("jobs");
  const update = useUpdate<JobRow, JobInput>("jobs");
  const initial: JobInput = row
    ? {
        title: row.title,
        company: row.company,
        job_type: row.job_type,
        started: row.started,
        ended: row.ended ?? "",
        url: row.url ?? "",
        description: row.description,
        location: row.location,
        domains: row.domains,
        skills: row.skills,
      }
    : {
        title: "",
        company: "",
        job_type: "ft",
        started: "",
        ended: "",
        url: "",
        description: "",
        location: null,
        domains: [],
        skills: [],
      };

  const form = useForm({
    defaultValues: initial,
    validators: { onChange: zodValidator(schema) },
    onSubmit: async ({ value }) => {
      const body: JobInput = { ...value, ended: value.ended || "" };
      try {
        if (row) await update.mutateAsync({ id: row.id, body });
        else await create.mutateAsync(body);
        toast.success(row ? "Updated" : "Created");
        onClose();
      } catch (e) {
        toast.error("Save failed — check field errors");
      }
    },
  });

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => { e.preventDefault(); form.handleSubmit(); }}
    >
      <form.Field name="title">{(f) => (
        <div className="space-y-1">
          <Label htmlFor={f.name}>Title</Label>
          <Input id={f.name} value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} />
          <FieldError errors={f.state.meta.errors} />
        </div>
      )}</form.Field>

      <form.Field name="company">{(f) => (
        <div className="space-y-1">
          <Label htmlFor={f.name}>Company</Label>
          <Input id={f.name} value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} />
          <FieldError errors={f.state.meta.errors} />
        </div>
      )}</form.Field>

      <form.Field name="job_type">{(f) => (
        <div className="space-y-1">
          <Label>Type</Label>
          <Select value={f.state.value} onValueChange={(v) => f.handleChange(v as JobInput["job_type"])}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {JOB_TYPES.map((t) => (
                <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}</form.Field>

      <div className="grid grid-cols-2 gap-3">
        <form.Field name="started">{(f) => (
          <div className="space-y-1">
            <Label htmlFor={f.name}>Started</Label>
            <Input id={f.name} type="date" value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} />
            <FieldError errors={f.state.meta.errors} />
          </div>
        )}</form.Field>
        <form.Field name="ended">{(f) => (
          <div className="space-y-1">
            <Label htmlFor={f.name}>Ended (blank = present)</Label>
            <Input id={f.name} type="date" value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} />
            <FieldError errors={f.state.meta.errors} />
          </div>
        )}</form.Field>
      </div>

      <form.Field name="url">{(f) => (
        <div className="space-y-1">
          <Label htmlFor={f.name}>URL</Label>
          <Input id={f.name} type="url" value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} />
          <FieldError errors={f.state.meta.errors} />
        </div>
      )}</form.Field>

      <form.Field name="location">{(f) => (
        <div className="space-y-1">
          <Label>Location</Label>
          <LocationPicker value={f.state.value} onChange={f.handleChange} />
        </div>
      )}</form.Field>

      <form.Field name="domains">{(f) => (
        <div className="space-y-1">
          <Label>Domains</Label>
          <DomainPicker value={f.state.value} onChange={f.handleChange} />
        </div>
      )}</form.Field>

      <form.Field name="description">{(f) => (
        <div className="space-y-1">
          <Label htmlFor={f.name}>Description (Markdown)</Label>
          <div className="grid md:grid-cols-2 gap-3">
            <Textarea
              id={f.name}
              rows={10}
              value={f.state.value}
              onChange={(e) => f.handleChange(e.target.value)}
              className="font-mono text-sm"
            />
            <div className="border rounded-md p-3 min-h-[240px] bg-muted/20">
              <MarkdownPreview source={f.state.value} />
            </div>
          </div>
        </div>
      )}</form.Field>

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
        <Button type="submit" disabled={create.isPending || update.isPending}>
          {row ? "Save" : "Create"}
        </Button>
      </div>
    </form>
  );
}

function FieldError({ errors }: { errors: Array<unknown> }) {
  const msg = errors.find((e) => typeof e === "string" || (e && typeof e === "object"));
  if (!msg) return null;
  const text =
    typeof msg === "string"
      ? msg
      : (msg as { message?: string }).message ??
        (msg as { fields?: Record<string, string> }).fields?.[Object.keys((msg as { fields?: Record<string, string> }).fields ?? {})[0]] ??
        "Invalid";
  return <p className="text-xs text-destructive">{String(text)}</p>;
}
```

The `SkillPicker` field is intentionally not wired in this first pass — Jobs have `skills: number[]` but populating skills in the editor only makes sense once the user actually has Skill rows. Land §8c's Skills page first, then come back and drop a `SkillPicker` field block in.

### 7e. Verify

- Visit `/cv/jobs`. Existing rows render. Search "engineer" — list filters as you type (debounced).
- Change the "Type" dropdown — `?job_type=` lands on the request (check the Network tab).
- Click **New** → drawer opens → fill in title, company, started → Save. Toast "Created". Drawer closes. Row appears in table.
- Edit a row's description, type some Markdown (`### Heading\n- bullet`) → preview pane renders.
- Add a domain inline via the picker — type a new name → "Create" → domain attaches; reload page → still attached.
- Delete a row via the trash icon → confirm → row gone.

If all six pass, the worked example is done. The next five sections are variations on this skeleton.

---

## 8. The other five sections

Each section is a copy of `jobs.tsx` with: a different Zod schema, different column config, a different editor body. The page shell, drawer, picker wiring, table chrome, and bulk bar are the same. Build them in this order so you keep climbing the difficulty curve: certifications → languages → education → projects → skills.

### 8a. `/cv/certifications`

The simplest. No FKs to other CV tables, no M2Ms.

Schema:

```ts
const schema = z.object({
  name: z.string().min(1).max(200),
  issuer: z.string().min(1).max(200),
  issued_on: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).or(z.literal("")),
  expires_on: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).or(z.literal("")),
  credential_id: z.string().max(200),
  url: z.string().url().or(z.literal("")),
  description: z.string(),
});
```

Columns: `name`, `issuer`, `issued_on`, `expires_on`, actions. No filter dropdown. No bulk domain picker (no domain M2M on this model — drop the `bulkDomains` props from the `BulkBar`).

Verify: create one → reload → row persists. Empty `issued_on`/`expires_on` round-trip cleanly (Django accepts `null`; the serializer accepts blank strings via `allow_null=True`).

### 8b. `/cv/languages`

Even simpler — just `name`, `fluency`, `description`. Use a fluency `Select` in the filter row + the editor. No FKs, no M2Ms.

Schema:

```ts
const schema = z.object({
  name: z.string().min(1).max(100),
  fluency: z.enum(["native", "fluent", "professional", "conversational", "basic"]),
  description: z.string(),
});
```

Columns: `name`, `fluency` (Badge), actions.

### 8c. `/cv/education`

Adds a `LocationPicker` and date fields. `started` is required; `ended` optional.

Schema:

```ts
const schema = z.object({
  institution: z.string().min(1).max(200),
  field_of_study: z.string().max(200),
  started: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  ended: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).or(z.literal("")),
  degree: z.string().max(100),
  grade: z.string().max(50),
  description: z.string(),
  location: z.number().nullable(),
});
```

No domain M2M; trim the bulk bar accordingly.

### 8d. `/cv/projects`

Almost identical to Jobs but with `name` instead of `title/company`, both date fields optional. M2M to Skills + Domains, FK to Location.

Schema:

```ts
const schema = z.object({
  name: z.string().min(1).max(200),
  started: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).or(z.literal("")),
  ended: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).or(z.literal("")),
  url: z.string().url().or(z.literal("")),
  description: z.string(),
  location: z.number().nullable(),
  skills: z.array(z.number()),
  domains: z.array(z.number()),
});
```

Re-use `DomainPicker`. For `skills`, build `src/components/cv/skill-picker.tsx` by cloning `domain-picker.tsx` and swapping the resource — same multi-select UX. Skip "create new" on the SkillPicker for now (creating a Skill on the fly without category/proficiency is half-formed; force users to `/cv/skills` for new skills).

### 8e. `/cv/skills`

The most field-heavy. Adds the `proficiency` + `category` enums (filter + editor), the `domains` M2M, an optional `certification` FK, and `first_used` date.

Schema:

```ts
const schema = z.object({
  name: z.string().min(1).max(200),
  proficiency: z.enum(["beginner", "intermediate", "advanced", "expert"]),
  category: z.enum(["technical", "soft", "domain", "other"]),
  domains: z.array(z.number()),
  first_used: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).or(z.literal("")),
  certification: z.number().nullable(),
  description: z.string(),
});
```

Filter row: two selects (`category`, `proficiency`) + domain `DomainPicker` configured for filter mode (you can also just leave domain filtering to the bulk-edit / list-filter as a query param — drop a small `<select>` listing user domains if you don't want a full picker in the filter row).

Columns: `name`, `proficiency` (Badge), `category` (Badge), `years_of_experience` (read-only), actions. Show `years_of_experience` as `—` when null.

`certification` is a FK to another user-scoped resource. Build a tiny `CertificationPicker` (clone `LocationPicker`, swap the resource, no "create new" — direct users to `/cv/certifications` to add).

Verify the computed field: create a Skill with `first_used: 2020-01-01` → list view shows `5` (or whatever the year delta is on the day you run this) → leave `first_used` empty but attach the Skill to an existing Job whose `started` date is older → reload the list → `years_of_experience` reflects the job. (`first_used` is the row, `years_of_experience` is the annotation; both round-trip independently.)

---

## 9. Bulk actions

`src/components/cv/bulk-bar.tsx` — sticky bar that appears above the table when at least one row is selected. Renders nothing otherwise.

```tsx
import { useState } from "react";
import { Trash2, Tag } from "lucide-react";
import { useList, type DomainRow } from "@/lib/queries/jac";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/checkbox";

export function BulkBar({
  count,
  onDelete,
  onAssignDomains,
}: {
  count: number;
  onDelete: () => void;
  onAssignDomains?: (add: number[], remove: number[]) => void;
}) {
  if (count === 0) return null;
  return (
    <div className="flex items-center gap-2 rounded-md border bg-muted/40 p-2">
      <span className="text-sm">{count} selected</span>
      <div className="flex-1" />
      {onAssignDomains && <DomainAssignDialog onApply={onAssignDomains} />}
      <Button variant="destructive" size="sm" onClick={onDelete}>
        <Trash2 className="size-4" /> Delete
      </Button>
    </div>
  );
}

function DomainAssignDialog({
  onApply,
}: {
  onApply: (add: number[], remove: number[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [add, setAdd] = useState<Set<number>>(new Set());
  const [remove, setRemove] = useState<Set<number>>(new Set());
  const list = useList<DomainRow>("domains");
  const domains = list.data?.results ?? [];

  return (
    <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) { setAdd(new Set()); setRemove(new Set()); } }}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Tag className="size-4" /> Domains…
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Bulk domain assignment</DialogTitle>
        </DialogHeader>
        <div className="space-y-2 max-h-80 overflow-y-auto">
          {domains.map((d) => {
            const isAdd = add.has(d.id);
            const isRemove = remove.has(d.id);
            return (
              <div key={d.id} className="flex items-center gap-3">
                <span className="flex-1">{d.name}</span>
                <label className="flex items-center gap-1 text-sm">
                  <Checkbox
                    checked={isAdd}
                    onCheckedChange={(v) => {
                      const next = new Set(add);
                      v ? next.add(d.id) : next.delete(d.id);
                      setAdd(next);
                      if (v) { const r = new Set(remove); r.delete(d.id); setRemove(r); }
                    }}
                  />
                  add
                </label>
                <label className="flex items-center gap-1 text-sm">
                  <Checkbox
                    checked={isRemove}
                    onCheckedChange={(v) => {
                      const next = new Set(remove);
                      v ? next.add(d.id) : next.delete(d.id);
                      setRemove(next);
                      if (v) { const a = new Set(add); a.delete(d.id); setAdd(a); }
                    }}
                  />
                  remove
                </label>
              </div>
            );
          })}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            disabled={add.size === 0 && remove.size === 0}
            onClick={() => { onApply([...add], [...remove]); setOpen(false); }}
          >
            Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

The "add" + "remove" toggles are mutually exclusive per row — checking "add" auto-unchecks "remove". The mutation in `useBulkPatchDomains` (defined in §3.2) computes the per-row final set via `(existing ∪ add) \ remove`.

Verify, on `/cv/skills`:

- Select two rows → bar appears with "2 selected".
- "Delete" → both gone after the toast.
- Recreate them, attach a "Python" domain to one. Select both → "Domains…" → add "Backend", remove "Python" → Apply. After invalidate: row A has only "Backend", row B has only "Backend" (Python was removed only where it existed).

---

## 10. End-to-end verification — the full loop

Backend + frontend running, logged in as a verified user with TOTP set up (Phase 2b).

1. **Land.** Visit `/` → public welcome page; click "Go to your CV" → `/cv`. Six cards with counts. Click "Jobs" → `/cv/jobs`.
2. **Search.** Type "engineer" in the search box → list filters within ~250ms. Network tab shows `?search=engineer`. Clear → full list returns.
3. **Filter.** Select "Contract" → `?job_type=ct` lands. Switch to "All types" → query param drops.
4. **Create.** Click **New**. Drawer slides in. Title = "Staff Engineer", Company = "ACME", Started = 2024-01-15. Save → toast "Created" → drawer closes → new row at the top.
5. **Edit.** Click pencil on the new row. Drawer opens with values pre-filled. Add a Markdown description with a heading + bullet list → live preview renders. Save → reload → still there.
6. **Domain picker.** In the same drawer, type "Hardware" in the domain picker → "Create" appears → click → badge added. Reload page → still attached.
7. **Location picker.** Type "Berlin" → "Create" → drawer label flips to "Berlin". Reload → row's `location` FK still set.
8. **Bulk delete.** Select two test rows → bar shows "2 selected" → Delete → confirm → both gone.
9. **Bulk domains.** Recreate two Skill rows → on `/cv/skills`, select both → Domains → add one, remove one → Apply → reload → both rows have the right domain set.
10. **Computed `years_of_experience`.** On `/cv/skills`, create a Skill with `first_used = 2020-01-01` → column shows `5` (or whatever the year delta is at the time you check). Edit it to clear `first_used` and attach it to an existing Job that started 2018-06-01 → column shows `7`.
11. **Cross-section.** On `/cv` overview, click "Languages" → empty? Add one. Back to `/cv` → counter increments.
12. **Persistence.** Log out (header), log back in → land on `/cv` → all the above still in place. Open Django shell → `Job.objects.filter(user__email="you@example.com")` shows the rows you created.
13. **Permissions.** Open a private browser, log in as a different user → `/cv/jobs` shows their data, not yours.

If all thirteen pass, Phase 2c is done.

---

## 11. What you should have at the end

```
frontend/src/
├── components/
│   ├── cv/
│   │   ├── bulk-bar.tsx
│   │   ├── certification-picker.tsx   # built in step 8e
│   │   ├── domain-picker.tsx
│   │   ├── location-picker.tsx
│   │   ├── section-page.tsx
│   │   └── skill-picker.tsx           # built in step 8d
│   ├── markdown-preview.tsx
│   └── ui/
│       ├── badge.tsx
│       ├── checkbox.tsx
│       ├── command.tsx
│       ├── popover.tsx
│       └── sheet.tsx
├── lib/
│   ├── queries/
│   │   ├── jac.ts
│   │   └── paginated.ts
│   └── use-debounced.ts
└── routes/
    ├── index.tsx                       # public portfolio landing (welcome + CTA)
    └── _authenticated/
        └── cv/
            ├── index.tsx               # dashboard
            ├── certifications.tsx
            ├── education.tsx
            ├── jobs.tsx
            ├── languages.tsx
            ├── projects.tsx
            └── skills.tsx
        cv.tsx                          # layout (tabs)
```

Commit checkpoint:

```bash
git add frontend/ .claude/plans/phase-2c-setup-guide.md
git commit -m "Phase 2c: JAC CRUD (six section pages + dashboard + bulk actions)"
```

Run the suite once more to make sure nothing in the backend depends on a default ordering you accidentally changed:

```bash
cd backend && python manage.py test && cd ../frontend
```

---

## 12. Known gaps to revisit in Phase 3

Don't fix in 2c — log for the Phase 3 backend pass:

- **Bulk endpoints.** The N-request fan-out for bulk delete/domain-patch is fine at personal-CV scale, but a `POST /api/jac/<resource>/bulk/` action would be cleaner. Punt unless you actually feel the latency.
- **Skill creation from picker.** Currently disabled — creating a Skill needs category + proficiency. Either build a tiny inline mini-form, or accept that Skills get created on `/cv/skills` only.
- **`updated_at` ordering on the dashboard.** Works because `OrderingFilter` accepts any field; lock it down with explicit `ordering_fields = ["updated_at", ...]` on each viewset if you ever care about that surface.
- **Markdown safety.** `react-markdown` is HTML-safe by default (no `rehype-raw`), but if you ever enable raw HTML, add `rehype-sanitize`.
- **Locations editor.** Cities-only inline creation is fine for now; a dedicated `/cv/locations` page (street/zip/lat/lon) belongs in Phase 3 if anyone asks. The Django admin covers the gap meanwhile.
- **Domain "system default" UX.** Defaults look identical to user-owned rows in the picker. Either tag them visually (Badge "default") or split the list. Either decision wants its own design conversation.
- **Search debouncing on filters.** Search has 250ms debounce; filter dropdowns hit immediately. That's right for one-shot selects, wrong if you ever surface a free-text filter input.
- **Avatar / file fields.** Still no multipart helper. Defer to Phase 3 with the profile-avatar work.
- **Tests.** Backend still at 163; frontend still at zero. Phase 3 should add at least one Playwright smoke (`/cv/jobs` create → edit → delete) so the next refactor doesn't silently regress these flows.

---

## What's next

- **2d** — `/settings/llm` + `/settings/llm/usage` backed by `/api/llm/`. The `LLMConfig.api_key` write-only field is the only real twist; everything else is a smaller version of what you just built. The `useList` / `useCreate` / `useUpdate` / `useDestroy` factories drop straight in once you add `llm-configs` + `llm-request-logs` to the `R` map.
- **Phase 3** — gather the backend gaps surfaced by 2c/2d (bulk endpoints, dedicated `ordering_fields`, multipart parser, etc.), plus the first SPA backend (`PortfolioLink`, `VisitorResponse`) and `celery.py` + a trivial task to prove the wiring.
