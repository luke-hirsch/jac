import type { ReactNode } from "react";
import { MediaPlayer } from "@/components/portfolio/media-player";
import { StudioMarkdown } from "@/components/portfolio/studio-markdown";
import { Button } from "@/components/ui/button";
import { anchorId, linkTarget, outboundUrl } from "@/lib/portfolio/content";
import {
  hostLabel,
  metaLine,
  ordinal,
  quoted,
  safeHref,
  scrollMotion,
} from "@/lib/portfolio/studio";
import type { BlockFigure, PortfolioItem } from "@/lib/queries/portfolio";
import { cn } from "@/lib/utils";

/** A job's skills as chips; the network has all of them. */
const MAX_CHIPS = 8;

/** A link that leaves opens in a new tab and doesn't say where the visitor came from; a mail
 *  link just opens the mail program. */
const leaves = (href: string) =>
  href.startsWith("mailto:")
    ? {}
    : { target: "_blank" as const, rel: "noreferrer" };

/** A block's nested links. Each title is clickable when it has somewhere to go:
 *  on-page jump to the item's own card, else out to the entry's url (see linkTarget).
 *  The anchor scrolls instead of hard-jumping — href stays for middle-click. Colours follow the
 *  card's text, so the same list reads on a dark card and on a quote's accent. */
