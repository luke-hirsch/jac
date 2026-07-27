import { toast } from "sonner";
import { Link } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  type ApplicationRow,
  useUpdateApplication,
} from "@/lib/queries/applications";
import {
  linkOf,
  useAttachments,
  type Attachment,
  type EntryLink,
} from "@/lib/queries/attachments";
import { moveId } from "@/lib/render/attachments";

const LINK_LABELS: Record<EntryLink, string> = {
  job: "reference",
  education: "diploma",
  certification: "certificate",
};

/**
 * Per-application attachment picker: select from the user's reusable file library (uploaded
 * under CV → Files, or from a CV entry) and order them. The chosen ids live on
 * `application.attachments`; the export merges the resolved files after the CV/letter.
 */
export function AttachmentsCard({ app }: { app: ApplicationRow }) {
  const library = useAttachments();
  const update = useUpdateApplication();
  const selected = app.attachments ?? [];
  const byId = new Map((library.data ?? []).map((a) => [a.id, a] as const));
  const selectedItems = selected
    .map((id) => byId.get(id))
    .filter((a): a is Attachment => a != null);
  const available = (library.data ?? []).filter((a) => !selected.includes(a.id));

  function save(ids: number[]) {
    update.mutate(
      { id: app.id, body: { attachments: ids } },
      { onError: () => toast.error("Could not update attachments.") },
    );
  }

  // Move by the id's real position in `selected` (robust to stale ids skipped in the render).
  function move(a: Attachment, delta: -1 | 1) {
    const next = moveId(selected, selected.indexOf(a.id), delta);
    if (next !== selected) save(next);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Attachments</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Pick from your file library and order them — merged after the CV/letter
          on export, in this order. Not included in a letter-only export.
        </p>

        <ul className="space-y-1">
          {selectedItems.map((a, i) => (
            <li key={a.id} className="flex items-center gap-2 text-sm">
              <span className="flex-1 truncate">
                {a.label || `attachment ${a.id}`}
              </span>
              <LinkBadge a={a} />
              <Button
                size="sm"
                variant="ghost"
                disabled={i === 0}
                onClick={() => move(a, -1)}
              >
                ↑
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={i === selectedItems.length - 1}
                onClick={() => move(a, 1)}
              >
                ↓
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => save(selected.filter((id) => id !== a.id))}
              >
                ✕
              </Button>
            </li>
          ))}
          {selectedItems.length === 0 && (
            <li className="text-xs text-muted-foreground">
              Nothing attached yet.
            </li>
          )}
        </ul>

        {available.length > 0 && (
          <div className="space-y-1 border-t pt-2">
            <p className="text-xs font-medium text-muted-foreground">
              Add from library
            </p>
            {available.map((a) => (
              <div key={a.id} className="flex items-center gap-2 text-sm">
                <span className="flex-1 truncate">
                  {a.label || `attachment ${a.id}`}
                </span>
                <LinkBadge a={a} />
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => save([...selected, a.id])}
                >
                  Add
                </Button>
              </div>
            ))}
          </div>
        )}

        {(library.data ?? []).length === 0 && !library.isLoading && (
          <p className="text-xs text-muted-foreground">
            No files yet — upload some under{" "}
            <Link to="/cv/attachments" className="underline">
              CV → Files
            </Link>
            , or straight from a CV entry.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function LinkBadge({ a }: { a: Attachment }) {
  const link = linkOf(a);
  if (!link) return null;
  return (
    <Badge variant="secondary" className="text-[10px]">
      {LINK_LABELS[link]}
    </Badge>
  );
}
