import { useMutation } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { type ApplicationRow } from "@/lib/queries/applications";
import {
  createApplicationLink,
  revokePortfolioLink,
  type PortfolioLinkRow,
} from "@/lib/queries/portfolio";

/** Export-card section for the application's portfolio link. Link state lives in the
 *  parent (buildPdf needs it). Client-only state: after a reload the toggle starts
 *  off — enabling it again just returns the same link (server get-or-create). */
export function PortfolioLinkSection({
  app,
  link,
  onLink,
  includeQr,
  onIncludeQr,
}: {
  app: ApplicationRow;
  link: PortfolioLinkRow | null;
  onLink: (link: PortfolioLinkRow | null) => void;
  includeQr: boolean;
  onIncludeQr: (on: boolean) => void;
}) {
  const create = useMutation({
    mutationFn: () => createApplicationLink(app.id),
    onSuccess: (row) => {
      onLink(row);
      onIncludeQr(true);
    },
    onError: () => toast.error("Couldn't create the portfolio link"),
  });
  const revoke = useMutation({
    mutationFn: () => revokePortfolioLink(link!.id),
    onSuccess: () => {
      onLink(null);
      onIncludeQr(false);
      toast.success("Portfolio link revoked");
    },
    onError: () => toast.error("Couldn't revoke the link"),
  });

  if (!link) {
    return (
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Add a personalised portfolio link + QR to the CV header.
        </p>
        <Button
          variant="outline"
          size="sm"
          disabled={create.isPending}
          onClick={() => create.mutate()}
        >
          Add portfolio link
        </Button>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-4">
      <div className="rounded-md border p-2 bg-white">
        <QRCodeSVG value={link.url} size={72} />
      </div>
      <div className="flex-1 space-y-2 text-sm">
        <div className="flex items-center gap-2">
          <code className="truncate">{link.url}</code>
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
        </div>
        <div className="flex items-center gap-2">
          <Checkbox
            id={`qr-${app.id}`}
            checked={includeQr}
            onCheckedChange={(v) => onIncludeQr(v === true)}
          />
          <Label htmlFor={`qr-${app.id}`}>Include QR in the CV header</Label>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="text-destructive"
          disabled={revoke.isPending}
          onClick={() => revoke.mutate()}
        >
          Revoke link
        </Button>
      </div>
    </div>
  );
}
