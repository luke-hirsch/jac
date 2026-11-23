import { useState } from "react";
import { toast } from "sonner";
import { Pencil, Trash2 } from "lucide-react";
import { drfFieldError } from "@/lib/field-save";
import {
  useDeleteDomain,
  useFullList,
  useMergeDomain,
  useRenameDomain,
  type DomainRow,
  type OnDomainGone,
} from "@/lib/queries/jac";
import {
  deletePrompt,
  mergePrompt,
  mergeTargets,
  ownDomains,
  usesLabel,
} from "@/lib/domains";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { FieldError } from "@/components/field-error";
import { QueryError } from "@/components/query-error";

/**
 * The user's own domains, to fix what the pickers made: rename a typo, merge two that mean the
 * same, delete one. jac's defaults only show up as merge targets. `onRemoved` tells the caller
 * a domain is gone (and where a merge took it), so a filter on it can follow.
 *
 * Merge and delete run from here, not from the row: the row is gone by the time the refetch
 * lands, and a mutation's per-call callbacks (the toasts) don't fire once their component
 * unmounted.
 */
export function DomainManager({
  open,
  onOpenChange,
  onRemoved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onRemoved?: OnDomainGone;
}) {
  const domains = useFullList<DomainRow>("domains", {}, { enabled: open });
  const merge = useMergeDomain(onRemoved);
  const remove = useDeleteDomain(onRemoved);
  const rows = domains.data ?? [];
  const own = ownDomains(rows);

  function onMerge(source: DomainRow, target: DomainRow) {
    if (!confirm(mergePrompt(source, target))) return;
    merge.mutate(
      { id: source.id, into: target.id },
      {
        onSuccess: (merged) => toast.success(`Merged into "${merged.name}"`),
        onError: () => toast.error("Merge failed"),
      },
    );
  }

  function onDelete(domain: DomainRow) {
    if (!confirm(deletePrompt(domain))) return;
    remove.mutate(domain.id, {
      onSuccess: () => toast.success("Deleted"),
      onError: () => toast.error("Delete failed"),
    });
  }

  const busyId = merge.isPending
    ? merge.variables?.id
    : remove.isPending
      ? remove.variables
      : undefined;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="grid max-h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)] gap-4 overflow-hidden sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Your domains</DialogTitle>
          <DialogDescription>
            Rename a typo, merge two that mean the same, or delete one you don't
            need. jac's defaults stay as they are, but you can merge into them.
          </DialogDescription>
        </DialogHeader>
        <div className="overflow-y-auto px-1">
          {domains.isError && !domains.data ? (
            <QueryError
              error={domains.error}
              what="your domains"
              onRetry={() => domains.refetch()}
              retrying={domains.isFetching}
            />
          ) : domains.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : own.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              You haven't added a domain of your own yet.
            </p>
          ) : (
            <ul className="divide-y">
              {own.map((d) => (
                <DomainItem
                  key={d.id}
                  domain={d}
                  rows={rows}
                  busy={busyId === d.id}
                  onMerge={(target) => onMerge(d, target)}
                  onDelete={() => onDelete(d)}
                />
              ))}
            </ul>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function DomainItem({
  domain,
  rows,
  busy,
  onMerge,
  onDelete,
}: {
  domain: DomainRow;
  rows: DomainRow[];
  busy: boolean;
  onMerge: (target: DomainRow) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(domain.name);
  const [error, setError] = useState<string>();
  const rename = useRenameDomain();
  const targets = mergeTargets(rows, domain.id);
  const anyTarget = targets.own.length + targets.defaults.length > 0;

  function stopEditing() {
    setEditing(false);
    setName(domain.name);
    setError(undefined);
  }

  function save() {
    const next = name.trim();
    if (!next || next === domain.name) return stopEditing();
    rename.mutate(
      { id: domain.id, name: next },
      {
        onSuccess: () => {
          setEditing(false);
          setError(undefined);
          toast.success("Renamed");
        },
        onError: (e) => setError(drfFieldError(e, "name")),
      },
    );
  }

  if (editing)
    return (
      <li className="py-2">
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <Input
            aria-label={`New name for ${domain.name}`}
            value={name}
            maxLength={100}
            autoFocus
            onChange={(e) => {
              setName(e.target.value);
              setError(undefined);
            }}
            aria-invalid={error ? true : undefined}
            className="h-9 min-w-40 flex-1"
          />
          <Button type="submit" size="sm" disabled={rename.isPending}>
            {rename.isPending ? "Saving…" : "Save"}
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={stopEditing}>
            Cancel
          </Button>
        </form>
        <FieldError msg={error} />
      </li>
    );

  return (
    <li className="flex items-center gap-2 py-2">
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium">{domain.name}</p>
        <p className="text-xs text-muted-foreground">
          {usesLabel(domain.uses)}
        </p>
      </div>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={`Rename ${domain.name}`}
        disabled={busy}
        onClick={() => {
          setName(domain.name);
          setEditing(true);
        }}
      >
        <Pencil />
      </Button>
      {/* Always back to the placeholder: picking a target asks, then merges. */}
      <Select
        value=""
        disabled={busy || !anyTarget}
        onValueChange={(v) => {
          const target = rows.find((d) => d.id === Number(v));
          if (target) onMerge(target);
        }}
      >
        <SelectTrigger
          size="sm"
          className="w-36"
          aria-label={`Merge ${domain.name} into another domain`}
        >
          <SelectValue placeholder="Merge into…" />
        </SelectTrigger>
        <SelectContent position="popper" className="max-h-72">
          {targets.own.length > 0 && (
            <SelectGroup>
              <SelectLabel>Yours</SelectLabel>
              {targets.own.map((d) => (
                <SelectItem key={d.id} value={String(d.id)}>
                  {d.name}
                </SelectItem>
              ))}
            </SelectGroup>
          )}
          {targets.defaults.length > 0 && (
            <SelectGroup>
              <SelectLabel>jac's defaults</SelectLabel>
              {targets.defaults.map((d) => (
                <SelectItem key={d.id} value={String(d.id)}>
                  {d.name}
                </SelectItem>
              ))}
            </SelectGroup>
          )}
        </SelectContent>
      </Select>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={`Delete ${domain.name}`}
        disabled={busy}
        onClick={onDelete}
      >
        <Trash2 />
      </Button>
    </li>
  );
}
