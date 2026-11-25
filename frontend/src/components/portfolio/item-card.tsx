import type { ReactNode } from "react";
import { MediaPlayer } from "@/components/portfolio/media-player";
import { StudioMarkdown } from "@/components/portfolio/studio-markdown";
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

/** The card every kind but the quote and the link list sits in. In the page's grid of 340 px
 *  columns it takes one; `wide` takes the whole row (text reads better long). */
function Shell({
  item,
  wide,
  className,
  children,
}: {
  item: PortfolioItem;
  wide?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <article
      id={anchorId(item.id)}
      className={cn(
        "flex min-w-0 scroll-mt-6 flex-col gap-3 rounded-[18px] border bg-card p-6 sm:p-7",
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
    <div className="flex justify-between gap-3 text-[13px] text-muted-foreground">
      {number ? <span className="font-display">{ordinal(number)}</span> : null}
      <span className={number ? "text-right" : ""}>{line}</span>
    </div>
  );
}

function Title({ children }: { children: ReactNode }) {
  return (
    <h3 className="font-display text-[1.625rem] leading-tight font-semibold tracking-[-0.01em]">
      {children}
    </h3>
  );
}

/** A call to action's button, in the accent. */
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
    <a
      href={href}
      {...leaves(href)}
      className={cn(
        "inline-flex min-h-[46px] w-fit items-center rounded-[10px] bg-studio-accent px-5 py-3 font-semibold text-studio-ink no-underline hover:brightness-110",
        className,
      )}
    >
      {item.data.label}
    </a>
  );
}

/** The hero's "now" card: the page's first call to action, as the mock-up has it. */
export function NowCard({ item }: { item: PortfolioItem }) {
  return (
    <aside
      id={anchorId(item.id)}
      aria-label={item.title || item.data?.label}
      className="flex min-w-0 flex-[1_1_20rem] scroll-mt-6 flex-col gap-[18px] rounded-[18px] border bg-card p-6 sm:p-7"
    >
      {item.title ? (
        <div className="flex items-center gap-2.5">
          <span
            aria-hidden
            className="size-2.5 shrink-0 rounded-full bg-studio-accent"
          />
          <span className="font-display text-lg font-semibold">
            {item.title}
          </span>
        </div>
      ) : null}
      {item.body ? (
        <p className="text-[15px] whitespace-pre-line text-studio-soft">
          {item.body}
        </p>
      ) : null}
      <CtaButton item={item} />
    </aside>
  );
}

/** Key figures as tiles: the value big, in the accent, its label under it. */
function Figures({
  figures,
  inCard,
}: {
  figures: BlockFigure[];
  inCard?: boolean;
}) {
  return (
    <dl className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,10rem),1fr))] gap-4">
      {figures.map((f, i) => (
        <div
          key={i}
          className={cn(
            "flex flex-col gap-1.5 rounded-2xl",
            inCard ? "bg-studio-raise p-5" : "border bg-card p-6",
          )}
        >
          <dt className="order-2 text-sm text-muted-foreground">{f.label}</dt>
          <dd
            className={cn(
              "order-1 font-display leading-none font-bold tracking-[-0.03em] [overflow-wrap:anywhere] text-studio-accent",
              inCard ? "text-[2.5rem]" : "text-[clamp(2.5rem,5vw,3.75rem)]",
            )}
          >
            {f.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** The tiles under the hero: the page's first key-figures block. */
export function FigureTiles({ item }: { item: PortfolioItem }) {
  return (
    <section
      id={anchorId(item.id)}
      aria-label={item.title || "Key figures"}
      className="flex scroll-mt-6 flex-col gap-4"
    >
      {item.title || item.body ? (
        <div className="flex flex-col gap-1">
          {item.title ? (
            <h2 className="font-display text-xl font-semibold">{item.title}</h2>
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
      className="m-0 flex min-w-0 scroll-mt-6 flex-col justify-between gap-5 rounded-[18px] bg-studio-accent p-6 text-studio-ink sm:p-7"
    >
      {label ? (
        <span className="font-display text-[13px] font-semibold">{label}</span>
      ) : null}
      <blockquote className="font-display text-[1.5625rem] leading-snug font-medium tracking-[-0.01em] whitespace-pre-line">
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
      className="flex min-w-0 scroll-mt-6 flex-col"
    >
      <Meta item={item} number={number} />
      {item.title ? (
        <h3 className="mt-1 mb-2 font-display text-[1.75rem] font-bold tracking-[-0.02em]">
          {item.title}
        </h3>
      ) : null}
      {item.body ? <p className="mb-3 text-studio-soft">{item.body}</p> : null}
      <ul className="border-b">
        {rows.map((row, i) => (
          <li key={i} className="border-t">
            <a
              href={row.href}
              {...leaves(row.href)}
              className="flex min-h-11 items-center gap-4 py-4 no-underline"
            >
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-xs tracking-[0.08em] text-studio-accent uppercase">
                  {hostLabel(row.href)}
                </span>
                <span className="font-semibold">{row.title}</span>
                {row.note ? (
                  <span className="text-sm text-muted-foreground">
                    {row.note}
                  </span>
                ) : null}
              </span>
              <span aria-hidden className="text-[22px]">
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
    <Shell item={item} className="gap-0 overflow-hidden p-0 sm:p-0">
      {item.image_url ? (
        <img
          src={item.image_url}
          alt={item.alt_text || item.title}
          loading="lazy"
          className="max-h-[28rem] w-full object-cover"
        />
      ) : null}
      {text ? (
        <div className="flex flex-col gap-2.5 p-6 sm:p-7">
          <Meta item={item} number={number} />
          {item.title ? <Title>{item.title}</Title> : null}
          {item.body ? (
            <p className="whitespace-pre-line text-studio-soft">{item.body}</p>
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
        <span className="text-[15px] text-muted-foreground">
          {item.subtitle}
        </span>
      ) : null}
      {item.description ? (
        <p className="whitespace-pre-line text-studio-soft">
          {item.description}
        </p>
      ) : null}
      {href ? (
        <a
          href={href}
          {...leaves(href)}
          className="w-fit text-[15px] underline underline-offset-[3px]"
        >
          {hostLabel(href)} ↗
        </a>
      ) : null}
      {skills.length ? (
        <ul aria-label="Skills" className="flex flex-wrap gap-1.5 pt-1">
          {skills.slice(0, MAX_CHIPS).map((name, i) => (
            <li
              key={i}
              className="rounded-md bg-studio-raise px-2.5 py-0.5 text-[13px]"
            >
              {name}
            </li>
          ))}
          {skills.length > MAX_CHIPS ? (
            <li className="px-1 py-0.5 text-[13px] text-muted-foreground">
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
        <p className="whitespace-pre-line text-studio-soft">{item.body}</p>
      ) : null}
      {item.links?.length ? (
        <LinkedItems items={item.links} anchors={anchors} />
      ) : null}
    </Shell>
  );
}

/** One item on the Studio page, drawn by what it is. `number` is its place in the highlights;
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
            <p className="whitespace-pre-line text-studio-soft">{item.body}</p>
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
