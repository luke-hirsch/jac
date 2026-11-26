import { createFileRoute, Link, Outlet } from "@tanstack/react-router";
import { toast } from "sonner";
import { Page } from "@/components/page";
import { Switch } from "@/components/ui/switch";
import { baseDomain } from "@/lib/host";
import {
  isLive,
  portfolioAddress,
  publishNotice,
} from "@/lib/portfolio/publish";
import { useProfile, useUpdateProfile } from "@/lib/queries/profile";
import { PageTitle } from "@/components/heading";

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
    <section className="space-y-2 border p-4 md:p-6">
      <label className="flex items-center gap-3 text-sm font-medium">
        <Switch
          checked={isLive(p)}
          disabled={p.portfolio_always_public || update.isPending}
          onCheckedChange={(on) =>
            update.mutate(
              { portfolio_public: on },
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
    <Page width="default">
      <div>
        <PageTitle>Portfolio</PageTitle>
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
    </Page>
  );
}
