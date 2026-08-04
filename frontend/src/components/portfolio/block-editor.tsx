import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ContentPicker } from "@/components/portfolio/content-picker";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useFullList, type DomainRow } from "@/lib/queries/jac";
import {
  useSaveBlock,
  type BlockInput,
  type PortfolioBlockRow,
} from "@/lib/queries/portfolio";

type Draft = BlockInput;

function draftFrom(block: PortfolioBlockRow | null): Draft {
  return {
    kind: block?.kind ?? "text",
    title: block?.title ?? "",
    body: block?.body ?? "",
    alt_text: block?.alt_text ?? "",
    domains: block?.domains ?? [],
    links: block?.links ?? [],
    favourite: block?.favourite ?? false,
    order: block?.order ?? 0,
    is_active: block?.is_active ?? true,
  };
}

export function BlockEditor({
  open,
  block,
  onOpenChange,
}: {
  open: boolean;
  block: PortfolioBlockRow | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftFrom(block));
  const [image, setImage] = useState<File | null>(null);
  const domains = useFullList<DomainRow>("domains");
  const save = useSaveBlock();

  function set<K extends keyof Draft>(k: K, v: Draft[K]) {
    setDraft((d) => ({ ...d, [k]: v }));
  }

  function toggleDomain(id: number) {
    set(
      "domains",
      draft.domains.includes(id)
        ? draft.domains.filter((d) => d !== id)
        : [...draft.domains, id],
    );
  }

  function submit() {
    if (draft.kind === "image" && !image && !block?.image) {
      toast.error("An image block needs an image.");
      return;
    }
    if (draft.kind === "text" && !draft.body.trim()) {
      toast.error("A text block needs a body.");
      return;
    }
    save.mutate(
      { id: block?.id, input: draft, image },
      {
        onSuccess: () => {
          toast.success(block ? "Block saved" : "Block created");
          onOpenChange(false);
        },
        onError: () => toast.error("Couldn't save the block"),
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="grid max-h-[calc(100dvh-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] gap-4 overflow-hidden sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{block ? "Edit block" : "New block"}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 overflow-y-auto px-1">
          <div className="grid gap-2">
            <Label>Kind</Label>
            <Select
              value={draft.kind}
              onValueChange={(v) => set("kind", v as Draft["kind"])}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="text">Text</SelectItem>
                <SelectItem value="image">Image</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="b-title">Title</Label>
            <Input
              id="b-title"
              value={draft.title}
              onChange={(e) => set("title", e.target.value)}
            />
          </div>

          {draft.kind === "text" ? (
            <div className="grid gap-2">
              <Label htmlFor="b-body">Body (markdown)</Label>
              <Textarea
                id="b-body"
                rows={6}
                value={draft.body}
                onChange={(e) => set("body", e.target.value)}
              />
            </div>
          ) : (
            <>
              <div className="grid gap-2">
                <Label htmlFor="b-image">Image</Label>
                <input
                  id="b-image"
                  type="file"
                  accept="image/*"
                  onChange={(e) => setImage(e.target.files?.[0] ?? null)}
                />
                {block?.image && !image ? (
                  <img
                    src={block.image}
                    alt={block.alt_text}
                    className="max-h-40 rounded-md object-cover"
                  />
                ) : null}
              </div>
              <div className="grid gap-2">
                <Label htmlFor="b-alt">Alt text</Label>
                <Input
                  id="b-alt"
                  value={draft.alt_text}
                  onChange={(e) => set("alt_text", e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="b-caption">Caption</Label>
                <Textarea
                  id="b-caption"
                  rows={2}
                  value={draft.body}
                  onChange={(e) => set("body", e.target.value)}
                />
              </div>
            </>
          )}

          <div className="grid gap-2">
            <Label>Domains</Label>
            <div className="flex flex-wrap gap-2">
              {(domains.data ?? []).map((d) => (
                <label
                  key={d.id}
                  className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-sm"
                >
                  <Checkbox
                    checked={draft.domains.includes(d.id)}
                    onCheckedChange={() => toggleDomain(d.id)}
                  />
                  {d.name}
                </label>
              ))}
            </div>
          </div>
          <div className="grid gap-2">
            <Label>Linked content (shown nested under this block)</Label>
            <ContentPicker
              selected={draft.links}
              onChange={(l) => set("links", l)}
            />
          </div>
          <div className="flex items-center gap-6">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={draft.favourite}
                onCheckedChange={(v) => set("favourite", v === true)}
              />
              Featured by default
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={draft.is_active}
                onCheckedChange={(v) => set("is_active", v === true)}
              />
              Active
            </label>
            <div className="flex items-center gap-2 text-sm">
              <Label htmlFor="b-order">Order</Label>
              <Input
                id="b-order"
                type="number"
                className="w-20"
                value={draft.order}
                onChange={(e) => set("order", Number(e.target.value) || 0)}
              />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={save.isPending} onClick={submit}>
            {block ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
