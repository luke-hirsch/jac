import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { QRCodeSVG } from "qrcode.react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  portfolioLinkNote,
  useUpdateApplication,
  type ApplicationRow,
} from "@/lib/queries/applications";
import {
  createApplicationLink,
  revokePortfolioLink,
} from "@/lib/queries/portfolio";
import { useProfile } from "@/lib/queries/profile";

const linkKey = (applicationId: number) =>
  ["portfolio", "application-link", applicationId] as const;

/** Whether this application's exports carry its portfolio link + QR, and the link itself.
 *  The choice is stored on the application (`include_portfolio_link`; null follows the
 *  profile's default), so it survives a reload. The link is minted while the choice is on:
 *  the server's get-or-create hands back the same active link every time. */
export function usePortfolioLinkChoice(app: ApplicationRow) {
  const qc = useQueryClient();
  const update = useUpdateApplication();
  const [on, setOnState] = useState(app.portfolio_link_on);
  const link = useQuery({
    queryKey: linkKey(app.id),
    queryFn: () => createApplicationLink(app.id),
    enabled: on,
    staleTime: Infinity,
  });

  function setOn(next: boolean) {
    setOnState(next);
    update.mutate(
      { id: app.id, body: { include_portfolio_link: next } },
      {
        onError: () => {
          setOnState(!next);
          toast.error("Couldn't save the portfolio-link choice");
        },
      },
    );
  }

  const revoke = useMutation({
    mutationFn: (id: number) => revokePortfolioLink(id),
    onSuccess: () => {
      qc.removeQueries({ queryKey: linkKey(app.id) });
      qc.invalidateQueries({ queryKey: ["portfolio", "links"] });
      setOn(false);
      toast.success("Portfolio link revoked");
    },
    onError: () => toast.error("Couldn't revoke the link"),
  });

  /** The URL an export prints: waits for the link when it's still on its way, and leaves it
   *  out (the section already says so) when it can't be made. */
  async function exportUrl(): Promise<string | undefined> {
    if (!on) return undefined;
    try {
      const row = await qc.fetchQuery({
        queryKey: linkKey(app.id),
        queryFn: () => createApplicationLink(app.id),
        staleTime: Infinity,
      });
      return row.url;
    } catch {
      return undefined;
    }
  }

  return {
    on,
    setOn,
    exportUrl,
    link: on ? (link.data ?? null) : null,
    linkFailed: on && link.isError,
    revoke,
  };
}

export type PortfolioLinkChoice = ReturnType<typeof usePortfolioLinkChoice>;

/** Export-card section for the application's portfolio link: the switch, and while it's on
 *  the link, its QR, copy and revoke. */
export function PortfolioLinkSection({
  app,
  choice,
}: {
  app: ApplicationRow;
  choice: PortfolioLinkChoice;
}) {
  const profile = useProfile();
  const { on, setOn, link, linkFailed, revoke } = choice;
  // The note reads the stored choice; a flip here is this application's own from then on.
  const note = portfolioLinkNote(
    on === app.portfolio_link_on ? app.include_portfolio_link : on,
    profile.data?.portfolio_link_by_default,
  );

  return (
    <div className="space-y-2">
      <div className="flex items-start gap-2">
        <Checkbox
          id={`portfolio-link-${app.id}`}
          checked={on}
          onCheckedChange={(v) => setOn(v === true)}
          className="mt-0.5"
        />
        <div className="space-y-0.5">
          <Label htmlFor={`portfolio-link-${app.id}`}>
            Add my portfolio link, with a QR code in the CV header
          </Label>
          <p className="text-xs text-muted-foreground">
            {note}{" "}
            <Link to="/portfolio/links" className="underline">
              Change the default
            </Link>
          </p>
        </div>
      </div>

      {linkFailed && (
        <p className="text-sm text-destructive">
          Couldn't create the portfolio link. Exports leave it out for now.
        </p>
      )}
      {link && (
        <div className="flex items-start gap-4">
          <div className="border bg-white p-2">
            <QRCodeSVG value={link.url} size={72} />
          </div>
          <div className="min-w-0 flex-1 space-y-2 text-sm">
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
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive"
              disabled={revoke.isPending}
              onClick={() => {
                if (
                  !confirm(
                    "Revoke this link? Anyone who has it lands on nothing. Switching the link back on makes a new one.",
                  )
                )
                  return;
                revoke.mutate(link.id);
              }}
            >
              Revoke link
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
