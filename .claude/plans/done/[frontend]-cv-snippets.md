# [frontend] Cover-letter snippets — CRUD tab under `/cv`

## Context / goal

The cover-letter pipeline (`SnippetSelector` → `CoverLetterWriter`) selects `ResumeSnippet`s by
relevance to a posting, but there is **no way to author them in the app**. The backend is fully
wired; the frontend has zero snippet UI. This guide adds a **Snippets** tab to the `/cv` section —
a CRUD page in the exact shape of the existing career-DB tabs (jobs/skills/…), so a user can write
intro / achievement / value-statement / closing snippets **and connect each one to the career DB**
(job, project, skills, domains) that gives it context.

Roadmap: this is a gap-fill under the career-DB surface, not a numbered roadmap item — the cover
letter is "done (backend)" but its raw material was un-authorable. It also feeds roadmap #1 (the
generation pipeline consumes these snippets).

### What the backend already gives us (no backend work here)

- **Model** `jac.models.ResumeSnippet`: `title`, `content`, `kind`
  (`intro`/`achievement`/`value_statement`/`closing`/`other`), `language` (default `"en"`),
  `is_active` (default `True`), plus the **career-DB links** — `domains` (M2M), `skills` (M2M),
  `job` (FK, nullable), `project` (FK, nullable). No `favourite` field.
- **Serializer** `ResumeSnippetSerializer`: user-hidden, all related querysets scoped to the request
  user (`user_scoped_fields = ("skills","job","project")`, `domain_scoped_fields = ("domains",)`).
- **Viewset** `ResumeSnippetViewSet` (route `resume-snippets`): `IsAuthenticated, IsOwner`,
  `search_fields = ["title","content"]`, `filterset_fields = ["kind","is_active","domains","skills"]`,
  `ordering_fields = ["kind","title","created_at","updated_at"]`, and `BulkActionMixin`
  (`delete` + `patch_domains`, both valid since the model has `domains`).

Endpoint base: `/api/jac/resume-snippets/`.

## Affected files

| path                                                 | change                                                                                                                                |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `frontend/src/lib/queries/jac.ts`                    | add `ResumeSnippetRow` type; register `snippets` in `R`; widen `useBulkPatchDomains` to accept `"snippets"`                           |
| `frontend/src/lib/snippet-form.ts`                   | **new** — pure form↔payload helpers (kinds, zod schema, `emptySnippetInput`, `snippetToInput`, `toSnippetPayload`); the testable core |
| `frontend/src/components/cv/project-picker.tsx`      | **new** — single-select FK picker over the user's projects (mirror of `JobPicker`)                                                    |
| `frontend/src/routes/_authenticated/cv/snippets.tsx` | **new** — the CRUD page (mirror of `jobs.tsx`)                                                                                        |
| `frontend/src/routes/_authenticated/cv.tsx`          | add the `Snippets` tab to `TABS`                                                                                                      |
| `frontend/src/routes/_authenticated/cv/index.tsx`    | add the `Snippets` card to `SECTIONS`                                                                                                 |
| `frontend/src/routeTree.gen.ts`                      | **auto-generated** by the `tanstackRouter` vite plugin when `snippets.tsx` appears — don't hand-edit; just run the dev server once    |

Reused as-is (no change): `DomainPicker`, `DomainFilter`, `SkillPicker`, `JobPicker`, `SectionPage`,
`Pagination`, `BulkBar`, `MarkdownPreview`, and the generic `usePagedList/useCreate/useUpdate/
useDestroy/useBulkDestroy/useBulkPatchDomains` hooks from `jac.ts`.

---

## The code

Type it in this order (types → pure helpers → picker → route → nav).

### 1. `frontend/src/lib/queries/jac.ts`

Add the row type next to the other `…Row` types (e.g. after `LanguageRow`):

```ts
export type ResumeSnippetRow = {
  id: number;
  title: string;
  content: string;
  kind: "intro" | "achievement" | "value_statement" | "closing" | "other";
  domains: number[];
  skills: number[];
  job: number | null;
  project: number | null;
  language: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};
```

Add the resource to the `R` registry (note the route is `resume-snippets`, the key is the shorter
`snippets`):

