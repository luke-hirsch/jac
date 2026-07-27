import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LinkEditor } from "@/components/portfolio/link-editor";
import {
  usePortfolioLinks,
  useDeleteLink,
  useRevokeLink,
  type PortfolioLinkRow,
} from "@/lib/queries/portfolio";

export const Route = createFileRoute("/_authenticated/portfolio/links")({
  component: LinksTab,
});

function LinksTab() {
  const links = usePortfolioLinks();
  const revoke = useRevokeLink();
  const del = useDeleteLink();
  const [editing, setEditing] = useState<PortfolioLinkRow | null>(null);
  const [open, setOpen] = useState(false);

  function openNew() {
    setEditing(null);
    setOpen(true);
  }
  function openEdit(link: PortfolioLinkRow) {
    setEditing(link);
    setOpen(true);
  }

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button onClick={openNew}>New manual link</Button>
      </div>

      {links.isPending ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (links.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No links yet. Create a manual link, or add one from an application's export card.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Slug</TableHead>
              <TableHead>Kind</TableHead>
              <TableHead>Visits</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(links.data ?? []).map((link) => (
              <TableRow key={link.id} className={link.revoked_at ? "opacity-50" : ""}>
                <TableCell className="font-mono">
                  <a
                    href={link.url}
                    target="_blank"
                    rel="noreferrer"
                    className="hover:underline"
                  >
                    /{link.slug}
                  </a>
                </TableCell>
                <TableCell>
                  <Badge variant="secondary">{link.kind}</Badge>
                </TableCell>
                <TableCell>{link.visits}</TableCell>
                <TableCell>
                  {link.revoked_at ? (
                    <span className="text-destructive">revoked</span>
                  ) : (
                    "active"
                  )}
                </TableCell>
                <TableCell className="text-right space-x-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      navigator.clipboard.writeText(link.url);
                      toast.success("Link copied");
                    }}
                  >
                    Copy
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => openEdit(link)}>
                    Edit
                  </Button>
                  {link.revoked_at ? (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive"
                      onClick={() => del.mutate(link.id)}
                    >
                      Delete
                    </Button>
                  ) : (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive"
                      onClick={() => revoke.mutate(link.id)}
                    >
                      Revoke
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {open ? (
        <LinkEditor open={open} link={editing} onOpenChange={setOpen} />
      ) : null}
    </div>
  );
}
