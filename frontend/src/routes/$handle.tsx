import { createFileRoute, Outlet } from "@tanstack/react-router";
import { leaveTo, portfolioOrigin, siteHost } from "@/lib/host";

/** One owner's portfolio: portfolio.<base>/<handle>/ and its links below it. These pages live on the
 *  portfolio host only; anywhere else the same path moves there. A static top-level route (auth, cv,
 *  …) always wins over a handle, which is why the backend reserves those names. */
export const Route = createFileRoute("/$handle")({
  head: () => ({ meta: [{ name: "robots", content: "noindex" }] }),
  beforeLoad: ({ location }) => {
    if (siteHost() !== "portfolio")
      return leaveTo(portfolioOrigin() + location.href);
  },
  component: Outlet,
});
