import { createFileRoute, Link, Outlet } from "@tanstack/react-router";
import { toast } from "sonner";
import { baseDomain } from "@/lib/host";
import {
  isLive,
  portfolioAddress,
  publishNotice,
} from "@/lib/portfolio/publish";
import { useProfile, useUpdateProfile } from "@/lib/queries/profile";

const TABS = [
  { to: "/portfolio/links", label: "Links" },
  { to: "/portfolio/blocks", label: "Blocks" },
] as const;

export const Route = createFileRoute("/_authenticated/portfolio")({
  component: PortfolioLayout,
});

function PublishSwitch() {
  const profile = useProfile();
  const update = useUpdateProfile();
  if (!profile.data) return null;
  const p = profile.data;
  const base = baseDomain();
  return (
    <section className="space-y-1 rounded-md border p-4">
      <label className="flex items-center gap-2 text-sm font-medium">
        <input
          type="checkbox"
          role="switch"
          checked={isLive(p)}
          disabled={p.portfolio_always_public || update.isPending}
          onChange={(e) =>
            update.mutate(
              { portfolio_public: e.target.checked },
              { onError: () => toast.error("Could not save that") },
            )
          }
        />
        <span>
          Publish my portfolio at{" "}
          <code>{portfolioAddress(p.handle, base)}</code>
        </span>
      </label>
      <p className="text-xs text-muted-foreground">{publishNotice(p, base)}</p>
    </section>
  );
}

function PortfolioLayout() {
  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Portfolio</h1>
        <p className="text-sm text-muted-foreground">
          The personalised pages recruiters and visitors see.
        </p>
      </div>
      <PublishSwitch />
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
