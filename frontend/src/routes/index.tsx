import { createFileRoute, redirect } from "@tanstack/react-router";
import { apexOrigin, jacOrigin, leaveTo, siteHost } from "@/lib/host";

/** "/" renders nothing on any host. The jac host's home is the applications list, and the
 *  portfolio host's root is the apex. The apex itself is Django's landing in prod (nginx never
 *  loads the SPA there), so this branch only runs in vite dev, where it opens the app. */
export const Route = createFileRoute("/")({
  head: () => ({ meta: [{ name: "robots", content: "noindex" }] }),
  beforeLoad: ({ location }) => {
    const host = siteHost();
    if (host === "jac") throw redirect({ to: "/applications" });
    if (host === "portfolio") return leaveTo(apexOrigin() + location.href);
    return leaveTo(jacOrigin());
  },
});
