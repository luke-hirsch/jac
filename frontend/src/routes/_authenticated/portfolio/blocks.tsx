import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { BlockEditor } from "@/components/portfolio/block-editor";
import { blockSummary, kindLabel } from "@/lib/portfolio/block-form";
import {
  usePortfolioBlocks,
  useDeleteBlock,
  type PortfolioBlockRow,
} from "@/lib/queries/portfolio";

export const Route = createFileRoute("/_authenticated/portfolio/blocks")({
  component: BlocksTab,
});

function BlocksTab() {
  const blocks = usePortfolioBlocks();
  const del = useDeleteBlock();
  const [editing, setEditing] = useState<PortfolioBlockRow | null>(null);
  const [open, setOpen] = useState(false);

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button
          onClick={() => {
            setEditing(null);
            setOpen(true);
          }}
        >
          New block
        </Button>
      </div>

      {blocks.isPending ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (blocks.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No blocks yet. Add what the career DB can't hold: text, a quote, key
          figures, a call to action, links, an image, audio or video.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {(blocks.data ?? []).map((b) => (
            <Card key={b.id}>
              <CardContent className="p-4 space-y-2">
                <div className="flex items-center gap-2">
                  <Badge variant="secondary">{kindLabel(b.kind)}</Badge>
                  {b.favourite ? <Badge>featured</Badge> : null}
                  {!b.is_active ? (
                    <Badge variant="outline">hidden</Badge>
                  ) : null}
                  <span className="font-medium truncate">
                    {b.title || `${kindLabel(b.kind)} block`}
                  </span>
                </div>
                {b.kind === "image" && b.image ? (
                  <img
                    src={b.image}
                    alt={b.alt_text}
                    className="max-h-32 rounded-md object-cover"
                  />
                ) : (
                  <p className="text-sm text-muted-foreground line-clamp-3">
                    {blockSummary(b)}
                  </p>
                )}
                <div className="flex justify-end gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setEditing(b);
                      setOpen(true);
                    }}
                  >
                    Edit
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-destructive"
                    onClick={() => del.mutate(b.id)}
                  >
                    Delete
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {open ? (
        <BlockEditor open={open} block={editing} onOpenChange={setOpen} />
      ) : null}
    </div>
  );
}