function LinkedItems({
  items,
  anchors,
}: {
  items: PortfolioItem[];
  anchors: Set<string>;
}) {
  return (
    <div className="mt-1 space-y-1 border-l-2 border-current/25 pl-3">
      {items.map((li) => {
        const target = linkTarget(li, anchors);
        const outbound = outboundUrl(li, target);
        return (
          <div key={li.id} className="text-sm">
            {target === null ? (
              <span className="font-medium">{li.title}</span>
            ) : target.kind === "anchor" ? (
              <a
                href={target.href}
                className="font-medium underline underline-offset-2"
                onClick={(e) => {
                  const el = document.getElementById(anchorId(li.id));
                  if (!el) return; // no card after all — let the browser try the hash
                  e.preventDefault();
                  el.scrollIntoView({
                    behavior: scrollMotion(),
                    block: "start",
                  });
                }}
              >
                {li.title}
              </a>
            ) : (
              <a
                href={target.href}
                target="_blank"
                rel="noreferrer"
                className="font-medium underline underline-offset-2"
              >
                {li.title} ↗
              </a>
            )}
            {li.subtitle ? (
              <span className="opacity-75"> · {li.subtitle}</span>
            ) : null}
            {outbound ? (
              <a
                href={outbound}
                target="_blank"
                rel="noreferrer"
                aria-label={`${li.title} (external link)`}
                className="ml-1 opacity-75"
              >
                ↗
              </a>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/** The inside of every cell on a portfolio: the one place its padding is set. */
const PAD = "p-6 md:p-8 xl:p-10";

/** A cell of the page's grid (portfolio-page.tsx's CELLS). The grid draws the top and left
 *  lines, each cell its right and bottom one, so neighbours share a single hairline and a short
 *  last row ends cleanly. */
const CELL = "border-r border-b";

/** The cell every kind but the quote and the link list sits in. It takes one column; `wide`
 *  takes the whole row (text reads better long). */

function Shell({
  item,
  wide,
  flush,
  className,
  children,
}: {
  item: PortfolioItem;
  wide?: boolean;
  /** No padding: the card pads its own parts (an image to the edge, text under it). */
  flush?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <article
      id={anchorId(item.id)}
      className={cn(
        "flex min-w-0 scroll-mt-6 flex-col gap-3 md:gap-4",
        CELL,
        !flush && PAD,
        wide && "col-span-full",
        className,
      )}
    >
      {children}
    </article>
  );
}

/** The small line over a card: its place in the highlights, and what and when it is. Without a
 *  number (the rest of the page) the line starts on the left. */
function Meta({ item, number }: { item: PortfolioItem; number?: number }) {
  const line = metaLine(item);
  if (!number && !line) return null;
  return (
    <div className="flex justify-between gap-3 text-sm text-muted-foreground">
      {number ? <span className="font-heading">{ordinal(number)}</span> : null}
      <span className={number ? "text-right" : ""}>{line}</span>
    </div>
  );
}

function Title({ children }: { children: ReactNode }) {
  return (
    <h3 className="font-heading text-2xl leading-tight font-semibold tracking-tight">
      {children}
    </h3>
  );
}

/** A call to action's button: jac's own, as a link. A label runs to 40 characters, so it may
 *  wrap on a phone rather than overflow. */
function CtaButton({
  item,
  className,
}: {
  item: PortfolioItem;
  className?: string;
}) {
  const href = safeHref(item.data?.url);
  if (!href || !item.data?.label) return null;
  return (
    <Button
      asChild
      size="lg"
      className={cn("h-auto min-h-11 w-fit py-3 whitespace-normal", className)}
    >
      <a href={href} {...leaves(href)}>
        {item.data.label}
      </a>
    </Button>
  );
}

/** The hero's "now" box: the page's first call to action, in the hero's last column. */
export function NowCard({ item }: { item: PortfolioItem }) {
  return (
    <aside
      id={anchorId(item.id)}
      aria-label={item.title || item.data?.label}
      className="flex min-w-0 scroll-mt-6 flex-col gap-4 self-end border p-6"
    >
      {item.title ? (
        <div className="flex items-center gap-2.5">
          <span aria-hidden className="size-2.5 shrink-0 bg-primary" />
          <span className="font-heading text-lg font-semibold">
            {item.title}
          </span>
        </div>
      ) : null}
      {item.body ? (
        <p className="whitespace-pre-line text-muted-foreground">{item.body}</p>
      ) : null}
      <CtaButton item={item} />
    </aside>
  );
}

/** Key figures: the value big, in the primary, its label under it. On the page they are cells of
 *  their own grid; inside a cell, columns under a rule. Columns by how many there are and how wide
 *  the box is (a container query: a cell is narrower than the page), so three never leave a gap
 *  in a row of four. */
const FIGURE_COLUMNS: Record<number, string> = {
  2: "grid-cols-2",
  3: "grid-cols-2 @xl:grid-cols-3",
  4: "grid-cols-2 @3xl:grid-cols-4",
};

function Figures({
  figures,
  inCard,
}: {
  figures: BlockFigure[];
  inCard?: boolean;
}) {
  return (
    <div className="@container">
      <dl
        className={cn(
          "grid",
          inCard ? "gap-x-6 gap-y-5" : "border-t border-l",
          FIGURE_COLUMNS[figures.length] ?? FIGURE_COLUMNS[4],
        )}
      >
        {figures.map((f, i) => (
          <div
            key={i}
            className={cn(
              "flex flex-col gap-1.5",
              inCard ? "border-t pt-3" : cn(CELL, PAD),
            )}
          >
            <dt className="order-2 text-sm text-muted-foreground">{f.label}</dt>
            <dd
              className={cn(
                "order-1 font-heading leading-none font-bold tracking-tight wrap-anywhere text-primary",
                inCard ? "text-4xl" : "text-4xl md:text-5xl lg:text-6xl",
              )}
            >
              {f.value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** The tiles under the hero: the page's first key-figures block. */
export function FigureTiles({ item }: { item: PortfolioItem }) {
  return (
    <section
      id={anchorId(item.id)}
      aria-label={item.title || "Key figures"}
      className="flex scroll-mt-6 flex-col gap-4 md:gap-6"
    >
      {item.title || item.body ? (
        <div className="flex flex-col gap-1">
          {item.title ? (
            <h2 className="font-heading text-xl font-semibold">{item.title}</h2>
          ) : null}
          {item.body ? (
            <p className="text-muted-foreground">{item.body}</p>
          ) : null}
        </div>
      ) : null}
      <Figures figures={item.data?.figures ?? []} />
    </section>
  );
}

type CardProps = {
  item: PortfolioItem;
  number?: number;
  anchors: Set<string>;
  skillNames?: Map<string, string>;
};

function QuoteCard({ item, number, anchors }: CardProps) {
  const label = [number ? ordinal(number) : "", item.title]
    .filter(Boolean)
    .join(" · ");
  const by = [item.data?.by, item.data?.role].filter(Boolean).join(" · ");
  return (
    <figure
      id={anchorId(item.id)}
      className={cn(
        "m-0 flex min-w-0 scroll-mt-6 flex-col justify-between gap-5 bg-primary text-primary-foreground md:gap-6",
        CELL,
        PAD,
      )}
    >
      {label ? (
        <span className="font-heading text-sm font-semibold">{label}</span>
      ) : null}
      <blockquote className="font-heading text-2xl leading-snug font-medium whitespace-pre-line">
        {quoted(item.body ?? "")}
      </blockquote>
      {item.links?.length ? (
        <LinkedItems items={item.links} anchors={anchors} />
      ) : null}
      {by ? (
        <figcaption className="text-sm leading-snug font-medium">
          {by}
        </figcaption>
      ) : null}
    </figure>
  );
}

function LinkListCard({ item, number }: CardProps) {
  const rows = (item.data?.items ?? []).flatMap((row) => {
    const href = safeHref(row.url);
    return href ? [{ ...row, href }] : [];
  });
  return (
    <nav
      id={anchorId(item.id)}
      aria-label={item.title || "Links"}
      className={cn("flex min-w-0 scroll-mt-6 flex-col", CELL, PAD)}
    >
      <Meta item={item} number={number} />
      {item.title ? (
        <h3 className="mt-1 mb-2 font-heading text-2xl leading-tight font-semibold tracking-tight">
          {item.title}
        </h3>
      ) : null}
      {item.body ? (
        <p className="mb-3 text-muted-foreground">{item.body}</p>
      ) : null}
      <ul className="border-b">
        {rows.map((row, i) => (
          <li key={i} className="border-t">
            <a
              href={row.href}
              {...leaves(row.href)}
              className="flex min-h-11 items-center gap-4 py-4 no-underline"
            >
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-xs tracking-wider text-primary uppercase">
                  {hostLabel(row.href)}
                </span>
                <span className="font-semibold">{row.title}</span>
                {row.note ? (
                  <span className="text-sm text-muted-foreground">
                    {row.note}
                  </span>
                ) : null}
              </span>
              <span aria-hidden className="text-2xl">
                →
              </span>
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function TextCard({ item, number, anchors }: CardProps) {
  return (
    <Shell item={item} wide>
      <div className="flex flex-col gap-x-12 gap-y-4 md:flex-row">
        {number || item.title ? (
          <div className="flex flex-col gap-2 md:w-1/3 md:shrink-0">
            <Meta item={item} number={number} />
            {item.title ? <Title>{item.title}</Title> : null}
          </div>
        ) : null}
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <StudioMarkdown source={item.body ?? ""} />
          {item.links?.length ? (
            <LinkedItems items={item.links} anchors={anchors} />
          ) : null}
        </div>
      </div>
    </Shell>
  );
}

function ImageCard({ item, number, anchors }: CardProps) {
  const text = Boolean(number || item.title || item.body || item.links?.length);
  return (
    <Shell item={item} flush className="gap-0 overflow-hidden md:gap-0">
      {item.image_url ? (
        <img
          src={item.image_url}
          alt={item.alt_text || item.title}
          loading="lazy"
          className="max-h-112 w-full object-cover"
        />
      ) : null}
      {text ? (
        <div className={cn("flex flex-col gap-2.5", PAD)}>
          <Meta item={item} number={number} />
          {item.title ? <Title>{item.title}</Title> : null}
          {item.body ? (
            <p className="whitespace-pre-line text-muted-foreground">
              {item.body}
            </p>
          ) : null}
          {item.links?.length ? (
            <LinkedItems items={item.links} anchors={anchors} />
          ) : null}
        </div>
      ) : null}
    </Shell>
  );
}

/** A career entry: what, where, when, in its own words, and the skills on this page it used. */
function EntryCard({ item, number, skillNames }: CardProps) {
  const href = safeHref(item.url);
  const skills = (item.skills ?? [])
    .map((id) => skillNames?.get(id))
    .filter((n): n is string => Boolean(n));
  return (
    <Shell item={item}>
      <Meta item={item} number={number} />
      <Title>{item.title}</Title>
      {item.subtitle ? (
        <span className="text-muted-foreground">{item.subtitle}</span>
      ) : null}
      {item.description ? (
        <p className="whitespace-pre-line text-muted-foreground">
          {item.description}
        </p>
      ) : null}
      {href ? (
        <a
          href={href}
          {...leaves(href)}
          className="w-fit text-sm underline underline-offset-4"
        >
          {hostLabel(href)} ↗
        </a>
      ) : null}
      {skills.length ? (
        <ul aria-label="Skills" className="flex flex-wrap gap-1.5 pt-1">
          {skills.slice(0, MAX_CHIPS).map((name, i) => (
            <li key={i} className="rounded-md bg-muted px-2.5 py-0.5 text-xs">
              {name}
            </li>
          ))}
          {skills.length > MAX_CHIPS ? (
            <li className="px-1 py-0.5 text-xs text-muted-foreground">
              +{skills.length - MAX_CHIPS} more
            </li>
          ) : null}
        </ul>
      ) : null}
    </Shell>
  );
}

/** Anything without a better drawing (a kind this page doesn't know yet, or a block missing what
 *  its kind needs): its title and its words, never nothing. */
function PlainCard({ item, number, anchors }: CardProps) {
  return (
    <Shell item={item}>
      <Meta item={item} number={number} />
      {item.title ? <Title>{item.title}</Title> : null}
      {item.body ? (
        <p className="whitespace-pre-line text-muted-foreground">{item.body}</p>
      ) : null}
      {item.links?.length ? (
        <LinkedItems items={item.links} anchors={anchors} />
      ) : null}
    </Shell>
  );
}

/** One item on the portfolio page, drawn by what it is. `number` is its place in the highlights;
 *  the rest go unnumbered. */
export function ItemCard({
  item,
  number,
  anchors,
  skillNames,
}: {
  item: PortfolioItem;
  number?: number;
  /** Ids that render as a card on this page — nested links jump there instead of
   *  leaving the site. Defaults to empty (a card rendered outside a full page). */
  anchors?: Set<string>;
  /** Skill ids → names, for a job's or a project's chips. */
  skillNames?: Map<string, string>;
}) {
  const props = { item, number, anchors: anchors ?? new Set<string>() };
  if (item.type !== "block")
    return <EntryCard {...props} skillNames={skillNames} />;
  switch (item.kind) {
    case "text":
      return <TextCard {...props} />;
    case "image":
      return <ImageCard {...props} />;
    case "quote":
      return item.body ? <QuoteCard {...props} /> : <PlainCard {...props} />;
    case "figures":
      return item.data?.figures?.length ? (
        <Shell item={item}>
          <Meta item={item} number={number} />
          {item.title ? <Title>{item.title}</Title> : null}
          {item.body ? (
            <p className="text-muted-foreground">{item.body}</p>
          ) : null}
          <Figures figures={item.data.figures} inCard />
        </Shell>
      ) : (
        <PlainCard {...props} />
      );
    case "cta":
      return (
        <Shell item={item}>
          <Meta item={item} number={number} />
          {item.title ? <Title>{item.title}</Title> : null}
          {item.body ? (
            <p className="whitespace-pre-line text-muted-foreground">
              {item.body}
            </p>
          ) : null}
          <CtaButton item={item} className="mt-1" />
        </Shell>
      );
    case "linklist":
      return item.data?.items?.length ? (
        <LinkListCard {...props} />
      ) : (
        <PlainCard {...props} />
      );
    case "media":
      return item.media_url ? (
        <Shell item={item}>
          <Meta item={item} number={number} />
          <MediaPlayer item={item} />
        </Shell>
      ) : (
        <PlainCard {...props} />
      );
    default:
      return <PlainCard {...props} />;
  }
}
