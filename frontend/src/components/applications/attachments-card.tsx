import { useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import type { ApplicationRow } from "@/lib/queries/applications";
import {
  useAttachments,
  useDeleteAttachment,
  useReorderAttachment,
  useUploadAttachment,
} from "@/lib/queries/attachments";
import { moveAttachment } from "@/lib/render/attachments";

export function AttachmentsCard({ app }: { app: ApplicationRow }) {
  const list = useAttachments(app.id);
  const upload = useUploadAttachment(app.id);
  const remove = useDeleteAttachment(app.id);
  const reorder = useReorderAttachment(app.id);
  const fileRef = useRef<HTMLInputElement>(null);
  const [label, setLabel] = useState("");

  const items = list.data ?? [];

  function onAdd() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    if (file.type !== "application/pdf") {
      toast.error("PDF only.");
      return;
    }
    upload.mutate(
      {
        file,
        label: label || file.name.replace(/\.pdf$/i, ""),
        position: items.length,
      },
      {
        onSuccess: () => {
          setLabel("");
          if (fileRef.current) fileRef.current.value = "";
        },
        onError: () => toast.error("Upload failed."),
      },
    );
  }

  function onMove(index: number, delta: -1 | 1) {
    const next = moveAttachment(items, index, delta);
    if (next === items) return;
    // Persist only the rows whose position actually changed.
    next.forEach((a, i) => {
      if (a.position !== items[i]?.position || a.id !== items[i]?.id) {
        reorder.mutate({ id: a.id, position: i });
      }
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Attachments (certs, transcripts)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          PDFs merged into the exported document after the CV/letter, in this
          order. Not included in a letter-only export.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <Input
            ref={fileRef}
            type="file"
            accept="application/pdf"
            className="w-56"
          />
          <Input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="Label (e.g. Zeugnisse)"
            className="w-44"
          />
          <Button size="sm" onClick={onAdd} disabled={upload.isPending}>
            {upload.isPending ? "Uploading…" : "Add"}
          </Button>
        </div>
        <ul className="space-y-1">
          {items.map((a, i) => (
            <li key={a.id} className="flex items-center gap-2 text-sm">
              <span className="flex-1 truncate">
                {a.label || `attachment ${a.id}`}
              </span>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onMove(i, -1)}
                disabled={i === 0}
              >
                ↑
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onMove(i, 1)}
                disabled={i === items.length - 1}
              >
                ↓
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => remove.mutate(a.id)}
              >
                ✕
              </Button>
            </li>
          ))}
          {items.length === 0 && (
            <li className="text-xs text-muted-foreground">
              No attachments yet.
            </li>
          )}
        </ul>
      </CardContent>
    </Card>
  );
}