```ts
const R = {
  domains: { key: "domains", url: "/api/jac/domains/" },
  locations: { key: "locations", url: "/api/jac/locations/" },
  education: { key: "education", url: "/api/jac/education/" },
  certifications: { key: "certifications", url: "/api/jac/certifications/" },
  skills: { key: "skills", url: "/api/jac/skills/" },
  jobs: { key: "jobs", url: "/api/jac/jobs/" },
  projects: { key: "projects", url: "/api/jac/projects/" },
  languages: { key: "languages", url: "/api/jac/languages/" },
  snippets: { key: "snippets", url: "/api/jac/resume-snippets/" },
} as const satisfies Record<string, Resource>;
```

Widen the `useBulkPatchDomains` key constraint so the snippets page can bulk-assign domains (the
model has `domains`, so `patch_domains` is supported server-side):

```ts
export function useBulkPatchDomains(
  key: Extract<ResourceKey, "skills" | "jobs" | "projects" | "snippets">,
) {
```

Everything else (`usePagedList`, `useCreate`, `useUpdate`, `useDestroy`, `useBulkDestroy`) works for
`"snippets"` for free once it's in `R`.

### 2. `frontend/src/lib/snippet-form.ts` (new — the testable core)

Pure, no React, no network — mirrors how the LLM tab keeps its form↔payload logic in `llm.ts` so it
can be unit-tested. The route below imports everything from here.

```ts
import { z } from "@/lib/form";
import type { ResumeSnippetRow } from "@/lib/queries/jac";

/** The five backend `ResumeSnippet.Kind` choices, in author-facing order. */
export const SNIPPET_KINDS = [
  { value: "intro", label: "Introduction" },
  { value: "achievement", label: "Achievement" },
  { value: "value_statement", label: "Value statement" },
  { value: "closing", label: "Closing" },
  { value: "other", label: "Other" },
] as const;

export type SnippetKind = (typeof SNIPPET_KINDS)[number]["value"];

export const snippetSchema = z.object({
  title: z.string().min(1, "Required").max(200),
  content: z.string().min(1, "Required"),
  kind: z.enum(["intro", "achievement", "value_statement", "closing", "other"]),
  language: z.string().min(2, "e.g. en, de").max(8),
  domains: z.array(z.number()),
  skills: z.array(z.number()),
  job: z.number().nullable(),
  project: z.number().nullable(),
  is_active: z.boolean(),
});

export type SnippetInput = z.infer<typeof snippetSchema>;

/** Fresh blank form state (new arrays each call — never share references). */
export function emptySnippetInput(): SnippetInput {
  return {
    title: "",
    content: "",
    kind: "other",
    language: "en",
    domains: [],
    skills: [],
    job: null,
    project: null,
    is_active: true,
  };
}

/** Seed form state from an existing row, or blank defaults when creating. */
export function snippetToInput(row: ResumeSnippetRow | null): SnippetInput {
  if (!row) return emptySnippetInput();
  return {
    title: row.title,
    content: row.content,
    kind: row.kind,
    language: row.language,
    domains: [...row.domains],
    skills: [...row.skills],
    job: row.job,
    project: row.project,
    is_active: row.is_active,
  };
}

export type SnippetPayload = {
  title: string;
  content: string;
  kind: SnippetKind;
  language: string;
  domains: number[];
  skills: number[];
  job: number | null;
  project: number | null;
  is_active: boolean;
};

/** Assemble the request body: trim title, normalise language, empty lang → "en". */
export function toSnippetPayload(input: SnippetInput): SnippetPayload {
  return {
    title: input.title.trim(),
    content: input.content,
    kind: input.kind,
    language: (input.language.trim() || "en").toLowerCase(),
    domains: input.domains,
    skills: input.skills,
    job: input.job,
    project: input.project,
    is_active: input.is_active,
  };
}
```

> Note: FK fields are already `number | null` from the pickers, so there's no `"" → null` dance like
> jobs.tsx does for dates. `job`/`project` pass straight through.

### 3. `frontend/src/components/cv/project-picker.tsx` (new)

