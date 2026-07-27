import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { EscapeHatch } from "@/components/portfolio/escape-hatch";
import { PortfolioPage } from "@/components/portfolio/portfolio-page";
import { reorderByRank } from "@/lib/portfolio/content";
import { clearStamp } from "@/lib/portfolio/stamp";
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
  const navigate = useNavigate();
  const portfolio = useNativePortfolio(search);
  const rank = usePortfolioRank(search);

  // NOTE: no stamp is written here. The stamp is set once, when the visitor answers
  // the questionnaire (index.tsx onDone). Writing it on every passive load was the
  // reset trap — it re-stamped a visitor the moment the escape hatch cleared them.

  if (portfolio.isPending) {
    return (
      <main className="min-h-screen grid place-items-center text-muted-foreground">
        Loading…
      </main>
    );
  }
  if (!portfolio.data) {
    // Owner unset (native flow off) or transient failure — always offer a way back,
    // and clear the stamp so "/" doesn't bounce the visitor straight back here.
    return (
      <main className="min-h-screen grid place-items-center">
        <div className="space-y-3 text-center">
          <p className="text-muted-foreground">
            The portfolio isn't available right now.
          </p>
          <Button
            variant="outline"
            onClick={() => {
              clearStamp();
              navigate({ to: "/" });
            }}
          >
            Back to start
          </Button>
        </div>
      </main>
    );
  }

  const more = rank.data
    ? reorderByRank(portfolio.data.more, rank.data.ranked)
    : portfolio.data.more;
  return (
    <>
      <EscapeHatch
        onShuffle={search.lucky ? () => portfolio.refetch() : undefined}
      />
      {search.q && rank.isError ? (
        <p className="text-center text-xs text-muted-foreground pt-2">
          Couldn't rank by your interest just now — showing the natural order.
        </p>
      ) : null}
      <PortfolioPage payload={portfolio.data} moreOverride={more} />
    </>
  );
}
