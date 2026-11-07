import { useEffect } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { EscapeHatch } from "@/components/portfolio/escape-hatch";
import { PortfolioPage } from "@/components/portfolio/portfolio-page";
import { useOwnerHome } from "@/components/portfolio/use-owner-home";
import { QueryError } from "@/components/query-error";
import { ApiError } from "@/lib/api";
import { clearStamp, readStamp, writeStamp } from "@/lib/portfolio/stamp";
import { usePortfolioLink } from "@/lib/queries/portfolio";

/** One of an owner's personalised links: portfolio.<base>/<handle>/<slug>/. */
export const Route = createFileRoute("/$handle/$slug")({
  component: PortfolioLink,
});

function PortfolioLink() {
  const handle = Route.useParams({ select: (p) => p.handle.toLowerCase() });
  const { slug } = Route.useParams();
  const q = usePortfolioLink(handle, slug);
  const goHome = useOwnerHome(handle);

  // The session cookie stays on the jac host, so everyone here is anonymous, the owner included.
  useEffect(() => {
    if (q.data) writeStamp(handle, { kind: "link", slug });
  }, [q.data, handle, slug]);

  // Revoked or unknown slug: drop a matching stamp and fall through to the owner's questionnaire.
  useEffect(() => {
    if (q.error instanceof ApiError && q.error.status === 404) {
      const stamp = readStamp(handle);
      if (stamp?.kind === "link" && stamp.slug === slug) clearStamp(handle);
      goHome({ replace: true });
    }
  }, [q.error, handle, slug, goHome]);

  if (q.isPending) {
    return (
      <main className="min-h-screen grid place-items-center text-muted-foreground">
        Loading…
      </main>
    );
  }
  if (!q.data) {
    if (q.error instanceof ApiError && q.error.status === 404) return null; // the effect above is navigating away
    // Someone scanned a QR code during a hiccup: a blank page would read as a dead link.
    return (
      <main className="min-h-screen grid place-items-center p-6 text-center">
        <QueryError
          error={q.error}
          what="this page"
          onRetry={() => q.refetch()}
          retrying={q.isFetching}
        />
      </main>
    );
  }
  return (
    <>
      <EscapeHatch handle={handle} />
      <PortfolioPage payload={q.data} />
    </>
  );
}
