import { useEffect } from "react";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { EscapeHatch } from "@/components/portfolio/escape-hatch";
import { PortfolioPage } from "@/components/portfolio/portfolio-page";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { clearStamp, readStamp, writeStamp } from "@/lib/portfolio/stamp";
import { usePortfolioLink } from "@/lib/queries/portfolio";

export const Route = createFileRoute("/portfolio/$slug")({
  head: () => ({ meta: [{ name: "robots", content: "noindex" }] }),
  component: PortfolioLinkRoute,
});

function PortfolioLinkRoute() {
  const { slug } = Route.useParams();
  const { status, isPending } = useAuth();
  const q = usePortfolioLink(slug);
  const navigate = useNavigate();

  // Successful anonymous load → remember this view. Owner previews never self-stamp.
  useEffect(() => {
    if (q.data && !isPending && status === "anonymous") {
      writeStamp({ kind: "link", slug });
    }
  }, [q.data, isPending, status, slug]);

  // Revoked/unknown slug → drop a matching stamp and fall through to the native flow.
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
  if (!q.data) return null; // 404 effect above is navigating away
  return (
    <>
      <EscapeHatch />
      <PortfolioPage payload={q.data} />
    </>
  );
}
