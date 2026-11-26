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
import {
  BLOCK_KINDS,
  BLOCK_LIMITS,
  MEDIA_ACCEPT,
  blockProblems,
  emptyFigure,
  emptyItem,
  formFromBlock,
  mediaKind,
  removeAt,
  saveErrorMessage,
  unseenFiles,
  updateAt,
  type BlockFiles,
  type BlockForm,
} from "@/lib/portfolio/block-form";
import { useFullList, type DomainRow } from "@/lib/queries/jac";
import {
  useSaveBlock,
  type BlockKind,
  type PortfolioBlockRow,
} from "@/lib/queries/portfolio";

type SetField = <K extends keyof BlockForm>(k: K, v: BlockForm[K]) => void;

export function BlockEditor({
  open,
  block,
  onOpenChange,
}: {
  open: boolean;
  block: PortfolioBlockRow | null;
  onOpenChange: (open: boolean) => void;
}) {
  const [form, setForm] = useState<BlockForm>(() => formFromBlock(block));
  const [files, setFiles] = useState<BlockFiles>({});
  const domains = useFullList<DomainRow>("domains");
  const save = useSaveBlock();

  const set: SetField = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const pick = (k: keyof BlockFiles) => (file: File | null) =>
    setFiles((f) => ({ ...f, [k]: file }));

  function toggleDomain(id: number) {
    set(
      "domains",
      form.domains.includes(id)
        ? form.domains.filter((d) => d !== id)
        : [...form.domains, id],
    );
  }

  function submit() {
    const problems = blockProblems(form, files, {
      image: Boolean(block?.image),
      media: Boolean(block?.media),
    });
    if (problems.length) {
      toast.error(problems[0]);
      return;
    }
    save.mutate(
      { id: block?.id, form, files },
      {
        onSuccess: () => {
          toast.success(block ? "Block saved" : "Block created");
          onOpenChange(false);
        },
        onError: (error) => toast.error(saveErrorMessage(error)),
      },
    );
  }

  // fetch can't report upload progress; the button at least says why it takes a while.
  const uploading = save.isPending && Boolean(files.media || files.image);
  const unseen = unseenFiles(block, form.kind);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="grid grid-rows-[auto_minmax(0,1fr)_auto] gap-4 overflow-hidden sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{block ? "Edit block" : "New block"}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 overflow-y-auto px-1">
          <div className="grid gap-2">
            <Label>Kind</Label>
            <Select
              value={form.kind}
              onValueChange={(v) => set("kind", v as BlockKind)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {BLOCK_KINDS.map((k) => (
                  <SelectItem key={k.value} value={k.value}>
                    {k.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {unseen.length ? (
              <p className="text-xs text-muted-foreground">
                This kind doesn't show the block's{" "}
                {unseen
                  .map((f) => (f === "media" ? "audio or video" : "image"))
                  .join(" and ")}
                . It stays stored until you switch back or delete the block.
              </p>
            ) : null}
          </div>

          <div className="grid gap-2">
            <Label htmlFor="b-title">Title</Label>
            <Input
              id="b-title"
              value={form.title}
              onChange={(e) => set("title", e.target.value)}
            />
          </div>

          <KindFields
            form={form}
            set={set}
            files={files}
            pick={pick}
            block={block}
          />

          <div className="grid gap-2">
            <Label>Domains</Label>
            <div className="flex flex-wrap gap-2">
              {(domains.data ?? []).map((d) => (
                <label
                  key={d.id}
                  className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-sm"
                >
                  <Checkbox
                    checked={form.domains.includes(d.id)}
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
              selected={form.links}
              onChange={(l) => set("links", l)}
            />
          </div>
          <div className="flex items-center gap-6">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={form.favourite}
                onCheckedChange={(v) => set("favourite", v === true)}
              />
              Featured by default
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={form.is_active}
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
                value={form.order}
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
            {uploading ? "Uploading…" : block ? "Save" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** The fields that change with the kind. Title, domains, links and the flags are the same for
 *  every kind and stay in the editor above. */
function KindFields({
  form,
  set,
  files,
  pick,
  block,
}: {
  form: BlockForm;
  set: SetField;
  files: BlockFiles;
  pick: (k: keyof BlockFiles) => (file: File | null) => void;
  block: PortfolioBlockRow | null;
}) {
  const L = BLOCK_LIMITS;
  switch (form.kind) {
    case "text":
      return (
        <BodyField
          label="Body (markdown)"
          rows={6}
          value={form.body}
          set={set}
        />
      );
    case "image":
      return (
        <>
          <FileField
            id="b-image"
            label="Image"
            accept="image/*"
            onPick={pick("image")}
          />
          {block?.image && !files.image ? (
            <img
              src={block.image}
              alt={block.alt_text}
              className="max-h-40 rounded-md object-cover"
            />
          ) : null}
          <AltField value={form.alt_text} set={set} />
          <BodyField label="Caption" rows={2} value={form.body} set={set} />
        </>
      );
    case "quote":
      return (
        <>
          <BodyField label="The quote" rows={3} value={form.body} set={set} />
          <div className="grid grid-cols-2 gap-2">
            <TextField
              id="b-by"
              label="Who said it"
              value={form.by}
              maxLength={L.by}
              onChange={(v) => set("by", v)}
            />
            <TextField
              id="b-role"
              label="Their role (optional)"
              value={form.role}
              maxLength={L.role}
              onChange={(v) => set("role", v)}
            />
          </div>
        </>
      );
    case "figures":
      return (
        <>
          <BodyField
            label="Lead line (optional)"
            rows={1}
            value={form.body}
            set={set}
          />
          <div className="grid gap-2">
            <Label>
              Figures ({L.figuresMin} to {L.figuresMax})
            </Label>
            {form.figures.map((f, i) => (
              <div key={i} className="flex items-center gap-2">
                <Input
                  aria-label={`Figure ${i + 1} value`}
                  placeholder="40%"
                  className="w-28"
                  value={f.value}
                  maxLength={L.figureValue}
                  onChange={(e) =>
                    set(
                      "figures",
                      updateAt(form.figures, i, { value: e.target.value }),
                    )
                  }
                />
                <Input
                  aria-label={`Figure ${i + 1} label`}
                  placeholder="faster builds"
                  value={f.label}
                  maxLength={L.figureLabel}
                  onChange={(e) =>
                    set(
                      "figures",
                      updateAt(form.figures, i, { label: e.target.value }),
                    )
                  }
                />
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={form.figures.length <= L.figuresMin}
                  onClick={() => set("figures", removeAt(form.figures, i))}
                >
                  Remove
                </Button>
              </div>
            ))}
            {form.figures.length < L.figuresMax ? (
              <Button
                variant="outline"
                size="sm"
                className="justify-self-start"
                onClick={() => set("figures", [...form.figures, emptyFigure()])}
              >
                Add a figure
              </Button>
            ) : null}
          </div>
        </>
      );
    case "cta":
      return (
        <>
          <BodyField
            label="The pitch (optional)"
            rows={2}
            value={form.body}
            set={set}
          />
          <div className="grid grid-cols-2 gap-2">
            <TextField
              id="b-label"
              label="Button label"
              value={form.label}
              maxLength={L.ctaLabel}
              onChange={(v) => set("label", v)}
            />
            <TextField
              id="b-url"
              label="Link (https:// or mailto:)"
              value={form.url}
              maxLength={L.url}
              onChange={(v) => set("url", v)}
            />
          </div>
        </>
      );
    case "linklist":
      return (
        <>
          <BodyField
            label="Lead line (optional)"
            rows={1}
            value={form.body}
            set={set}
          />
          <div className="grid gap-3">
            <Label>
              Links ({L.itemsMin} to {L.itemsMax})
            </Label>
            {form.items.map((item, i) => (
              <div key={i} className="grid gap-1.5 rounded-md border p-2">
                <div className="flex items-center gap-2">
                  <Input
                    aria-label={`Link ${i + 1} title`}
                    placeholder="Title"
                    value={item.title}
                    maxLength={L.itemTitle}
                    onChange={(e) =>
                      set(
                        "items",
                        updateAt(form.items, i, { title: e.target.value }),
                      )
                    }
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={form.items.length <= L.itemsMin}
                    onClick={() => set("items", removeAt(form.items, i))}
                  >
                    Remove
                  </Button>
                </div>
                <Input
                  aria-label={`Link ${i + 1} address`}
                  placeholder="https://…"
                  value={item.url}
                  maxLength={L.url}
                  onChange={(e) =>
                    set(
                      "items",
                      updateAt(form.items, i, { url: e.target.value }),
                    )
                  }
                />
                <Input
                  aria-label={`Link ${i + 1} note`}
                  placeholder="A note (optional)"
                  value={item.note}
                  maxLength={L.itemNote}
                  onChange={(e) =>
                    set(
                      "items",
                      updateAt(form.items, i, { note: e.target.value }),
                    )
                  }
                />
              </div>
            ))}
            {form.items.length < L.itemsMax ? (
              <Button
                variant="outline"
                size="sm"
                className="justify-self-start"
                onClick={() => set("items", [...form.items, emptyItem()])}
              >
                Add a link
              </Button>
            ) : null}
          </div>
        </>
      );
    case "media":
      return (
        <>
          <FileField
            id="b-media"
            label="Audio or video (MP4, WebM, MP3, M4A or Ogg, up to 50 MB)"
            accept={MEDIA_ACCEPT}
            onPick={pick("media")}
          />
          {block?.media && !files.media ? (
            <StoredMedia url={block.media} poster={block.image} />
          ) : null}
          <FileField
            id="b-poster"
            label="Cover image (optional)"
            accept="image/*"
            onPick={pick("image")}
          />
          <AltField value={form.alt_text} set={set} />
          <BodyField label="Caption" rows={2} value={form.body} set={set} />
        </>
      );
  }
}

/** The stored file, playable in place. `preload="none"`: nothing downloads until you press
 *  play, here as on the public page. */
function StoredMedia({ url, poster }: { url: string; poster: string | null }) {
  if (mediaKind(url) === "audio") {
    return <audio controls preload="none" src={url} className="w-full" />;
  }
  return (
    <video
      controls
      preload="none"
      src={url}
      poster={poster ?? undefined}
      className="max-h-48 w-full rounded-md bg-muted"
    />
  );
}

function BodyField({
  label,
  rows,
  value,
  set,
}: {
  label: string;
  rows: number;
  value: string;
  set: SetField;
}) {
  return (
    <div className="grid gap-2">
      <Label htmlFor="b-body">{label}</Label>
      <Textarea
        id="b-body"
        rows={rows}
        value={value}
        onChange={(e) => set("body", e.target.value)}
      />
    </div>
  );
}

function AltField({ value, set }: { value: string; set: SetField }) {
  return (
    <TextField
      id="b-alt"
      label="Alt text"
      value={value}
      maxLength={200}
      onChange={(v) => set("alt_text", v)}
    />
  );
}

function TextField({
  id,
  label,
  value,
  maxLength,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  maxLength: number;
  onChange: (value: string) => void;
}) {
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        value={value}
        maxLength={maxLength}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function FileField({
  id,
  label,
  accept,
  onPick,
}: {
  id: string;
  label: string;
  accept: string;
  onPick: (file: File | null) => void;
}) {
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>{label}</Label>
      <input
        id={id}
        type="file"
        accept={accept}
        onChange={(e) => onPick(e.target.files?.[0] ?? null)}
      />
    </div>
  );
}
