import { createFileRoute, Link } from "@tanstack/react-router";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/")({
  component: Home,
});

function Home() {
  const { status } = useAuth();
  const authenticated = status === "authenticated";

  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-6 px-4 text-center">
      <div className="max-w-xl space-y-4">
        <h1 className="text-4xl font-bold tracking-tight">
          Welcome to my portfolio
        </h1>
        <p className="text-lg text-muted-foreground">
          Here you'll learn more about me — and what I've built — soon. In the
          meantime, the Job Application Creator is live: build a career database
          and tailor a CV to any posting.
        </p>
      </div>

      {authenticated ? (
        <div className="flex flex-wrap items-center justify-center gap-3">
          <Button asChild>
            <Link to="/cv">Go to your CV</Link>
          </Button>
          <Button asChild variant="outline">
            <Link to="/account/profile">Profile</Link>
          </Button>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-3">
          <Button asChild size="lg">
            <Link to="/auth/signup">Create your own CV</Link>
          </Button>
          <p className="text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link to="/auth/login" className="underline">
              Sign in
            </Link>
          </p>
        </div>
      )}
    </main>
  );
}
