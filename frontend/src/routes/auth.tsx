import { createFileRoute, Outlet, Link } from "@tanstack/react-router";
import { fetchSession } from "@/lib/auth";

export const Route = createFileRoute("/auth")({
  // Bootstrap the csrftoken cookie before any child renders. allauth's
  // browser_view decorator calls get_token() on every /_allauth/browser/v1/*
  // hit, so any GET seeds the cookie the signup/login POSTs need.
  beforeLoad: async () => {
    await fetchSession();
  },
  component: () => (
    <div className="min-h-screen grid place-items-center p-4 bg-muted/30">
      <div className="w-full max-w-sm space-y-6">
        <Link
          to="/"
          className="block text-center text-sm text-muted-foreground hover:underline"
        >
          ← back to home
        </Link>
        <Outlet />
      </div>
    </div>
  ),
});
