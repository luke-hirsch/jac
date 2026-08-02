import { useNavigate } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { EscapeHatch } from "@/components/portfolio/escape-hatch";
import { PortfolioPage } from "@/components/portfolio/portfolio-page";
import { reorderByRank } from "@/lib/portfolio/content";
import { clearStamp } from "@/lib/portfolio/stamp";
import type { ExploreSearch } from "@/lib/portfolio/questionnaire";
import {
  useNativeIntro,
  useNativePortfolio,
  usePortfolioRank,
} from "@/lib/queries/portfolio";

export function ExploreResult({ search }: { search: ExploreSearch }) {
  const navigate = useNavigate();
  const portfolio = useNativePortfolio(search);
  const rank = usePortfolioRank(search);
  const intro = useNativeIntro(search); // disabled internally when search.lucky

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
  const payload = intro.data?.intro
    ? { ...portfolio.data, intro: intro.data.intro }
    : portfolio.data;
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
      <PortfolioPage payload={payload} moreOverride={more} />
    </>
  );
}
