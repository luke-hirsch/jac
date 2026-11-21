import { createFileRoute, Link, Outlet } from "@tanstack/react-router";
import { Card } from "@/components/ui/card";

// `nested`: a page under the item above it (Claude sits with the LLM providers: both send the
// career to a provider, behind the same consent).
const ITEMS: { to: string; label: string; nested?: boolean }[] = [
  { to: "/account/profile", label: "Profile" },
  { to: "/account/email", label: "Email addresses" },
  { to: "/account/security", label: "Security" },
  { to: "/account/llm", label: "LLM providers" },
  { to: "/account/llm/claude", label: "Claude", nested: true },
  { to: "/account/danger", label: "Danger zone" },
  { to: "/account/personality", label: "Personality" },
];

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
            className={`block py-2 rounded-md text-sm hover:bg-muted ${i.nested ? "pl-7 pr-3" : "px-3"}`}
            // Exact: /account/llm would light up on /account/llm/claude too.
            activeOptions={{ exact: true }}
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