There's a `JobPicker` but no `ProjectPicker`, and a snippet links to both. This is a line-for-line
mirror of `job-picker.tsx` over the `projects` resource.

```tsx
import { useState } from "react";
import { Check } from "lucide-react";
import { useList, type ProjectRow } from "@/lib/queries/jac";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

/**
 * Single-select FK picker over the user's projects — used to link a resume
 * snippet to the project it describes. Mirror of `JobPicker`. No inline create:
 * a project needs a name (and usually a job/skills), so create them on
 * `/cv/projects`. The current selection resolves from an unsearched base list so
 * it still shows while you type a non-matching search.
 */
export function ProjectPicker({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (next: number | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const options = useList<ProjectRow>("projects", { search });
  const base = useList<ProjectRow>("projects", {});
  const rows = options.data?.results ?? [];
  const current = (base.data?.results ?? []).find((r) => r.id === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          className="justify-start w-full"
        >
          {current ? current.name : "Pick project…"}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 p-0">
        <Command shouldFilter={false}>
          <CommandInput
            placeholder="Search projects…"
            value={search}
            onValueChange={setSearch}
          />
          <CommandList>
            <CommandEmpty>No matches.</CommandEmpty>
            <CommandGroup>
              {value !== null && (
                <CommandItem
                  onSelect={() => {
                    onChange(null);
                    setOpen(false);
                  }}
                >
                  <span className="text-muted-foreground">Clear</span>
                </CommandItem>
              )}
              {rows.map((r) => (
                <CommandItem
                  key={r.id}
                  onSelect={() => {
                    onChange(r.id);
                    setOpen(false);
                  }}
                >
                  <Check
                    className={
                      "size-4 mr-2 " +
                      (r.id === value ? "opacity-100" : "opacity-0")
                    }
                  />
                  {r.name}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
```

### 4. `frontend/src/routes/_authenticated/cv/snippets.tsx` (new)

The page. Structurally identical to `jobs.tsx` (search + filters toolbar, selectable table with a
bulk bar, `SectionPage` sheet editor), minus the favourite machinery (no such field), plus the
snippet-specific fields and the four career-DB link pickers. Imports the pure helpers from
`snippet-form.ts`.

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
  usePagedList,
  useCreate,
  useUpdate,
  useDestroy,
  useBulkDestroy,
  useBulkPatchDomains,
  type ResumeSnippetRow,
} from "@/lib/queries/jac";
import {
  SNIPPET_KINDS,
  snippetSchema,
  snippetToInput,
  toSnippetPayload,
  type SnippetInput,
  type SnippetKind,
} from "@/lib/snippet-form";
import { useDebounced } from "@/lib/use-debounced";
import { zodValidator } from "@/lib/form";
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
import { Pagination } from "@/components/cv/pagination";
import { DomainPicker } from "@/components/cv/domain-picker";
import { DomainFilter } from "@/components/cv/domain-filter";
import { SkillPicker } from "@/components/cv/skill-picker";
import { JobPicker } from "@/components/cv/job-picker";
import { ProjectPicker } from "@/components/cv/project-picker";
import { MarkdownPreview } from "@/components/markdown-preview";
import { BulkBar } from "@/components/cv/bulk-bar";

const kindLabel = (v: string) =>
  SNIPPET_KINDS.find((k) => k.value === v)?.label ?? v;

export const Route = createFileRoute("/_authenticated/cv/snippets")({
  component: SnippetsPage,
});

