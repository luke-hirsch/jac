import { useEffect } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { EscapeHatch } from "@/components/portfolio/escape-hatch";
import { PortfolioPage } from "@/components/portfolio/portfolio-page";
import { QueryError } from "@/components/query-error";
import { ApiError } from "@/lib/api";
import { clearStamp, readStamp, writeStamp } from "@/lib/portfolio/stamp";
import { usePortfolioLink } from "@/lib/queries/portfolio";

export const Route = createFileRoute("/$slug")({
  head: () => ({ meta: [{ name: "robots", content: "noindex" }] }),
  component: PortfolioSlug,
});

function PortfolioSlug() {
  const { slug } = Route.useParams();
  const q = usePortfolioLink(slug);
  const navigate = useNavigate();

  useEffect(() => {
    if (q.data) writeStamp({ kind: "link", slug }); // anonymous by construction on a handle host
  }, [q.data, slug]);

  useEffect(() => {
    if (q.error instanceof ApiError && q.error.status === 404) {
      const stamp = readStamp();
      if (stamp?.kind === "link" && stamp.slug === slug) clearStamp();
      navigate({ to: "/", replace: true });
    }
  }, [q.error, slug, navigate]);

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
      <EscapeHatch />
      <PortfolioPage payload={q.data} />
    </>
  );
}
