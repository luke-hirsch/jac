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
  type JobRow,
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
import { Pagination } from "@/components/cv/pagination";
import { DomainPicker } from "@/components/cv/domain-picker";
import { DomainFilter } from "@/components/cv/domain-filter";
import { LocationPicker } from "@/components/cv/location-picker";
import { SkillPicker } from "@/components/cv/skill-picker";
import { OptionalDateField } from "@/components/cv/optional-date-field";
import { MarkdownPreview } from "@/components/markdown-preview";
import { BulkBar } from "@/components/cv/bulk-bar";
import { FavouriteField } from "@/components/cv/favourite-field";
import { favouriteColumn } from "@/components/cv/favourite-column";
import { LineSaveHint } from "@/components/cv/line-save-hint";
import { useLineSave } from "@/components/cv/use-line-save";
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
  ended: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/, "YYYY-MM-DD")
    .or(z.literal("")),
  url: z.string().url().or(z.literal("")),
  description: z.string(),
  location: z.number().nullable(),
  domains: z.array(z.number()),
  skills: z.array(z.number()),
  favourite: z.boolean(),
});

type JobInput = z.infer<typeof schema>;

export const Route = createFileRoute("/_authenticated/cv/jobs")({
  component: JobsPage,
});

function JobsPage() {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search);
  const [jobType, setJobType] = useState<JobRow["job_type"] | "">("");
  const [domain, setDomain] = useState<number | "">("");
  const [selection, setSelection] = useState<RowSelectionState>({});
  const [editing, setEditing] = useState<JobRow | null>(null);
  const [open, setOpen] = useState(false);
  const [favFirst, setFavFirst] = useState(false);

  const list = usePagedList<JobRow>("jobs", {
    search: debouncedSearch,
    filters: { job_type: jobType || undefined, domains: domain || undefined },
    ordering: favFirst ? "-favourite,-started" : undefined,
  });

  const destroy = useDestroy("jobs");
  const bulkDestroy = useBulkDestroy("jobs");
  const bulkDomains = useBulkPatchDomains("jobs");

  const columns = useMemo(
    () =>
      buildColumns({
        favFirst,
        onToggleFav: () => setFavFirst((v) => !v),
        onEdit: (row) => {
          setEditing(row);
          setOpen(true);
        },
        onDelete: (row) => {
          if (!confirm(`Delete "${row.title}" at ${row.company}?`)) return;
          destroy.mutate(row.id, {
            onSuccess: () => toast.success("Deleted"),
            onError: () => toast.error("Delete failed"),
          });
        },
      }),
    [destroy, favFirst],
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
    <SectionPage<JobRow>
      title="Jobs"
      description="Employment + contract history. Started date is required."
      search={search}
      onSearchChange={setSearch}
      filters={
        <>
          <Select
            value={jobType || "all"}
            onValueChange={(v) =>
              setJobType(v === "all" ? "" : (v as JobRow["job_type"]))
            }
          >
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All types</SelectItem>
              {JOB_TYPES.map((t) => (
                <SelectItem key={t.value} value={t.value}>
                  {t.label}
                </SelectItem>
              ))}
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
              if (!confirm(`Delete ${selectedIds.length} jobs?`)) return;
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
                      No jobs yet — click <strong>New</strong> to add one.
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
      editor={(row, close) => <JobEditor row={row} onClose={close} />}
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

const col = createColumnHelper<JobRow>();

function buildColumns(opts: {
  favFirst: boolean;
  onToggleFav: () => void;
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
    favouriteColumn<JobRow>({
      active: opts.favFirst,
      onToggle: opts.onToggleFav,
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

function JobEditor({
  row,
  onClose,
}: {
  row: JobRow | null;
  onClose: () => void;
}) {
  const create = useCreate<JobRow>("jobs");
  const update = useUpdate<JobRow>("jobs");
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
        favourite: row.favourite,
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
        favourite: false,
      };

  // Edit mode saves line by line — each field PATCHes as you leave it, with
  // its own saved/error hint. Create still submits the whole form below.
  const line = useLineSave<JobRow>("jobs", row?.id ?? null, initial);

  const form = useForm({
    defaultValues: initial,
    validators: { onChange: zodValidator(schema) },
    onSubmit: async ({ value }) => {
      // DRF's DateField rejects "" for nullable dates — send null instead.
      const body = { ...value, ended: value.ended || null };
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
              onBlur={() => line.save(f.name, f.state.value, f.state.meta.errors)}
            />
            <FieldError errors={f.state.meta.errors} />
            <LineSaveHint s={line.fields[f.name]} />
          </div>
        )}
      </form.Field>

      <form.Field name="company">
        {(f) => (
          <div className="space-y-1">
            <Label htmlFor={f.name}>Company</Label>
            <Input
              id={f.name}
              value={f.state.value}
              onChange={(e) => f.handleChange(e.target.value)}
              onBlur={() => line.save(f.name, f.state.value, f.state.meta.errors)}
            />
            <FieldError errors={f.state.meta.errors} />
            <LineSaveHint s={line.fields[f.name]} />
          </div>
        )}
      </form.Field>

      <form.Field name="job_type">
        {(f) => (
          <div className="space-y-1">
            <Label>Type</Label>
            <Select
              value={f.state.value}
              onValueChange={(v) => {
                f.handleChange(v as JobInput["job_type"]);
                line.save(f.name, v);
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {JOB_TYPES.map((t) => (
                  <SelectItem key={t.value} value={t.value}>
                    {t.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <LineSaveHint s={line.fields[f.name]} />
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
                onBlur={() =>
                  line.save(f.name, f.state.value, f.state.meta.errors)
                }
              />
              <FieldError errors={f.state.meta.errors} />
              <LineSaveHint s={line.fields[f.name]} />
            </div>
          )}
        </form.Field>
        <form.Field name="ended">
          {(f) => (
            <OptionalDateField
              id={f.name}
              label="Ended"
              noneLabel="Current role"
              value={f.state.value}
              onChange={(v) => {
                f.handleChange(v);
                // "" = current role; DRF wants null for the empty date.
                line.save(f.name, v || null, f.state.meta.errors);
              }}
              error={
                <>
                  <FieldError errors={f.state.meta.errors} />
                  <LineSaveHint s={line.fields[f.name]} />
                </>
              }
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
              onBlur={() => line.save(f.name, f.state.value, f.state.meta.errors)}
            />
            <FieldError errors={f.state.meta.errors} />
            <LineSaveHint s={line.fields[f.name]} />
          </div>
        )}
      </form.Field>

      <form.Field name="location">
        {(f) => (
          <div className="space-y-1">
            <Label>Location</Label>
            <LocationPicker
              value={f.state.value}
              onChange={(v) => {
                f.handleChange(v);
                line.save(f.name, v);
              }}
            />
            <LineSaveHint s={line.fields[f.name]} />
          </div>
        )}
      </form.Field>

      <form.Field name="domains">
        {(f) => (
          <div className="space-y-1">
            <Label>Domains</Label>
            <DomainPicker
              value={f.state.value}
              onChange={(v) => {
                f.handleChange(v);
                line.save(f.name, v);
              }}
            />
            <LineSaveHint s={line.fields[f.name]} />
          </div>
        )}
      </form.Field>

      <form.Field name="skills">
        {(f) => (
          <div className="space-y-1">
            <Label>Skills</Label>
            <SkillPicker
              value={f.state.value}
              onChange={(v) => {
                f.handleChange(v);
                line.save(f.name, v);
              }}
              autoAddPrerequisites
            />
            <LineSaveHint s={line.fields[f.name]} />
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
                onBlur={() =>
                  line.save(f.name, f.state.value, f.state.meta.errors)
                }
                className="font-mono text-sm"
              />
              <div className="border rounded-md p-3 min-h-[240px] bg-muted/20">
                <MarkdownPreview source={f.state.value} />
              </div>
            </div>
            <LineSaveHint s={line.fields[f.name]} />
          </div>
        )}
      </form.Field>

      <form.Field name="favourite">
        {(f) => (
          <div className="space-y-1">
            <FavouriteField
              checked={f.state.value}
              onChange={(v) => {
                f.handleChange(v);
                line.save(f.name, v);
              }}
              hint="Pinned entries get a small ranking boost (max 4 jobs)."
            />
            <LineSaveHint s={line.fields[f.name]} />
          </div>
        )}
      </form.Field>

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        {row ? (
          <Button type="button" onClick={onClose}>
            Done
          </Button>
        ) : (
          <Button type="submit" disabled={create.isPending}>
            Create
          </Button>
        )}
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
