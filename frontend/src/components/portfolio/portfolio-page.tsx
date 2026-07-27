import { ItemCard } from "@/components/portfolio/item-card";
import type { PortfolioPayload } from "@/lib/queries/portfolio";

export function PortfolioPage({
  payload,
  moreOverride,
}: {
  payload: PortfolioPayload;
  /** /explore passes a rank-reordered "more" list; default is the server order. */
  moreOverride?: PortfolioPayload["more"];
}) {
  const { owner } = payload;
  const more = moreOverride ?? payload.more;
  return (
    <main className="max-w-4xl mx-auto p-6 space-y-10">
      <header className="flex items-center gap-6">
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
          <div className="flex gap-3 text-sm">
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

      {payload.featured.length > 0 && (
        <section className="space-y-4">
          <h2 className="text-xl font-semibold">Highlights</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {payload.featured.map((item) => (
              <ItemCard key={item.id} item={item} />
            ))}
          </div>
        </section>
      )}

      {more.length > 0 && (
        <section className="space-y-4">
          <h2 className="text-xl font-semibold">More to explore</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            {more.map((item) => (
              <ItemCard key={item.id} item={item} />
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
