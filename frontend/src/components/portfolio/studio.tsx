// Self-hosted through @fontsource, never Google Fonts: a font CDN would be a third party on every
// visit. Only the portfolio's pages import this module, so jac's own pages never load these faces.
import "@fontsource-variable/space-grotesk";
import "@fontsource-variable/inter";
import type { ComponentProps, CSSProperties, ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { studioAccent } from "@/lib/portfolio/studio";
import { cn } from "@/lib/utils";

/** A portfolio page in its owner's colour: the Studio tokens again (index.css `.studio`), with
 *  their accent made readable on the dark ground. The portfolio host's root already wears the
 *  Studio with the default accent; this wrapper is what makes it the owner's. `as="main"` lets a
 *  page's own <main> be the wrapper. */
export function Studio({
  as: Tag = "div",
  accent,
  className,
  children,
}: {
  as?: "div" | "main";
  accent?: string | null;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Tag
      className={cn("studio", className)}
      style={{ "--studio-accent": studioAccent(accent) } as CSSProperties}
    >
      {children}
    </Tag>
  );
}

/** jac's buttons are square and set in capitals; the Studio's are rounded and set in Inter. The
 *  outline gets the brighter `--input` line: `--border` is drawn for cards and too faint here. */
export function StudioButton({
  className,
  ...props
}: ComponentProps<typeof Button>) {
  return (
    <Button
      className={cn(
        "rounded-[10px] text-sm font-semibold tracking-normal normal-case data-[variant=outline]:border-input",
        className,
      )}
      {...props}
    />
  );
}

/** A section's big heading, with something (a legend) set against it on the right. */
export function SectionHeading({
  id,
  aside,
  children,
}: {
  id?: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
      <h2
        id={id}
        className="font-display text-[clamp(2.125rem,4.5vw,3.25rem)] leading-tight font-bold tracking-[-0.03em]"
      >
        {children}
      </h2>
      {aside}
    </div>
  );
}
