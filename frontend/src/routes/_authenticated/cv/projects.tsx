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
  type ProjectRow,
} from "@/lib/queries/jac";
import { useDebounced } from "@/lib/use-debounced";
import { zodValidator, z } from "@/lib/form";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";

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
import { SkillPicker } from "@/components/cv/skill-picker";
import { JobPicker } from "@/components/cv/job-picker";
import { OptionalDateField } from "@/components/cv/optional-date-field";
import { MarkdownPreview } from "@/components/markdown-preview";
import { BulkBar } from "@/components/cv/bulk-bar";

const schema = z.object({
  name: z.string().min(1).max(200),
  started: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .or(z.literal("")),
  ended: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .or(z.literal("")),
  url: z.string().url().or(z.literal("")),
  description: z.string(),
  location: z.number().nullable(),
  job: z.number().nullable(),
  skills: z.array(z.number()),
  domains: z.array(z.number()),
});
type ProjectInput = z.infer<typeof schema>;

export const Route = createFileRoute("/_authenticated/cv/projects")({
  component: ProjectPage,
});

function ProjectPage() {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search);
  const [selection, setSelection] = useState<RowSelectionState>({});
  const [editing, setEditing] = useState<ProjectRow | null>(null);
  const [open, setOpen] = useState(false);

  const list = useList<ProjectRow>("projects", {
    search: debouncedSearch,
  });

  const destroy = useDestroy("projects");
  const bulkDestroy = useBulkDestroy("projects");
  const bulkDomains = useBulkPatchDomains("projects");

  const columns = useMemo(
    () =>
      buildColumns({
        onEdit: (row) => {
          setEditing(row);
          setOpen(true);
        },
        onDelete: (row) => {
          if (!confirm(`Delete "${row.name}"?`)) return;
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
    <SectionPage<ProjectRow>
      title="Projects"
      description="Project history. Started date is required."
      search={search}
      onSearchChange={setSearch}
      table={
        <>
          <BulkBar
            count={selectedIds.length}
            onDelete={() => {
              if (!confirm(`Delete ${selectedIds.length} projects?`)) return;
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
                      No projects yet — click <strong>New</strong> to add one.
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
      editor={(row, close) => <ProjectEditor row={row} onClose={close} />}
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

const col = createColumnHelper<ProjectRow>();

function buildColumns(opts: {
  onEdit: (r: ProjectRow) => void;
  onDelete: (r: ProjectRow) => void;
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
    col.accessor("name", { header: "Name" }),

    col.accessor("started", { header: "From" }),
    col.accessor("ended", {
      header: "To",
      cell: ({ getValue }) =>
        getValue() || <span className="text-muted-foreground">present</span>,
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

function ProjectEditor({
  row,
  onClose,
}: {
  row: ProjectRow | null;
  onClose: () => void;
}) {
  const create = useCreate<ProjectRow>("projects");
  const update = useUpdate<ProjectRow>("projects");
  const initial: ProjectInput = row
    ? {
        name: row.name,
        started: row.started ?? new Date().toISOString().slice(0, 10),
        ended: row.ended ?? "",
        url: row.url ?? "",
        description: row.description,
        location: row.location,
        job: row.job,
        domains: row.domains,
        skills: row.skills,
      }
    : {
        name: "",
        started: new Date().toISOString().slice(0, 10),
        ended: "",
        url: "",
        description: "",
        location: null,
        job: null,
        domains: [],
        skills: [],
      };

  const form = useForm({
    defaultValues: initial,
    validators: { onChange: zodValidator(schema) },
    onSubmit: async ({ value }) => {
      // DRF's DateField rejects "" for nullable dates — send null instead.
      const body = {
        ...value,
        started: value.started || null,
        ended: value.ended || null,
      };
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
      <form.Field name="name">
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
        <form.Field name="started">
          {(f) => (
            <div className="space-y-1">
              <Label htmlFor={f.name}>Started</Label>
              <Input
                id={f.name}
                type="date"
                value={f.state.value}
                onChange={(e) => f.handleChange(e.target.value)}
              />
              <FieldError errors={f.state.meta.errors} />
            </div>
          )}
        </form.Field>
        <form.Field name="ended">
          {(f) => (
            <OptionalDateField
              id={f.name}
              label="Ended"
              noneLabel="Ongoing"
              value={f.state.value}
              onChange={f.handleChange}
              error={<FieldError errors={f.state.meta.errors} />}
            />
          )}
        </form.Field>
      </div>

      <form.Field name="url">
        {(f) => (
          <div className="space-y-1">
            <Label htmlFor={f.name}>URL</Label>
            <Input
              id={f.name}
              type="url"
              value={f.state.value}
              onChange={(e) => f.handleChange(e.target.value)}
            />
            <FieldError errors={f.state.meta.errors} />
          </div>
        )}
      </form.Field>

      <form.Field name="location">
        {(f) => (
          <div className="space-y-1">
            <Label>Location</Label>
            <LocationPicker value={f.state.value} onChange={f.handleChange} />
          </div>
        )}
      </form.Field>

      <form.Field name="job">
        {(f) => (
          <div className="space-y-1">
            <Label>Done at (job)</Label>
            <JobPicker value={f.state.value} onChange={f.handleChange} />
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

      <form.Field name="skills">
        {(f) => (
          <div className="space-y-1">
            <Label>Skills</Label>
            <SkillPicker value={f.state.value} onChange={f.handleChange} />
          </div>
        )}
      </form.Field>

      <form.Field name="description">
        {(f) => (
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
