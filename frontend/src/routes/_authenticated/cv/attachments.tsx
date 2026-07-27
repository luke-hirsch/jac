import { createFileRoute } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  linkOf,
  useAttachments,
  useDeleteAttachment,
  useUpdateAttachment,
  useUploadAttachment,
  type Attachment,
  type EntryLink,
} from "@/lib/queries/attachments";

export const Route = createFileRoute("/_authenticated/cv/attachments")({
  component: FilesPage,
});

const LINK_LABELS: Record<EntryLink, string> = {
  job: "reference",
  education: "diploma",
  certification: "certificate",
};

function FilesPage() {
  const list = useAttachments();
  const upload = useUploadAttachment();
  const fileRef = useRef<HTMLInputElement>(null);
  const [label, setLabel] = useState("");

  function onUpload() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    if (file.type !== "application/pdf") {
      toast.error("PDF only.");
      return;
    }
    upload.mutate(
      { file, label: label || file.name.replace(/\.pdf$/i, "") },
      {
        onSuccess: () => {
          setLabel("");
          if (fileRef.current) fileRef.current.value = "";
        },
        onError: () => toast.error("Upload failed."),
      },
    );
  }

  const items = list.data ?? [];
  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">Files</h2>
        <p className="text-sm text-muted-foreground">
          Your reusable PDF attachments (diplomas, Zeugnisse, references, a
          portfolio). Upload once, then pick which to attach on each application.
          You can also add a file straight from a job / education / certification
          entry — it links to that entry automatically.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-2 rounded-md border p-3">
        <div className="space-y-1">
          <Label className="text-xs">File (PDF)</Label>
          <Input
            ref={fileRef}
            type="file"
            accept="application/pdf"
            className="w-64"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Label</Label>
          <Input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. Zeugnisse"
            className="w-52"
          />
        </div>
        <Button size="sm" onClick={onUpload} disabled={upload.isPending}>
          {upload.isPending ? "Uploading…" : "Upload"}
        </Button>
      </div>

      <ul className="divide-y rounded-md border">
        {items.map((a) => (
          <AttachmentRow key={a.id} a={a} />
        ))}
        {items.length === 0 && (
          <li className="p-4 text-sm text-muted-foreground">No files yet.</li>
        )}
      </ul>
    </div>
  );
}

function AttachmentRow({ a }: { a: Attachment }) {
  const update = useUpdateAttachment();
  const remove = useDeleteAttachment();
  const [label, setLabel] = useState(a.label);
  const link = linkOf(a);

  return (
    <li className="flex items-center gap-2 p-2">
      <Input
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        onBlur={() => {
          if (label !== a.label) update.mutate({ id: a.id, body: { label } });
        }}
        className="max-w-xs"
      />
      {link ? (
        <Badge variant="secondary">{LINK_LABELS[link]}</Badge>
      ) : (
        <Badge variant="outline">standalone</Badge>
      )}
      <a
        href={a.file}
        target="_blank"
        rel="noreferrer"
        className="text-sm text-muted-foreground hover:underline"
      >
        open
      </a>
      <div className="flex-1" />
      <Button
        size="sm"
        variant="ghost"
        onClick={() => {
          if (confirm("Delete this file?")) remove.mutate(a.id);
        }}
      >
        Delete
      </Button>
    </li>
  );
}
