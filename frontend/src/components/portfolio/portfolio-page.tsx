import { Link } from "@tanstack/react-router";
import {
  FigureTiles,
  ItemCard,
  NowCard,
} from "@/components/portfolio/item-card";
import { SkillNetwork } from "@/components/portfolio/skill-network";
import { SectionHeading } from "@/components/portfolio/section-heading";
import { YearBars } from "@/components/portfolio/year-bars";
import { anchorId, isEmptyPayload, pageAnchors } from "@/lib/portfolio/content";
import {
  addressLabel,
  hostLabel,
  safeHref,
  studioSections,
  type StudioSections,
} from "@/lib/portfolio/studio";
import { yearBars } from "@/lib/portfolio/timeline";
import type { PortfolioItem, PortfolioPayload } from "@/lib/queries/portfolio";
import { GUTTER } from "@/lib/layout";
import { cn } from "@/lib/utils";

const notSkill = (i: PortfolioItem) => i.type !== "skill";

/** The card grids: one column on a phone, two on a tablet, three from a laptop. A text block
 *  spans the row. */
const GRID =
  "grid grid-cols-1 gap-4 md:grid-cols-2 md:gap-6 lg:grid-cols-3 xl:gap-8";

/** The big closing line: the last call to action's label, and where it goes written out. When it
 *  repeats the hero's card, its title and pitch were said up there already. */
function Closing({ item, repeat }: NonNullable<StudioSections["closing"]>) {
  const href = safeHref(item.data?.url);
  if (!href) return null;
  return (
    <section
      id={repeat ? undefined : anchorId(item.id)}
      aria-label={item.data?.label}
      className="flex scroll-mt-6 flex-col gap-5 pt-4 pb-6 md:pt-8 md:pb-12"
    >
      {!repeat && item.title ? (
        <p className="font-heading font-medium text-primary">{item.title}</p>
      ) : null}
      <h2 className="font-heading text-6xl leading-none font-bold tracking-tight wrap-anywhere sm:text-8xl lg:text-9xl">
        {item.data?.label}
      </h2>
      {!repeat && item.body ? (
        <p className="max-w-xl text-lg whitespace-pre-line text-muted-foreground">
          {item.body}
        </p>
      ) : null}
      <a
        href={href}
        {...(href.startsWith("mailto:")
          ? {}
          : { target: "_blank", rel: "noreferrer" })}
        className="w-fit text-lg wrap-anywhere text-primary underline underline-offset-4 md:text-xl"
      >
        {addressLabel(href)}
      </a>
    </section>
  );
}

/** The public page (lib/portfolio/studio.ts splits it, theme.css colours it): the hero with the
 *  owner's first call to action as its "now" card, the first key figures as tiles, the numbered
 *  highlights, the skill network, the jobs and education as year bars, the rest, and the last
 *  call to action as the closing line. Every section is there only when the data is. */
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
  const page = studioSections(payload.featured, more);
  // Which ids own a card here — a block's nested links jump to them instead of leaving.
  const anchors = pageAnchors(
    payload.featured.filter(notSkill),
    more.filter(notSkill),
  );
  const all = [...payload.featured, ...more];
  const skillNames = new Map(
    all.filter((i) => i.type === "skill").map((s) => [s.id, s.title]),
  );
  const axis = yearBars(all);
  const intro = payload.intro || owner.bio;
  // A link's or the standard page's own title, over the name; never one made up.
  const kicker =
    payload.title && payload.title !== owner.display_name ? payload.title : "";
  const socials = [owner.website, owner.linkedin_url, owner.github_url]
    .map((u) => safeHref(u))
    .filter((u): u is string => u !== null);

  return (
    <main
      className={cn(
        "mx-auto flex max-w-6xl flex-col gap-14 pb-6 sm:pb-12 md:gap-24",
        GUTTER,
      )}
    >
      <div className="flex flex-col gap-10 md:gap-16">
        <header className="grid items-end gap-10 pt-14 md:pt-24 lg:grid-cols-3">
          <div
            className={cn(
              "flex min-w-0 flex-col gap-5",
              page.now ? "lg:col-span-2" : "lg:col-span-3",
            )}
          >
            {owner.avatar_url ? (
              <img
                src={owner.avatar_url}
                alt=""
                className="size-16 object-cover"
              />
            ) : null}
            {kicker ? (
              <p className="font-heading font-medium text-primary">{kicker}</p>
            ) : null}
            <h1 className="font-heading text-5xl leading-none font-bold tracking-tight text-balance wrap-anywhere sm:text-7xl lg:text-8xl">
              {owner.display_name}
            </h1>
            {intro ? (
              <p className="max-w-xl text-lg leading-relaxed whitespace-pre-line text-muted-foreground md:text-xl">
                {intro}
              </p>
            ) : null}
            {payload.intro && aiIntroFor ? (
              <p className="text-sm text-muted-foreground">
                Written by AI from the entries below ·{" "}
                <Link
                  to="/about/"
                  search={{ from: aiIntroFor }}
                  hash="ai"
                  className="underline underline-offset-4 hover:text-foreground"
                >
                  how this works
                </Link>
              </p>
            ) : null}
            {socials.length ? (
              <p className="flex flex-wrap gap-x-4 gap-y-1">
                {socials.map((u) => (
                  <a
                    key={u}
                    href={u}
                    target="_blank"
                    rel="noreferrer"
                    className="underline underline-offset-4"
                  >
                    {hostLabel(u)}
                  </a>
                ))}
              </p>
            ) : null}
          </div>
          {page.now ? <NowCard item={page.now} /> : null}
        </header>
        {page.figures ? <FigureTiles item={page.figures} /> : null}
      </div>

      {page.highlights.length > 0 && (
        <section
          aria-labelledby="highlights"
          className="flex flex-col gap-6 md:gap-8"
        >
          <SectionHeading id="highlights">Highlights</SectionHeading>
          <div className={GRID}>
            {page.highlights.map((item, i) => (
              <ItemCard
                key={item.id}
                item={item}
                number={i + 1}
                anchors={anchors}
                skillNames={skillNames}
              />
            ))}
          </div>
        </section>
      )}

      <SkillNetwork featured={payload.featured} more={more} cards={anchors} />

      {axis ? <YearBars axis={axis} cards={anchors} /> : null}

      {page.more.length > 0 && (
        <section
          aria-labelledby="more"
          className="flex flex-col gap-6 md:gap-8"
        >
          <SectionHeading id="more">More to explore</SectionHeading>
          <div className={GRID}>
            {page.more.map((item) => (
              <ItemCard
                key={item.id}
                item={item}
                anchors={anchors}
                skillNames={skillNames}
              />
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

      {page.closing ? <Closing {...page.closing} /> : null}
    </main>
  );
}
