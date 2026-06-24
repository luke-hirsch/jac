import { createFileRoute, Link, Outlet } from "@tanstack/react-router";
import { Card } from "@/components/ui/card";

const ITEMS = [
  { to: "/account/profile", label: "Profile" },
  { to: "/account/email", label: "Email addresses" },
  { to: "/account/security", label: "Security" },
  { to: "/account/llm", label: "LLM providers" },
  { to: "/account/danger", label: "Danger zone" },
] as const;

export const Route = createFileRoute("/_authenticated/account")({
  component: AccountLayout,
});

function AccountLayout() {
  return (
    <div className="max-w-5xl mx-auto p-6 grid gap-6 md:grid-cols-[180px_1fr]">
      <nav className="space-y-1">
        {ITEMS.map((i) => (
          <Link
            key={i.to}
            to={i.to}
            className="block px-3 py-2 rounded-md text-sm hover:bg-muted"
            activeProps={{ className: "bg-muted font-medium" }}
          >
            {i.label}
          </Link>
        ))}
      </nav>
      <Card className="p-6">
        <Outlet />
      </Card>
    </div>
  );
}
