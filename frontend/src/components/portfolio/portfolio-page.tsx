import { Link } from "@tanstack/react-router";
import {
  FigureTiles,
  ItemCard,
  NowCard,
} from "@/components/portfolio/item-card";
import { SkillNetwork } from "@/components/portfolio/skill-network";
import { SectionHeading } from "@/components/portfolio/studio";
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

const notSkill = (i: PortfolioItem) => i.type !== "skill";

/** The card grids: as many 340 px columns as fit, one on a phone. A text block spans the row. */
const GRID =
  "grid grid-cols-[repeat(auto-fill,minmax(min(100%,21.25rem),1fr))] gap-4";

/** The big closing line: the last call to action's label, and where it goes written out. When it
 *  repeats the hero's card, its title and pitch were said up there already. */
function Closing({ item, repeat }: NonNullable<StudioSections["closing"]>) {
  const href = safeHref(item.data?.url);
  if (!href) return null;
  return (
    <section
      id={repeat ? undefined : anchorId(item.id)}
      aria-label={item.data?.label}
      className="flex scroll-mt-6 flex-col gap-5 pt-[clamp(1rem,4vw,2rem)] pb-[clamp(1.5rem,4vw,3rem)]"
    >
      {!repeat && item.title ? (
        <p className="font-display text-[15px] font-medium text-studio-accent">
          {item.title}
        </p>
      ) : null}
      <h2 className="font-display text-[clamp(3.5rem,12vw,10.5rem)] leading-[0.9] font-bold tracking-[-0.05em] [overflow-wrap:anywhere]">
        {item.data?.label}
      </h2>
      {!repeat && item.body ? (
        <p className="max-w-[56ch] text-lg whitespace-pre-line text-studio-soft">
          {item.body}
        </p>
      ) : null}
      <a
        href={href}
        {...(href.startsWith("mailto:")
          ? {}
          : { target: "_blank", rel: "noreferrer" })}
        className="w-fit text-[clamp(1.125rem,2vw,1.375rem)] [overflow-wrap:anywhere] text-studio-accent underline underline-offset-[6px]"
      >
        {addressLabel(href)}
      </a>
    </section>
  );
}

/** The public page in the Studio look (lib/portfolio/studio.ts splits it): the hero with the
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
    <main className="mx-auto flex max-w-[1160px] flex-col gap-[clamp(3.5rem,8vw,6rem)] px-[clamp(1rem,4vw,3rem)] pb-[clamp(1.5rem,4vw,3rem)]">
      <div className="flex flex-col gap-[clamp(2.5rem,6vw,4rem)]">
        <header className="flex flex-wrap items-end gap-10 pt-[clamp(3.5rem,9vw,6.5rem)]">
          <div className="flex min-w-0 flex-[999_1_35rem] flex-col gap-5">
            {owner.avatar_url ? (
              <img
                src={owner.avatar_url}
                alt=""
                className="size-16 rounded-full object-cover"
              />
            ) : null}
            {kicker ? (
              <p className="font-display text-[15px] font-medium text-studio-accent">
                {kicker}
              </p>
            ) : null}
            <h1 className="font-display text-[clamp(3.25rem,11vw,9rem)] leading-[0.88] font-bold tracking-[-0.045em] text-balance [overflow-wrap:anywhere]">
              {owner.display_name}
            </h1>
            {intro ? (
              <p className="max-w-[56ch] text-[clamp(1.0625rem,1.8vw,1.25rem)] leading-relaxed whitespace-pre-line text-studio-soft">
                {intro}
              </p>
            ) : null}
            {payload.intro && aiIntroFor ? (
              <p className="text-[13px] text-muted-foreground">
                Written by AI from the entries below ·{" "}
                <Link
                  to="/about/"
                  search={{ from: aiIntroFor }}
                  hash="ai"
                  className="underline underline-offset-[3px] hover:text-foreground"
                >
                  how this works
                </Link>
              </p>
            ) : null}
            {socials.length ? (
              <p className="flex flex-wrap gap-x-4 gap-y-1 text-[15px]">
                {socials.map((u) => (
                  <a
                    key={u}
                    href={u}
                    target="_blank"
                    rel="noreferrer"
                    className="underline underline-offset-[3px]"
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
        <section aria-labelledby="highlights" className="flex flex-col gap-6">
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
        <section aria-labelledby="more" className="flex flex-col gap-6">
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
