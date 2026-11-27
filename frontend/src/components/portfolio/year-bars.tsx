import type { CSSProperties } from "react";
import { PortfolioSection } from "@/components/portfolio/portfolio-section";
import { anchorId } from "@/lib/portfolio/content";
import { scrollMotion, yearSpan } from "@/lib/portfolio/studio";
import type { YearAxis, YearBar } from "@/lib/portfolio/timeline";
import { cn } from "@/lib/utils";

/** Work in the accent, fading toward stone the longer ago it ended; study as a dashed outline. */
function barStyle(bar: YearBar): CSSProperties {
  const at = { left: `${bar.left}%`, width: `${bar.width}%` };
  if (bar.kind === "study") return at;
  const pct = Math.round(40 + 60 * bar.recency);
  return {
    ...at,
    background: `color-mix(in oklch, var(--net-new) ${pct}%, var(--net-old))`,
  };
}

function Swatch({ kind }: { kind: YearBar["kind"] }) {
  return (
    <span
      aria-hidden
      className={cn(
        "h-2 w-4.5 rounded-sm",
        kind === "work"
          ? "bg-primary"
          : "border-2 border-dashed border-muted-foreground",
      )}
    />
  );
}

/** The page's jobs and education on one year axis (lib/portfolio/timeline.ts). The bars only
 *  draw what each row says in words: the dates are written next to every title, so the drawing
 *  is aria-hidden. On a phone the title goes above its bar, and the axis keeps the full width. */
export function YearBars({
  axis,
  cards,
  number,
}: {
  axis: YearAxis;
  /** Ids that have a card on the page: a title jumps there. */
  cards: Set<string>;
  /** The section's place on the page. */
  number?: number;
}) {
  const kinds = [...new Set(axis.rows.map((r) => r.kind))];
  const legend = (
    <div className="flex gap-4 text-xs">
      {kinds.map((k) => (
        <span key={k} className="inline-flex items-center gap-1.5">
          <Swatch kind={k} />
          {k}
        </span>
      ))}
    </div>
  );
  return (
    <PortfolioSection
      id="experience"
      number={number}
      title="Experience"
      aside={legend}
    >
      <div className="border p-5 md:p-8">
        <div
          aria-hidden
          className="grid text-xs text-muted-foreground tabular-nums sm:grid-cols-[13rem_minmax(0,1fr)] sm:gap-x-4"
        >
          <span className="hidden sm:block" />
          <div className="relative h-5">
            {axis.ticks.map((t) => (
              <span
                key={t.year}
                className="absolute top-0 border-l border-border pl-1"
                style={{ left: `${t.left}%` }}
              >
                {t.year}
              </span>
            ))}
          </div>
        </div>
        <ol className="mt-3 flex flex-col gap-4 sm:gap-3">
          {axis.rows.map((bar) => (
            <li
              key={bar.item.id}
              className="grid items-center gap-x-4 gap-y-1.5 sm:grid-cols-[13rem_minmax(0,1fr)]"
            >
              <div className="min-w-0">
                {cards.has(bar.item.id) ? (
                  <a
                    href={`#${anchorId(bar.item.id)}`}
                    className="font-semibold no-underline hover:underline"
                    onClick={(e) => {
                      const el = document.getElementById(anchorId(bar.item.id));
                      if (!el) return;
                      e.preventDefault();
                      el.scrollIntoView({
                        behavior: scrollMotion(),
                        block: "start",
                      });
                    }}
                  >
                    {bar.item.title}
                  </a>
                ) : (
                  <span className="font-semibold">{bar.item.title}</span>
                )}
                <p className="truncate text-xs text-muted-foreground">
                  {[bar.item.subtitle, yearSpan(bar.item)]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
              </div>
              <div aria-hidden className="relative h-3.5">
                <span
                  className={cn(
                    "absolute inset-y-0",
                    bar.kind === "study" &&
                      "border-2 border-dashed border-muted-foreground",
                  )}
                  style={barStyle(bar)}
                />
              </div>
            </li>
          ))}
        </ol>
      </div>
    </PortfolioSection>
  );
}
