import type { ReactNode } from "react";

/** A portfolio section's big heading, with something (a legend) set against it on the right. */
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
        className="font-heading text-4xl leading-tight font-bold tracking-tight md:text-5xl"
      >
        {children}
      </h2>
      {aside}
    </div>
  );
}
