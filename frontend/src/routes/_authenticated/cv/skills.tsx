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
import {
  ArrowDown,
  ArrowDownUp,
  ArrowUp,
  CircleDot,
  Pencil,
  Table2,
  Trash2,
} from "lucide-react";
import {
  usePagedList,
  useFullList,
  useCreate,
  useUpdate,
  useDestroy,
  useBulkDestroy,
  useBulkPatchDomains,
  type SkillRow,
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
import { SkillPicker } from "@/components/cv/skill-picker";
import { CertificationPicker } from "@/components/cv/certification-picker";
import { SkillBubbles } from "@/components/cv/skill-bubbles";

import { MarkdownPreview } from "@/components/markdown-preview";
import { BulkBar } from "@/components/cv/bulk-bar";
import { FavouriteField } from "@/components/cv/favourite-field";
import { favouriteColumn } from "@/components/cv/favourite-column";
import { LineSaveHint } from "@/components/cv/line-save-hint";
import { useLineSave } from "@/components/cv/use-line-save";
const PROFICIENCY: { value: SkillRow["proficiency"]; label: string }[] = [
  { value: "beginner", label: "Beginner" },
  { value: "intermediate", label: "Intermediate" },
  { value: "advanced", label: "Advanced" },
  { value: "expert", label: "Expert" },
];
const CATEGORIES: { value: SkillRow["category"]; label: string }[] = [
  { value: "technical", label: "Technical" },
  { value: "soft", label: "Soft" },
  { value: "domain", label: "Domain" },
  { value: "other", label: "Other" },
];

const schema = z.object({
  name: z.string().min(1).max(200),
  proficiency: z.enum(["beginner", "intermediate", "advanced", "expert"]),
  category: z.enum(["technical", "soft", "domain", "other"]),
  domains: z.array(z.number()),
  related_skills: z.array(z.number()),
  builds_on: z.array(z.number()),
  first_used: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .or(z.literal("")),
  certification: z.number().nullable(),
  years_of_experience_override: z.number().int().min(0).nullable(),
  description: z.string(),
  favourite: z.boolean(),
});

type SkillInput = z.infer<typeof schema>;

export const Route = createFileRoute("/_authenticated/cv/skills")({
  component: SkillPage,
});

function SkillPage() {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search);
  const [proficiency, setProficiency] = useState<SkillRow["proficiency"] | "">(
    "",
  );
  const [category, setCategory] = useState<SkillRow["category"] | "">("");
  const [domain, setDomain] = useState<number | "">("");
  // "" = default (name), "experience_since" = most experience first,
  // "-experience_since" = least first.
  const [expSort, setExpSort] = useState<
    "" | "experience_since" | "-experience_since"
  >("");
  const [favFirst, setFavFirst] = useState(false);
  const [selection, setSelection] = useState<RowSelectionState>({});
  const [editing, setEditing] = useState<SkillRow | null>(null);
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"table" | "bubbles">("table");

  const filters = { proficiency, category, domains: domain || undefined };

  const list = usePagedList<SkillRow>("skills", {
    search: debouncedSearch,
    filters,
    // Favourites-first prepends `-favourite`, keeping the active (or default name) sort
    // as the secondary key.
    ordering: favFirst
      ? `-favourite,${expSort || "name"}`
      : expSort || undefined,
  });

  // The bubble cloud needs the whole filtered set (no paging); only fetched
  // while that view is active.
  const cloud = useFullList<SkillRow>(
    "skills",
    { search: debouncedSearch, filters },
    { enabled: view === "bubbles" },
  );

  const openEditor = (row: SkillRow) => {
    setEditing(row);
    setOpen(true);
  };

  const cycleExpSort = () =>
    setExpSort((o) =>
      o === ""
        ? "experience_since"
        : o === "experience_since"
          ? "-experience_since"
          : "",
    );

  const destroy = useDestroy("skills");
  const bulkDestroy = useBulkDestroy("skills");
  const bulkDomains = useBulkPatchDomains("skills");

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
        expSort,
        onToggleExpSort: cycleExpSort,
        favFirst,
        onToggleFav: () => setFavFirst((v) => !v),
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [destroy, expSort, favFirst],
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
    <SectionPage<SkillRow>
      title="Skills"
      description="Technical, soft, and domain skills with self-assessed proficiency."
      search={search}
      onSearchChange={setSearch}
      filters={
        <>
          <div className="inline-flex rounded-md border p-0.5">
            <Button
              type="button"
              size="sm"
              variant={view === "table" ? "secondary" : "ghost"}
              className="gap-1.5"
              onClick={() => setView("table")}
            >
              <Table2 className="size-4" /> Table
            </Button>
            <Button
              type="button"
              size="sm"
              variant={view === "bubbles" ? "secondary" : "ghost"}
              className="gap-1.5"
              onClick={() => setView("bubbles")}
            >
              <CircleDot className="size-4" /> Bubbles
            </Button>
          </div>
          <Select
            value={proficiency || "all"}
            onValueChange={(v) =>
              setProficiency(v === "all" ? "" : (v as SkillRow["proficiency"]))
            }
          >
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All proficiencies</SelectItem>
              {PROFICIENCY.map((p) => (
                <SelectItem key={p.value} value={p.value}>
                  {p.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={category || "all"}
            onValueChange={(v) =>
              setCategory(v === "all" ? "" : (v as SkillRow["category"]))
            }
          >
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All categories</SelectItem>
              {CATEGORIES.map((p) => (
                <SelectItem key={p.value} value={p.value}>
                  {p.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <DomainFilter value={domain} onChange={setDomain} />{" "}
        </>
      }
      table={
        view === "bubbles" ? (
          <SkillBubbles
            skills={cloud.data ?? []}
            loading={cloud.isLoading}
            onSelect={openEditor}
          />
        ) : (
        <>
          <BulkBar
            count={selectedIds.length}
            onDelete={() => {
              if (!confirm(`Delete ${selectedIds.length} skills?`)) return;
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
                      No skills yet — click <strong>New</strong> to add one.
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
        )
      }
      pagination={
        view === "table" ? (
          <Pagination
            page={list.page}
            count={list.data?.count ?? 0}
            onPageChange={list.setPage}
          />
        ) : undefined
      }
      editor={(row, close) => <SkillEditor row={row} onClose={close} />}
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

const col = createColumnHelper<SkillRow>();

function buildColumns(opts: {
  onEdit: (r: SkillRow) => void;
  onDelete: (r: SkillRow) => void;
  expSort: "" | "experience_since" | "-experience_since";
  onToggleExpSort: () => void;
  favFirst: boolean;
  onToggleFav: () => void;
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
    favouriteColumn<SkillRow>({
      active: opts.favFirst,
      onToggle: opts.onToggleFav,
    }),
    col.accessor("name", { header: "Name" }),

    col.accessor("proficiency", {
      header: "Proficiency",
      cell: ({ getValue }) => (
        <Badge variant="outline">
          {PROFICIENCY.find((t) => t.value === getValue())?.label ?? getValue()}
        </Badge>
      ),
    }),
    col.accessor("category", {
      header: "Category",
      cell: ({ getValue }) => (
        <Badge variant="outline">
          {CATEGORIES.find((t) => t.value === getValue())?.label ?? getValue()}
        </Badge>
      ),
    }),
    col.accessor("years_of_experience", {
      header: () => (
        <button
          type="button"
          onClick={opts.onToggleExpSort}
          className="flex items-center gap-1 hover:text-foreground"
        >
          Experience
          {opts.expSort === "experience_since" ? (
            <ArrowDown className="size-3" />
          ) : opts.expSort === "-experience_since" ? (
            <ArrowUp className="size-3" />
          ) : (
            <ArrowDownUp className="size-3 opacity-50" />
          )}
        </button>
      ),
      cell: ({ getValue }) => {
        const years = getValue();
        return years == null ? "—" : `${years} yr${years === 1 ? "" : "s"}`;
      },
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

function SkillEditor({
  row,
  onClose,
}: {
  row: SkillRow | null;
  onClose: () => void;
}) {
  const create = useCreate<SkillRow>("skills");
  const update = useUpdate<SkillRow>("skills");
  const initial: SkillInput = row
    ? {
        name: row.name,
        proficiency: row.proficiency,
        category: row.category,
        domains: row.domains,
        related_skills: row.related_skills,
        builds_on: row.builds_on,
        first_used: row.first_used ?? "",
        certification: row.certification,
        years_of_experience_override: row.years_of_experience_override,
        description: row.description,
        favourite: row.favourite,
      }
    : {
        name: "",
        proficiency: "beginner",
        category: "technical",
        domains: [],
        related_skills: [],
        builds_on: [],
        first_used: "",
        certification: null,
        years_of_experience_override: null,
        description: "",
        favourite: false,
      };

  // Edit mode saves line by line (create still submits the whole form).
  const line = useLineSave<SkillRow>("skills", row?.id ?? null, initial);

  const form = useForm({
    defaultValues: initial,
    validators: { onChange: zodValidator(schema) },
    onSubmit: async ({ value }) => {
      // DRF's DateField rejects "" for the nullable `first_used` — send null.
      const body = { ...value, first_used: value.first_used || null };
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
              onBlur={() => line.save(f.name, f.state.value, f.state.meta.errors)}
            />
            <FieldError errors={f.state.meta.errors} />
            <LineSaveHint s={line.fields[f.name]} />
          </div>
        )}
      </form.Field>

      <form.Field name="proficiency">
        {(f) => (
          <div className="space-y-1">
            <Label>Proficiency</Label>
            <Select
              value={f.state.value}
              onValueChange={(v) => {
                f.handleChange(v as SkillInput["proficiency"]);
                line.save(f.name, v);
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PROFICIENCY.map((t) => (
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
      <form.Field name="category">
        {(f) => (
          <div className="space-y-1">
            <Label>Category</Label>
            <Select
              value={f.state.value}
              onValueChange={(v) => {
                f.handleChange(v as SkillInput["category"]);
                line.save(f.name, v);
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CATEGORIES.map((t) => (
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
        <form.Field name="first_used">
          {(f) => (
            <div className="space-y-1">
              <Label htmlFor={f.name}>First used</Label>
              <Input
                id={f.name}
                type="date"
                value={f.state.value}
                onChange={(e) => f.handleChange(e.target.value)}
                onBlur={() =>
                  line.save(f.name, f.state.value || null, f.state.meta.errors)
                }
              />
              <FieldError errors={f.state.meta.errors} />
              <LineSaveHint s={line.fields[f.name]} />
            </div>
          )}
        </form.Field>
        <form.Field name="years_of_experience_override">
          {(f) => (
            <div className="space-y-1">
              <Label htmlFor={f.name}>Years of experience</Label>
              <Input
                id={f.name}
                type="number"
                min={0}
                placeholder="auto"
                value={f.state.value ?? ""}
                onChange={(e) =>
                  f.handleChange(
                    e.target.value === "" ? null : Number(e.target.value),
                  )
                }
                onBlur={() =>
                  line.save(f.name, f.state.value, f.state.meta.errors)
                }
              />
              <p className="text-xs text-muted-foreground">
                Leave blank to auto-compute from first use / job history
                {row?.years_of_experience != null &&
                  ` (currently ${row.years_of_experience}y)`}
                .
              </p>
              <FieldError errors={f.state.meta.errors} />
              <LineSaveHint s={line.fields[f.name]} />
            </div>
          )}
        </form.Field>
      </div>

      <form.Field name="certification">
        {(f) => (
          <div className="space-y-1">
            <Label>Certification</Label>
            <CertificationPicker
              value={f.state.value}
              onChange={(v) => {
                f.handleChange(v);
                line.save(f.name, v);
              }}
            />
            <FieldError errors={f.state.meta.errors} />
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

      <form.Field name="related_skills">
        {(f) => (
          <div className="space-y-1">
            <Label>Related skills</Label>
            <SkillPicker
              value={f.state.value}
              onChange={(v) => {
                f.handleChange(v);
                line.save(f.name, v);
              }}
              excludeId={row?.id}
            />
            <p className="text-xs text-muted-foreground">
              Sibling skills (symmetric) — adjacent tools you'd group together.
            </p>
            <LineSaveHint s={line.fields[f.name]} />
          </div>
        )}
      </form.Field>

      <form.Field name="builds_on">
        {(f) => (
          <div className="space-y-1">
            <Label>Builds on</Label>
            <SkillPicker
              value={f.state.value}
              onChange={(v) => {
                f.handleChange(v);
                line.save(f.name, v);
              }}
              excludeId={row?.id}
            />
            <p className="text-xs text-muted-foreground">
              Prerequisite skills this one rests on (directed, e.g. DRF builds on
              Django + Python).
            </p>
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
              hint="Pinned entries get a small ranking boost (max 10 skills)."
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
