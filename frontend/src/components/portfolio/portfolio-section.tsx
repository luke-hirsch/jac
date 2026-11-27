import type { ReactNode } from "react";
import { ordinal } from "@/lib/portfolio/studio";
import { cn } from "@/lib/utils";

/** One section of a portfolio page, on the page's four-column grid: a hairline rule over it, its
 *  number and title (and a line or a legend under them) in the first column, the content across
 *  the other three. On a phone and a tablet the two stack. */
export function PortfolioSection({
  id,
  number,
  title,
  aside,
  className,
  children,
}: {
  id: string;
  /** Its place among the sections on this page (01, 02, …); none, no number. */
  number?: number;
  title: ReactNode;
  /** Under the title: a sentence on what the section shows, a legend. */
  aside?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-labelledby={id}
      className={cn(
        "grid scroll-mt-6 gap-6 border-t pt-6 md:gap-8 md:pt-8 lg:grid-cols-4 lg:gap-x-10",
        className,
      )}
    >
      <header className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          {number ? (
            <span className="text-sm font-semibold text-primary tabular-nums">
              {ordinal(number)}
            </span>
          ) : null}
          <h2
            id={id}
            className="font-heading text-3xl leading-tight font-bold tracking-tight md:text-4xl"
          >
            {title}
          </h2>
        </div>
        {aside ? (
          <div className="flex flex-col gap-3 text-sm text-muted-foreground">
            {aside}
          </div>
        ) : null}
      </header>
      <div className="min-w-0 lg:col-span-3">{children}</div>
    </section>
  );
}
