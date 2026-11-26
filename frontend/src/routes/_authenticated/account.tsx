import { createFileRoute, Link, Outlet } from "@tanstack/react-router";
import { Page } from "@/components/page";
import { Card } from "@/components/ui/card";

// `nested`: a page under the item above it.
const ITEMS: { to: string; label: string; nested?: boolean }[] = [
  { to: "/account/profile", label: "Profile" },
  { to: "/account/email", label: "Email addresses" },
  { to: "/account/security", label: "Security" },
  { to: "/account/llm", label: "LLM providers" },
  { to: "/account/danger", label: "Danger zone" },
  { to: "/account/personality", label: "Personality" },
];

export const Route = createFileRoute("/_authenticated/account")({
  component: AccountLayout,
});

function AccountLayout() {
  return (
    <Page width="default" className="md:grid md:grid-cols-4">
      <nav className="space-y-1">
        {ITEMS.map((i) => (
          <Link
            key={i.to}
            to={i.to}
            className={`block py-2 rounded-md text-sm hover:bg-muted ${i.nested ? "pl-7 pr-3" : "px-3"}`}
            // Exact: a parent mustn't light up on a page nested under it.
            activeOptions={{ exact: true }}
            activeProps={{ className: "bg-muted font-medium" }}
          >
            {i.label}
          </Link>
        ))}
      </nav>
      <Card className="p-6 md:col-span-3 md:p-8">
        <Outlet />
      </Card>
    </Page>
  );
}
