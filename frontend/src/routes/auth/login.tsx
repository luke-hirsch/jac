import { createFileRoute } from "@tanstack/react-router";

type LoginSearch = { redirect?: string };

export const Route = createFileRoute("/auth/login")({
  validateSearch: (search: Record<string, unknown>): LoginSearch => ({
    redirect: typeof search.redirect === "string" ? search.redirect : undefined,
  }),
  component: () => (
    <div className="p-8">
      <h1 className="text-2xl">login (stub — Phase 2b builds this)</h1>
    </div>
  ),
});
