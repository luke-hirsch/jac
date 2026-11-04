import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/_authenticated/portfolio/")({
  beforeLoad: () => {
    throw redirect({ to: "/portfolio/links" });
  },
});
