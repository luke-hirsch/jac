import {
  createFileRoute,
  isRedirect,
  Link,
  Outlet,
  redirect,
  useNavigate,
} from "@tanstack/react-router";
import { useMutation } from "@tanstack/react-query";
import {
  fetchSession,
  signOut,
  useAuth,
  useInvalidateSession,
} from "@/lib/auth";
import { useBrand } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { jacOrigin, leaveTo, siteHost } from "@/lib/host";
import { GUTTER } from "@/lib/layout";
import { useProfile } from "@/lib/queries/profile";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/_authenticated")({
  beforeLoad: async ({ location }) => {
    if (siteHost() !== "jac") return leaveTo(jacOrigin() + location.href);
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
  // jac wears the user's colour, else the site owner's (the profile's `brand`). Same query key
  // as the appearance provider's, so no second request; a saved colour shows at once.
  useBrand(useProfile().data?.brand);
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
      <header
        className={cn(
          "flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b py-3",
          GUTTER,
        )}
      >
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <Link to="/" className="font-semibold">
            lukehirsch
          </Link>
          <nav className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
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
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <Link
            to="/account/profile"
            className="min-w-0 break-all hover:underline"
          >
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
