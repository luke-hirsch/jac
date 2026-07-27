import { useEffect, useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Questionnaire } from "@/components/portfolio/questionnaire";
import { useAuth } from "@/lib/auth";
import { readStamp, writeStamp } from "@/lib/portfolio/stamp";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/")({
  component: Home,
});

function Home() {
  const { status, isPending } = useAuth();
  const navigate = useNavigate();
  // Only render content once the stamp check has run — avoids a welcome-page flash
  // before a stamped visitor is redirected.
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (isPending) return; // session unknown — authenticated users are never redirected
    if (status !== "anonymous") {
      setChecked(true);
      return;
    }
    const stamp = readStamp();
    if (stamp?.kind === "link") {
      navigate({
        to: "/portfolio/$slug",
        params: { slug: stamp.slug },
        replace: true,
      });
    } else if (stamp?.kind === "native") {
      navigate({ to: "/explore", search: stamp.search, replace: true });
    } else {
      setChecked(true);
    }
  }, [isPending, status, navigate]);

  if (!checked) return null;

  const authenticated = status === "authenticated";
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-6 px-4 text-center">
      <div className="max-w-xl space-y-4">
        <h1 className="text-4xl font-bold tracking-tight">
          Welcome to my portfolio
        </h1>
        <p className="text-lg text-muted-foreground">
          Tell me what you're here for and I'll show you the right side of me.
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
          <Button asChild variant="outline">
            <Link to="/explore" search={{}}>
              Preview the public portfolio
            </Link>
          </Button>
        </div>
      ) : (
        <>
          <Questionnaire
            onDone={(search) => {
              writeStamp({
                kind: "native",
                search: { d: search.d, lucky: search.lucky },
              });
              navigate({ to: "/explore", search });
            }}
          />
          <p className="text-sm text-muted-foreground">
            Here for the CV tool?{" "}
            <Link to="/auth/login" className="underline">
              Sign in
            </Link>
          </p>
        </>
      )}
    </main>
  );
}
