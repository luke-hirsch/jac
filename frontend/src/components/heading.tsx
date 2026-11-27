import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/** A page's title: one per page (a CV section, an application, the portfolio settings, the
 *  about page). The portfolio's own headings are in portfolio-page.tsx and section-heading.tsx. */
export function PageTitle({ className, ...props }: ComponentProps<"h1">) {
  return (
    <h1
      className={cn(
        "font-heading text-3xl leading-tight font-bold tracking-tight text-balance wrap-anywhere md:text-4xl",
        className,
      )}
      {...props}
    />
  );
}

/** A section's title inside a page (an account page's parts, the attachments' files). */
export function SectionTitle({ className, ...props }: ComponentProps<"h2">) {
  return (
    <h2
      className={cn(
        "font-heading text-lg leading-snug font-semibold tracking-tight",
        className,
      )}
      {...props}
    />
  );
}
