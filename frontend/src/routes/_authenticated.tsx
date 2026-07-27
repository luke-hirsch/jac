import {
  createFileRoute,
  isRedirect,
  Link,
  Outlet,
  redirect,
  useNavigate,
} from "@tanstack/react-router";
import { useMutation } from "@tanstack/react-query";
import { fetchSession, signOut, useAuth, useInvalidateSession } from "@/lib/auth";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/_authenticated")({
  beforeLoad: async ({ location }) => {
    try {
      const s = await fetchSession();
      if (s.meta?.is_authenticated) return;
      const pending = s.data?.flows?.find((f) => f.is_pending)?.id;
      throw redirect({
        to:
          pending === "verify_email"
            ? "/auth/verify-email"
            : pending === "mfa_authenticate"
              ? "/auth/mfa-challenge"
              : "/auth/login",
        search: { redirect: location.href },
      });
    } catch (e) {
      if (isRedirect(e)) throw e;
      throw redirect({
        to: "/auth/login",
        search: { redirect: location.href },
      });
    }
  },
  component: AuthedLayout,
});

function AuthedLayout() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const invalidate = useInvalidateSession();
  const logout = useMutation({
    mutationFn: signOut,
    onSuccess: async () => {
      await invalidate();
      navigate({ to: "/auth/login", search: {} as never });
    },
  });

  return (
    <div className="min-h-screen flex flex-col">
      <header className="flex items-center justify-between border-b px-4 py-2">
        <div className="flex items-center gap-4">
          <Link to="/" className="font-semibold">
            lukehirsch
          </Link>
          <nav className="flex items-center gap-3 text-sm">
            <Link
              to="/cv"
              className="hover:underline"
              activeProps={{ className: "font-medium underline" }}
            >
              CV
            </Link>
            <Link
              to="/applications"
              className="hover:underline"
              activeProps={{ className: "font-medium underline" }}
            >
              Applications
            </Link>
            <Link
              to="/portfolio/links"
              className="hover:underline"
              activeProps={{ className: "font-medium underline" }}
            >
              Portfolio
            </Link>
          </nav>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <Link to="/account/profile" className="hover:underline">
            {user?.email}
          </Link>
          <Button variant="ghost" size="sm" onClick={() => logout.mutate()}>
            Sign out
          </Button>
        </div>
      </header>
      <main className="flex-1">
        <Outlet />
      </main>
    </div>
  );
}
