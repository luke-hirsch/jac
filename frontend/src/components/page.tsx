import type { ReactNode } from "react";
import { GUTTER } from "@/lib/layout";
import { cn } from "@/lib/utils";

const WIDTHS = {
  wide: "max-w-6xl", // tables: the CV, the applications
  default: "max-w-5xl", // forms: the account, the portfolio settings
  narrow: "max-w-2xl", // reading: the about page
} as const;

/** The frame of a page: its width, the gutter, the room above and below it and between its
 *  parts. jac's sections (CV, applications, portfolio, account) and the about page sit in one.
 *  The portfolio itself has its own frame (portfolio-page.tsx) on the same gutter. */
export function Page({
  width = "wide",
  as: Tag = "div",
  className,
  children,
}: {
  width?: keyof typeof WIDTHS;
  as?: "div" | "main";
  className?: string;
  children: ReactNode;
}) {
  return (
    <Tag
      className={cn(
        "mx-auto flex w-full flex-col gap-8 py-8 md:gap-10 md:py-12",
        WIDTHS[width],
        GUTTER,
        className,
      )}
    >
      {children}
    </Tag>
  );
}
