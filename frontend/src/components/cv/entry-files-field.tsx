import { useRef } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useAttachments,
  useDeleteAttachment,
  useUploadAttachment,
  type EntryLink,
  type UploadVars,
} from "@/lib/queries/attachments";

/**
 * Inline attachment control for a career-entry form (diploma, Arbeitszeugnis, certificate…).
 *
 * - **Edit mode** (`entryId` set): live — lists the entry's linked files and uploads/deletes
 *   immediately, each new file linked to this entry.
 * - **Create mode** (`entryId` null): the entry has no id yet, so a picked file is *staged*
 *   via `onPendingChange`; the editor uploads it (linked) right after it creates the entry.
 */
export function EntryFilesField({
  entryType,
  entryId,
  pending,
  onPendingChange,
}: {
  entryType: EntryLink;
  entryId: number | null;
  pending: File | null;
  onPendingChange: (f: File | null) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const list = useAttachments(entryId ? { [entryType]: entryId } : undefined);
  const upload = useUploadAttachment();
  const remove = useDeleteAttachment();

  if (!entryId) {
    return (
      <div className="space-y-1">
        <Label>Attachment (PDF)</Label>
        <Input
          type="file"
          accept="application/pdf"
          onChange={(e) => onPendingChange(e.target.files?.[0] ?? null)}
        />
        <p className="text-xs text-muted-foreground">
          {pending
            ? `“${pending.name}” will attach to this entry once you create it.`
            : "Optional — attaches to this entry when you click Create. Manage more later under CV → Files."}
        </p>
      </div>
    );
  }

  function onUpload() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    if (file.type !== "application/pdf") {
      toast.error("PDF only.");
      return;
    }
    const vars: UploadVars = { file };
    vars[entryType] = entryId;
    upload.mutate(vars, {
      onSuccess: () => {
        if (fileRef.current) fileRef.current.value = "";
      },
      onError: () => toast.error("Upload failed."),
    });
  }

  const items = list.data ?? [];
  return (
    <div className="space-y-2">
      <Label>Attachments (PDF)</Label>
      <ul className="space-y-1">
        {items.map((a) => (
          <li key={a.id} className="flex items-center gap-2 text-sm">
            <a
              href={a.file}
              target="_blank"
              rel="noreferrer"
              className="flex-1 truncate hover:underline"
            >
              {a.label || `attachment ${a.id}`}
            </a>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => remove.mutate(a.id)}
            >
              ✕
            </Button>
          </li>
        ))}
        {items.length === 0 && (
          <li className="text-xs text-muted-foreground">No files linked yet.</li>
        )}
      </ul>
      <div className="flex items-center gap-2">
        <Input
          ref={fileRef}
          type="file"
          accept="application/pdf"
          className="w-56"
        />
        <Button
          type="button"
          size="sm"
          onClick={onUpload}
          disabled={upload.isPending}
        >
          {upload.isPending ? "Uploading…" : "Add file"}
        </Button>
      </div>
    </div>
  );
}
