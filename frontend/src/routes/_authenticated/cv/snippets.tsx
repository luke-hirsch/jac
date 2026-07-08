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
              <div className="border rounded-md p-3 min-h-[240px] bg-muted/20">
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
