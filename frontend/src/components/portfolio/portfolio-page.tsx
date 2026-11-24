import { Link } from "@tanstack/react-router";
import { ItemCard } from "@/components/portfolio/item-card";
import { SkillNetwork } from "@/components/portfolio/skill-network";
import { isEmptyPayload, pageAnchors } from "@/lib/portfolio/content";
import type { PortfolioPayload } from "@/lib/queries/portfolio";

export function PortfolioPage({
  payload,
  moreOverride,
  aiIntroFor,
}: {
  payload: PortfolioPayload;
  /** /explore passes a rank-reordered "more" list; default is the server order. */
  moreOverride?: PortfolioPayload["more"];
  /** The handle, when `payload.intro` is the AI's welcome rather than the owner's own words:
   *  the page then says so, and links to how it's made. */
  aiIntroFor?: string;
}) {
  const { owner } = payload;
  const more = moreOverride ?? payload.more;
  // Skills live in the network, not in the card grids.
  const featuredCards = payload.featured.filter((i) => i.type !== "skill");
  const moreCards = more.filter((i) => i.type !== "skill");
  // Which ids own a card here — a block's nested links jump to them instead of leaving.
  const anchors = pageAnchors(featuredCards, moreCards);
  return (
    <main className="max-w-4xl mx-auto p-6 space-y-10">
      <header className="flex flex-col gap-6 sm:flex-row sm:items-center">
        {owner.avatar_url ? (
          <img
            src={owner.avatar_url}
            alt={owner.display_name}
            className="size-24 rounded-full object-cover"
          />
        ) : null}
        <div className="space-y-1">
          <h1 className="text-3xl font-bold tracking-tight">
            {payload.title || owner.display_name}
          </h1>
          {payload.intro || owner.bio ? (
            <p className="text-muted-foreground max-w-prose">
              {payload.intro || owner.bio}
            </p>
          ) : null}
          {payload.intro && aiIntroFor ? (
            <p className="text-xs text-muted-foreground">
              Written by AI from the entries below ·{" "}
              <Link
                to="/about/"
                search={{ from: aiIntroFor }}
                hash="ai"
                className="underline underline-offset-2 hover:text-foreground"
              >
                how this works
              </Link>
            </p>
          ) : null}
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm">
            {[owner.website, owner.linkedin_url, owner.github_url]
              .filter((u): u is string => Boolean(u))
              .map((u) => (
                <a
                  key={u}
                  href={u}
                  target="_blank"
                  rel="noreferrer"
                  className="underline"
                >
                  {new URL(u).hostname.replace(/^www\./, "")}
                </a>
              ))}
          </div>
        </div>
      </header>

      {featuredCards.length > 0 && (
        <section className="space-y-4">
          <h2 className="text-xl font-semibold">Highlights</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {featuredCards.map((item) => (
              <ItemCard key={item.id} item={item} anchors={anchors} />
            ))}
          </div>
        </section>
      )}

      <SkillNetwork featured={payload.featured} more={more} cards={anchors} />

      {moreCards.length > 0 && (
        <section className="space-y-4">
          <h2 className="text-xl font-semibold">More to explore</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {moreCards.map((item) => (
              <ItemCard key={item.id} item={item} anchors={anchors} />
            ))}
          </div>
        </section>
      )}

      {isEmptyPayload(payload.featured, more) && (
        <section className="py-12 text-center text-muted-foreground">
          <p>Nothing to show for this selection yet.</p>
          <p className="text-sm">
            Try “Start over” above to pick a different path.
          </p>
        </section>
      )}
    </main>
  );
}
