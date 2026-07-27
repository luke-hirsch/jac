import { createFileRoute, Link, Outlet } from "@tanstack/react-router";

const TABS = [
  { to: "/portfolio/links", label: "Links" },
  { to: "/portfolio/blocks", label: "Blocks" },
] as const;

export const Route = createFileRoute("/_authenticated/portfolio")({
  component: PortfolioLayout,
});

function PortfolioLayout() {
  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Portfolio</h1>
        <p className="text-sm text-muted-foreground">
          The personalised pages recruiters and visitors see.
        </p>
      </div>
      <nav className="flex gap-2 border-b">
        {TABS.map((t) => (
          <Link
            key={t.to}
            to={t.to}
            className="px-3 py-2 text-sm -mb-px border-b-2 border-transparent hover:border-muted-foreground/40"
            activeProps={{ className: "border-primary font-medium" }}
          >
            {t.label}
          </Link>
        ))}
      </nav>
      <Outlet />
    </div>
  );
}
