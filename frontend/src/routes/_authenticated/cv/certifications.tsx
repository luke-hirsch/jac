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
  type CertificationRow,
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
import { SkillPicker } from "@/components/cv/skill-picker";
import { OptionalDateField } from "@/components/cv/optional-date-field";
import { MarkdownPreview } from "@/components/markdown-preview";
import { BulkBar } from "@/components/cv/bulk-bar";

const schema = z.object({
  name: z.string().min(1).max(200),
  issuer: z.string().min(1).max(200),
  issued_on: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .or(z.literal("")),
  expires_on: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .or(z.literal("")),
  credential_id: z.string().max(200),
  url: z.string().url().or(z.literal("")),
  description: z.string(),
  skills: z.array(z.number()),
  domains: z.array(z.number()),
});
type CertificationInput = z.infer<typeof schema>;

export const Route = createFileRoute("/_authenticated/cv/certifications")({
  component: CertificationsPage,
});

function CertificationsPage() {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search);

  const [selection, setSelection] = useState<RowSelectionState>({});
  const [editing, setEditing] = useState<CertificationRow | null>(null);
  const [open, setOpen] = useState(false);

  const list = useList<CertificationRow>("certifications", {
    search: debouncedSearch,
  });

  const destroy = useDestroy("certifications");
  const bulkDestroy = useBulkDestroy("certifications");

  const columns = useMemo(
    () =>
      buildColumns({
        onEdit: (row) => {
          setEditing(row);
          setOpen(true);
        },
        onDelete: (row) => {
          if (!confirm(`Delete "${row.name}" at ${row.issuer}?`)) return;
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
    <SectionPage<CertificationRow>
      title="Certifications"
      description="Professional certifications and credentials."
      search={search}
      onSearchChange={setSearch}
      table={
        <>
          <BulkBar
            count={selectedIds.length}
            onDelete={() => {
              if (!confirm(`Delete ${selectedIds.length} certifications?`))
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
                      No certifications yet — click <strong>New</strong> to add
                      one.
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
      editor={(row, close) => <CertificationEditor row={row} onClose={close} />}
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

const col = createColumnHelper<CertificationRow>();

function buildColumns(opts: {
  onEdit: (r: CertificationRow) => void;
  onDelete: (r: CertificationRow) => void;
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
    col.accessor("issuer", { header: "Issuer" }),

    col.accessor("issued_on", { header: "Issued on" }),
    col.accessor("expires_on", {
      header: "Expires on",
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

function CertificationEditor({
  row,
  onClose,
}: {
  row: CertificationRow | null;
  onClose: () => void;
}) {
  const create = useCreate<CertificationRow>("certifications");
  const update = useUpdate<CertificationRow>("certifications");
  const initial: CertificationInput = row
    ? {
        name: row.name,
        issuer: row.issuer,
        issued_on: row.issued_on ?? "",
        expires_on: row.expires_on ?? "",
        credential_id: row.credential_id,
        url: row.url,
        description: row.description,
        skills: row.skills,
        domains: row.domains,
      }
    : {
        name: "",
        issuer: "",
        issued_on: "",
        expires_on: "",
        credential_id: "",
        url: "",
        description: "",
        skills: [],
        domains: [],
      };

  const form = useForm({
    defaultValues: initial,
    validators: { onChange: zodValidator(schema) },
    onSubmit: async ({ value }) => {
      // DRF's DateField rejects "" for nullable dates — send null instead.
      const body = {
        ...value,
        issued_on: value.issued_on || null,
        expires_on: value.expires_on || null,
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
            <Label htmlFor={f.name}>Name</Label>
            <Input
              id={f.name}
              value={f.state.value}
              onChange={(e) => f.handleChange(e.target.value)}
            />
            <FieldError errors={f.state.meta.errors} />
          </div>
        )}
      </form.Field>

      <form.Field name="issuer">
        {(f) => (
          <div className="space-y-1">
            <Label htmlFor={f.name}>Issuer</Label>
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
        <form.Field name="issued_on">
          {(f) => (
            <div className="space-y-1">
              <Label htmlFor={f.name}>Issued on</Label>
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
        <form.Field name="expires_on">
          {(f) => (
            <OptionalDateField
              id={f.name}
              label="Expires on"
              noneLabel="Never expires"
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
      <form.Field name="credential_id">
        {(f) => (
          <div className="space-y-1">
            <Label htmlFor={f.name}>Credential ID</Label>
            <Input
              id={f.name}
              value={f.state.value}
              onChange={(e) => f.handleChange(e.target.value)}
            />
            <FieldError errors={f.state.meta.errors} />
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
            <Label>Skills it evidences</Label>
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

      <form.Subscribe
        selector={(s) => ({
          submitted: s.submissionAttempts,
          canSubmit: s.canSubmit,
        })}
      >
        {({ submitted, canSubmit }) =>
          submitted > 0 && !canSubmit ? (
            <p className="text-sm text-destructive">
              Some fields are invalid — check the highlighted fields above.
            </p>
          ) : null
        }
      </form.Subscribe>

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