function SnippetsPage() {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search);
  const [kind, setKind] = useState<SnippetKind | "">("");
  const [active, setActive] = useState<"" | "true" | "false">("");
  const [domain, setDomain] = useState<number | "">("");
  const [selection, setSelection] = useState<RowSelectionState>({});
  const [editing, setEditing] = useState<ResumeSnippetRow | null>(null);
  const [open, setOpen] = useState(false);

  const list = usePagedList<ResumeSnippetRow>("snippets", {
    search: debouncedSearch,
    filters: {
      kind: kind || undefined,
      is_active: active || undefined,
      domains: domain || undefined,
    },
  });

  const destroy = useDestroy("snippets");
  const bulkDestroy = useBulkDestroy("snippets");
  const bulkDomains = useBulkPatchDomains("snippets");

  const columns = useMemo(
    () =>
      buildColumns({
        onEdit: (row) => {
          setEditing(row);
          setOpen(true);
        },
        onDelete: (row) => {
          if (!confirm(`Delete "${row.title}"?`)) return;
          destroy.mutate(row.id, {
            onSuccess: () => toast.success("Deleted"),
            onError: () => toast.error("Delete failed"),
          });
        },
      }),
    [destroy],
  );

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
    <SectionPage<ResumeSnippetRow>
      title="Snippets"
      description="Reusable cover-letter building blocks. Link each to the jobs, projects, skills and domains it draws on so the pipeline can match it to a posting."
      search={search}
      onSearchChange={setSearch}
      filters={
        <>
          <Select
            value={kind || "all"}
            onValueChange={(v) =>
              setKind(v === "all" ? "" : (v as SnippetKind))
            }
          >
            <SelectTrigger className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All kinds</SelectItem>
              {SNIPPET_KINDS.map((k) => (
                <SelectItem key={k.value} value={k.value}>
                  {k.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={active || "all"}
            onValueChange={(v) =>
              setActive(v === "all" ? "" : (v as "true" | "false"))
            }
          >
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="true">Active</SelectItem>
              <SelectItem value="false">Inactive</SelectItem>
            </SelectContent>
          </Select>
          <DomainFilter value={domain} onChange={setDomain} />
        </>
      }
      table={
        <>
          <BulkBar
            count={selectedIds.length}
            onDelete={() => {
              if (!confirm(`Delete ${selectedIds.length} snippets?`)) return;
              bulkDestroy.mutate(selectedIds, {
                onSuccess: () => {
                  toast.success("Deleted");
                  setSelection({});
                },
                onError: () => toast.error("Bulk delete failed"),
              });
            }}
            onAssignDomains={(add, remove) =>
              bulkDomains.mutate(
                { ids: selectedIds, add, remove },
                {
                  onSuccess: () => {
                    toast.success("Domains updated");
                    setSelection({});
                  },
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
                    <TableCell
                      colSpan={columns.length}
                      className="text-center text-muted-foreground"
                    >
                      Loading…
                    </TableCell>
                  </TableRow>
                )}
                {!list.isLoading && rows.length === 0 && (
                  <TableRow>
                    <TableCell
                      colSpan={columns.length}
                      className="text-center text-muted-foreground"
                    >
                      No snippets yet — click <strong>New</strong> to add one.
                    </TableCell>
                  </TableRow>
                )}
                {table.getRowModel().rows.map((row) => (
                  <TableRow
                    key={row.id}
                    data-state={row.getIsSelected() ? "selected" : undefined}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <TableCell key={cell.id}>
                        {flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext(),
                        )}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </>
      }
      pagination={
        <Pagination
          page={list.page}
          count={list.data?.count ?? 0}
          onPageChange={list.setPage}
        />
      }
      editor={(row, close) => <SnippetEditor row={row} onClose={close} />}
      open={open}
      editing={editing}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setEditing(null);
      }}
      onNew={() => {
        setEditing(null);
        setOpen(true);
      }}
    />
  );
}

const col = createColumnHelper<ResumeSnippetRow>();

function buildColumns(opts: {
  onEdit: (r: ResumeSnippetRow) => void;
  onDelete: (r: ResumeSnippetRow) => void;
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
    col.accessor("kind", {
      header: "Kind",
      cell: ({ getValue }) => (
        <Badge variant="outline">{kindLabel(getValue())}</Badge>
      ),
    }),
    col.accessor("language", {
      header: "Lang",
      cell: ({ getValue }) => getValue().toUpperCase(),
    }),
    col.accessor("is_active", {
      header: "Status",
      cell: ({ getValue }) =>
        getValue() ? (
          <Badge variant="secondary">Active</Badge>
        ) : (
          <span className="text-muted-foreground">Inactive</span>
        ),
    }),
    col.display({
      id: "actions",
      header: "",
      size: 80,
      cell: ({ row }) => (
        <div className="flex gap-1 justify-end">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => opts.onEdit(row.original)}
          >
            <Pencil className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => opts.onDelete(row.original)}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      ),
    }),
  ];
}

function SnippetEditor({
  row,
  onClose,
}: {
  row: ResumeSnippetRow | null;
  onClose: () => void;
}) {
  const create = useCreate<ResumeSnippetRow>("snippets");
  const update = useUpdate<ResumeSnippetRow>("snippets");

  const form = useForm({
    defaultValues: snippetToInput(row),
    validators: { onChange: zodValidator(snippetSchema) },
    onSubmit: async ({ value }) => {
      const body = toSnippetPayload(value);
      try {
        if (row) await update.mutateAsync({ id: row.id, body });
        else await create.mutateAsync(body);
        toast.success(row ? "Updated" : "Created");
        onClose();
      } catch (e) {
        console.error(e);
        toast.error("Save failed — check field errors");
      }
    },
  });

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        form.handleSubmit();
      }}
    >
      <form.Field name="title">
        {(f) => (
          <div className="space-y-1">
            <Label htmlFor={f.name}>Title</Label>
            <Input
              id={f.name}
              value={f.state.value}
              onChange={(e) => f.handleChange(e.target.value)}
            />
            <FieldError errors={f.state.meta.errors} />
          </div>
        )}
      </form.Field>

      <div className="grid grid-cols-2 gap-3">
        <form.Field name="kind">
          {(f) => (
            <div className="space-y-1">
              <Label>Kind</Label>
              <Select
                value={f.state.value}
                onValueChange={(v) => f.handleChange(v as SnippetInput["kind"])}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SNIPPET_KINDS.map((k) => (
                    <SelectItem key={k.value} value={k.value}>
                      {k.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </form.Field>
        <form.Field name="language">
          {(f) => (
            <div className="space-y-1">
              <Label htmlFor={f.name}>Language</Label>
              <Input
                id={f.name}
                placeholder="en"
                value={f.state.value}
                onChange={(e) => f.handleChange(e.target.value)}
              />
              <FieldError errors={f.state.meta.errors} />
            </div>
          )}
        </form.Field>
      </div>

      <form.Field name="content">
        {(f) => (
          <div className="space-y-1">
            <Label htmlFor={f.name}>Content (Markdown)</Label>
            <div className="grid md:grid-cols-2 gap-3">
              <Textarea
                id={f.name}
                rows={10}
                value={f.state.value}
                onChange={(e) => f.handleChange(e.target.value)}
                className="font-mono text-sm"
              />
              <div className="border rounded-md p-3 min-h-60 bg-muted/20">
                <MarkdownPreview source={f.state.value} />
              </div>
            </div>
            <FieldError errors={f.state.meta.errors} />
          </div>
        )}
      </form.Field>

      {/* --- career-DB links: this is the "connect it to the CV" ask --- */}
      <form.Field name="job">
        {(f) => (
          <div className="space-y-1">
            <Label>Job</Label>
            <JobPicker value={f.state.value} onChange={f.handleChange} />
          </div>
        )}
      </form.Field>

      <form.Field name="project">
        {(f) => (
          <div className="space-y-1">
            <Label>Project</Label>
            <ProjectPicker value={f.state.value} onChange={f.handleChange} />
          </div>
        )}
      </form.Field>

      <form.Field name="skills">
        {(f) => (
          <div className="space-y-1">
            <Label>Skills</Label>
            <SkillPicker
              value={f.state.value}
              onChange={f.handleChange}
              autoAddPrerequisites
            />
          </div>
        )}
      </form.Field>

      <form.Field name="domains">
        {(f) => (
          <div className="space-y-1">
            <Label>Domains</Label>
            <DomainPicker value={f.state.value} onChange={f.handleChange} />
          </div>
        )}
      </form.Field>

      <form.Field name="is_active">
        {(f) => (
          <label className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={f.state.value}
              onCheckedChange={(v) => f.handleChange(!!v)}
            />
            Active (available to the cover-letter selector)
          </label>
        )}
      </form.Field>

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={create.isPending || update.isPending}>
          {row ? "Save" : "Create"}
        </Button>
      </div>
    </form>
  );
}

function FieldError({ errors }: { errors: Array<unknown> }) {
  const msg = errors.find(
    (e) => typeof e === "string" || (e && typeof e === "object"),
  );
  if (!msg) return null;
  const text =
    typeof msg === "string"
      ? msg
      : ((msg as { message?: string }).message ??
        (msg as { fields?: Record<string, string> }).fields?.[
          Object.keys(
            (msg as { fields?: Record<string, string> }).fields ?? {},
          )[0]
        ] ??
        "Invalid");
  return <p className="text-xs text-destructive">{String(text)}</p>;
}
```

> The `FieldError` helper is copied verbatim from `jobs.tsx` (it's a private component there, not
> exported). If you'd rather de-duplicate, lift it into a shared file — out of scope for this guide.

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
  { to: "/cv/snippets", label: "Snippets" },
] as const;
```

### 6. `frontend/src/routes/_authenticated/cv/index.tsx` — add the overview card

Append to `SECTIONS`:

```ts
    {
      key: "snippets",
      label: "Snippets",
      to: "/cv/snippets",
      url: "/api/jac/resume-snippets/",
    },
```

(`key: "snippets"` is now a valid `ResourceKey` thanks to step 1. The card's count query uses
`-updated_at`, which the viewset's `ordering_fields` supports.)

---

## Tests

Pre-written, on disk, **red** until the code above exists:

- `frontend/tests/lib/snippet-form.test.ts` — unit tests for the pure `snippet-form.ts` helpers:
  `SNIPPET_KINDS` covers exactly the five backend kinds; `emptySnippetInput()` returns blank
  defaults **with fresh (non-shared) arrays**; `snippetToInput(null)` == empty and
  `snippetToInput(row)` copies every field (arrays copied, not aliased); `toSnippetPayload` trims the
  title, lower-cases + defaults the language (`""`/`"  "` → `"en"`, `"DE"` → `"de"`), and passes
  `job`/`project`/`is_active` through unchanged; `snippetSchema` accepts a valid input and rejects an
  empty title, empty content, and an unknown kind.

Run:

```bash
cd frontend && npx vitest run tests/lib/snippet-form.test.ts
```

It will **fail to resolve `@/lib/snippet-form`** until step 2 is typed — that's the red state. Once
`snippet-form.ts` exists and matches the guide, it goes green.

The route, picker, and query-registry wiring are exercised by the manual verification below (the
frontend test harness deliberately covers pure `lib/` logic only — components/hooks are deferred
until styling settles; see `[[frontend-test-layout]]`).

---

## Verification

1. **Tests green:** `cd frontend && npx vitest run tests/lib/snippet-form.test.ts` → all pass.
2. **Type/build check:** `cd frontend && npx tsc -b` → no errors (confirms the `ResumeSnippetRow`
   type, `R` registry entry, widened `useBulkPatchDomains`, and `routeTree.gen.ts` all line up).
3. **Route generation:** start the dev server (`npm run dev`); the `tanstackRouter` plugin
   regenerates `routeTree.gen.ts` with the `/cv/snippets` route. Confirm a **Snippets** tab appears
   in the `/cv` nav and an overview card on `/cv`.
4. **Create + link:** on `/cv/snippets` click **New**, fill title + content, pick a **Kind**, set a
   language, then link a **Job**, **Project**, **Skills**, and **Domains** from the existing career
   DB, tick **Active**, and Create. The row appears in the table.
   - Sanity-check the write in the API: `GET /api/jac/resume-snippets/` (logged in) returns the row
     with the `job`/`project`/`skills`/`domains` ids you selected.
5. **Edit round-trips:** reopen the row — every field (incl. the linked job/project/skills/domains)
   is pre-filled. Change the kind, Save, confirm it sticks.
6. **Filters:** filter by **Kind**, by **Active/Inactive**, and by **Domain**; each narrows the list.
   Search by title/content text.
7. **Bulk:** select rows → bulk **Delete** and bulk **assign domains** both work (the bulk bar).
8. **Scope:** the job/project/skill/domain pickers only offer _your own_ rows (server-side scoping) —
   no cross-user leakage.

```

```
