import { useState } from "react";
import { Trash2, Tag } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { DomainPicker } from "@/components/cv/domain-picker";

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
  const [add, setAdd] = useState<number[]>([]);
  const [remove, setRemove] = useState<number[]>([]);

  // A domain can't be both added and removed in the same apply — the most
  // recent pick wins, so picking in one combobox clears it from the other.
  const pickAdd = (next: number[]) => {
    setAdd(next);
    setRemove((r) => r.filter((id) => !next.includes(id)));
  };
  const pickRemove = (next: number[]) => {
    setRemove(next);
    setAdd((a) => a.filter((id) => !next.includes(id)));
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) {
          setAdd([]);
          setRemove([]);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Tag className="size-4" /> Domains…
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Bulk domain assignment</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1">
            <Label>Add to selected</Label>
            <DomainPicker value={add} onChange={pickAdd} />
          </div>
          <div className="space-y-1">
            <Label>Remove from selected</Label>
            <DomainPicker
              value={remove}
              onChange={pickRemove}
              allowCreate={false}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            disabled={add.length === 0 && remove.length === 0}
            onClick={() => {
              onApply(add, remove);
              setOpen(false);
            }}
          >
            Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
