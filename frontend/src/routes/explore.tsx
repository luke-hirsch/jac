import { useEffect } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";
import { EscapeHatch } from "@/components/portfolio/escape-hatch";
import { PortfolioPage } from "@/components/portfolio/portfolio-page";
import { useAuth } from "@/lib/auth";
import { reorderByRank } from "@/lib/portfolio/content";
import { writeStamp } from "@/lib/portfolio/stamp";
import { useNativePortfolio, usePortfolioRank } from "@/lib/queries/portfolio";

const exploreSearch = z.object({
  d: z.array(z.string()).optional(),
  lucky: z.boolean().optional(),
  q: z.string().optional(),
});

export const Route = createFileRoute("/explore")({
  validateSearch: exploreSearch,
  head: () => ({ meta: [{ name: "robots", content: "noindex" }] }),
  component: ExploreRoute,
});

function ExploreRoute() {
  const search = Route.useSearch();
  const { status, isPending } = useAuth();
  const portfolio = useNativePortfolio(search);
  const rank = usePortfolioRank(search);

  // Remember the answers (not the free-text q — a stale query re-ranking on every
  // return visit would burn the 6/h budget for nothing).
  useEffect(() => {
    if (portfolio.data && !isPending && status === "anonymous") {
      writeStamp({
        kind: "native",
        search: { d: search.d, lucky: search.lucky },
      });
    }
  }, [portfolio.data, isPending, status, search.d, search.lucky]);

  if (portfolio.isPending) {
    return (
      <main className="min-h-screen grid place-items-center text-muted-foreground">
        Loading…
      </main>
    );
  }
  if (!portfolio.data) {
    // Owner unset (native flow off) or transient failure — no dead end.
    return (
      <main className="min-h-screen grid place-items-center text-muted-foreground">
        The portfolio isn't available right now.
      </main>
    );
  }

  const more = rank.data
    ? reorderByRank(portfolio.data.more, rank.data.ranked)
    : portfolio.data.more;
  return (
    <>
      <EscapeHatch />
      {search.q && rank.isError ? (
        <p className="text-center text-xs text-muted-foreground pt-2">
          Couldn't rank by your interest just now — showing the natural order.
        </p>
      ) : null}
      <PortfolioPage payload={portfolio.data} moreOverride={more} />
    </>
  );
}
