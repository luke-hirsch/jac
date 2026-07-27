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
  type EducationRow,
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
import { Pagination } from "@/components/cv/pagination";
import { LocationPicker } from "@/components/cv/location-picker";
import { DomainPicker } from "@/components/cv/domain-picker";
import { DomainFilter } from "@/components/cv/domain-filter";
import { SkillPicker } from "@/components/cv/skill-picker";
import { OptionalDateField } from "@/components/cv/optional-date-field";
import { MarkdownPreview } from "@/components/markdown-preview";
import { BulkBar } from "@/components/cv/bulk-bar";
import { FavouriteField } from "@/components/cv/favourite-field";
import { favouriteColumn } from "@/components/cv/favourite-column";
import { LineSaveHint } from "@/components/cv/line-save-hint";
import { useLineSave } from "@/components/cv/use-line-save";
import { EntryFilesField } from "@/components/cv/entry-files-field";
import { useUploadAttachment } from "@/lib/queries/attachments";

const schema = z.object({
  institution: z.string().min(1).max(200),
  field_of_study: z.string().max(200),
  started: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  ended: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .or(z.literal("")),
  degree: z.string().max(100),
  grade: z.string().max(50),
  description: z.string(),
  location: z.number().nullable(),
  skills: z.array(z.number()),
  domains: z.array(z.number()),
  favourite: z.boolean(),
});

type EducationInput = z.infer<typeof schema>;

export const Route = createFileRoute("/_authenticated/cv/education")({
  component: EducationPage,
});

function EducationPage() {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search);
  const [domain, setDomain] = useState<number | "">("");

  const [selection, setSelection] = useState<RowSelectionState>({});
  const [editing, setEditing] = useState<EducationRow | null>(null);
  const [open, setOpen] = useState(false);
  const [favFirst, setFavFirst] = useState(false);

  const list = usePagedList<EducationRow>("education", {
    search: debouncedSearch,
    filters: { domains: domain || undefined },
    ordering: favFirst ? "-favourite,-started" : undefined,
  });

  const destroy = useDestroy("education");
  const bulkDestroy = useBulkDestroy("education");

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
          if (!confirm(`Delete "${row.field_of_study}" at ${row.institution}?`))
            return;
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
    <SectionPage<EducationRow>
      title="Education"
      description="Educational background and qualifications."
      search={search}
      onSearchChange={setSearch}
      filters={<DomainFilter value={domain} onChange={setDomain} />}
      table={
        <>
          <BulkBar
            count={selectedIds.length}
            onDelete={() => {
              if (!confirm(`Delete ${selectedIds.length} education entries?`))
                return;
              bulkDestroy.mutate(selectedIds, {
                onSuccess: () => {
                  toast.success("Deleted");
                  setSelection({});
                },
                onError: () => toast.error("Bulk delete failed"),
              });
            }}
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
                      No education entries yet — click <strong>New</strong> to
                      add one.
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
      editor={(row, close) => <EducationEditor row={row} onClose={close} />}
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

const col = createColumnHelper<EducationRow>();

function buildColumns(opts: {
  favFirst: boolean;
  onToggleFav: () => void;
  onEdit: (r: EducationRow) => void;
  onDelete: (r: EducationRow) => void;
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
    favouriteColumn<EducationRow>({
      active: opts.favFirst,
      onToggle: opts.onToggleFav,
    }),
    col.accessor("field_of_study", { header: "Field of Study" }),
    col.accessor("institution", { header: "Institution" }),
    col.accessor("degree", { header: "Degree" }),
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

function EducationEditor({
  row,
  onClose,
}: {
  row: EducationRow | null;
  onClose: () => void;
}) {
  const create = useCreate<EducationRow>("education");
  const update = useUpdate<EducationRow>("education");
  const uploadAttachment = useUploadAttachment();
  const [pendingFile, setPendingFile] = useState<File | null>(null);

  const initial: EducationInput = row
    ? {
        field_of_study: row.field_of_study,
        institution: row.institution,
        degree: row.degree ?? "",
        grade: row.grade ?? "",
        started: row.started,
        ended: row.ended ?? "",
        description: row.description,
        location: row.location,
        skills: row.skills,
        domains: row.domains,
        favourite: row.favourite,
      }
    : {
        field_of_study: "",
        institution: "",
        degree: "",
        grade: "",
        started: "",
        ended: "",
        description: "",
        location: null,
        skills: [],
        domains: [],
        favourite: false,
      };

  // Edit mode saves line by line (create still submits the whole form).
  const line = useLineSave<EducationRow>("education", row?.id ?? null, initial);

  const form = useForm({
    defaultValues: initial,
    validators: { onChange: zodValidator(schema) },
    onSubmit: async ({ value }) => {
      // DRF's DateField rejects "" for the nullable `ended` — send null.
      const body = { ...value, ended: value.ended || null };
      try {
        if (row) {
          await update.mutateAsync({ id: row.id, body });
        } else {
          const created = await create.mutateAsync(body);
          if (pendingFile)
            await uploadAttachment.mutateAsync({
              file: pendingFile,
              education: created.id,
            });
        }
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
      <form.Field name="field_of_study">
        {(f) => (
          <div className="space-y-1">
            <Label htmlFor={f.name}>Field of Study</Label>
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

      <form.Field name="institution">
        {(f) => (
          <div className="space-y-1">
            <Label htmlFor={f.name}>Institution</Label>
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

      <form.Field name="degree">
        {(f) => (
          <div className="space-y-1">
            <Label htmlFor={f.name}>Degree</Label>
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

      <form.Field name="grade">
        {(f) => (
          <div className="space-y-1">
            <Label htmlFor={f.name}>Grade</Label>
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
              noneLabel="Ongoing"
              value={f.state.value}
              onChange={(v) => {
                f.handleChange(v);
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

      <EntryFilesField
        entryType="education"
        entryId={row?.id ?? null}
        pending={pendingFile}
        onPendingChange={setPendingFile}
      />

      <form.Field name="favourite">
        {(f) => (
          <div className="space-y-1">
            <FavouriteField
              checked={f.state.value}
              onChange={(v) => {
                f.handleChange(v);
                line.save(f.name, v);
              }}
              hint="Pinned entries get a small ranking boost (max 2 educations)."
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
