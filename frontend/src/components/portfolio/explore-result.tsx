import { Button } from "@/components/ui/button";
import { EscapeHatch } from "@/components/portfolio/escape-hatch";
import { PortfolioPage } from "@/components/portfolio/portfolio-page";
import { useOwnerHome } from "@/components/portfolio/use-owner-home";
import { reorderByRank } from "@/lib/portfolio/content";
import { clearStamp } from "@/lib/portfolio/stamp";
import type { ExploreSearch } from "@/lib/portfolio/questionnaire";
import {
  useNativeIntro,
  useNativePortfolio,
  usePortfolioRank,
} from "@/lib/queries/portfolio";

export function ExploreResult({
  handle,
  search,
}: {
  handle: string;
  search: ExploreSearch;
}) {
  const goHome = useOwnerHome(handle);
  const portfolio = useNativePortfolio(handle, search);
  const rank = usePortfolioRank(handle, search);
  const intro = useNativeIntro(handle, search); // disabled internally when search.lucky

  if (portfolio.isPending) {
    return (
      <main className="min-h-screen grid place-items-center text-muted-foreground">
        Loading…
      </main>
    );
  }
  if (!portfolio.data) {
    return (
      <main className="min-h-screen grid place-items-center">
        <div className="space-y-3 text-center">
          <p className="text-muted-foreground">
            The portfolio isn't available right now.
          </p>
          <Button
            variant="outline"
            onClick={() => {
              clearStamp(handle);
              goHome();
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
  const payload = intro.data?.intro
    ? { ...portfolio.data, intro: intro.data.intro }
    : portfolio.data;
  return (
    <>
      <EscapeHatch
        handle={handle}
        onShuffle={search.lucky ? () => portfolio.refetch() : undefined}
      />
      {search.q && rank.data?.ai === "warming_up" ? (
        <p className="text-center text-xs text-muted-foreground pt-2">
          The AI is warming up — showing the natural order for now.
        </p>
      ) : search.q && (rank.isError || rank.data?.ai === "unavailable") ? (
        <p className="text-center text-xs text-muted-foreground pt-2">
          Couldn't rank by your interest just now — showing the natural order.
        </p>
      ) : null}
      {intro.isFetching ? (
        <p className="text-center text-xs text-muted-foreground pt-2">
          Writing a personal welcome…
        </p>
      ) : null}
      <PortfolioPage payload={payload} moreOverride={more} />
    </>
  );
}
