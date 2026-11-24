import { useState } from "react";
import { Button } from "@/components/ui/button";
import { EscapeHatch } from "@/components/portfolio/escape-hatch";
import {
  PersonaliseGate,
  PersonaliseNotice,
} from "@/components/portfolio/personalise-gate";
import { PortfolioPage } from "@/components/portfolio/portfolio-page";
import { useOwnerHome } from "@/components/portfolio/use-owner-home";
import { usePersonalise } from "@/components/portfolio/use-personalise";
import { reorderByRank } from "@/lib/portfolio/content";
import { clearStamp } from "@/lib/portfolio/stamp";
import type { ExploreSearch } from "@/lib/portfolio/questionnaire";
import { useNativePortfolio } from "@/lib/queries/portfolio";

/** A questionnaire's result. The standard page (the answers' topics, order and tone) loads at
 *  once; the AI's ranking and welcome come from a queued job, which can take minutes while the
 *  model server wakes. Until it's done the visitor sees a waiting card and chooses: wait, or
 *  browse the standard portfolio and switch when the note says it's ready. Nothing reorders
 *  under a reader. */
export function ExploreResult({
  handle,
  search,
}: {
  handle: string;
  search: ExploreSearch;
}) {
  const goHome = useOwnerHome(handle);
  const portfolio = useNativePortfolio(handle, search);
  const view = usePersonalise(handle, search);
  const [browsing, setBrowsing] = useState(false);
  const hasQuery = Boolean(search.q?.trim());

  if (view.phase === "working" && !browsing) {
    return (
      <>
        <EscapeHatch
          handle={handle}
          label="Your personalised page is on its way."
        />
        <PersonaliseGate
          view={view}
          ownerName={portfolio.data?.owner.display_name}
          onBrowse={() => setBrowsing(true)}
        />
      </>
    );
  }

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

  const personal = view.phase === "ready" && !browsing ? view : null;
  const standard = view.phase === "failed" || (browsing && !personal);
  const more = personal?.ranked
    ? reorderByRank(portfolio.data.more, personal.ranked)
    : portfolio.data.more;
  const payload = personal?.intro
    ? { ...portfolio.data, intro: personal.intro }
    : portfolio.data;
  return (
    <>
      <EscapeHatch
        handle={handle}
        onShuffle={search.lucky ? () => portfolio.refetch() : undefined}
        label={standard ? "You're seeing the standard portfolio." : undefined}
      />
      <PersonaliseNotice
        view={view}
        browsing={browsing}
        hasQuery={hasQuery}
        onShow={() => {
          setBrowsing(false);
          window.scrollTo({ top: 0, behavior: "smooth" });
        }}
      />
      <PortfolioPage
        payload={payload}
        moreOverride={more}
        aiIntroFor={personal?.intro ? handle : undefined}
      />
    </>
  );
}
