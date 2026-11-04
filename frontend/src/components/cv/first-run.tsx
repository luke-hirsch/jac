import { Link } from "@tanstack/react-router";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api";
import { useLoadSampleCareer } from "@/lib/queries/jac";

/** Shown instead of the posting form while there is nothing for a run to write from. */
export function EmptyCareerCard() {
  const load = useLoadSampleCareer();

  // mutateAsync, not mutate's callbacks: the refetch that ends the mutation also unmounts this card,
  // and callbacks passed to mutate() don't fire on an unmounted component.
  async function onLoad() {
    try {
      await load.mutateAsync();
      toast.success(
        "Sample career loaded. Paste a job posting to see what jac makes of it.",
      );
    } catch (e) {
      const detail =
        e instanceof ApiError ? (e.data as { detail?: string })?.detail : null;
      toast.error(detail ?? "Could not load the sample career");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Your career is empty</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm">
          The CV and the letter are written from your career entries. There are
          none yet, so a job posting would only get you an empty result.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button onClick={onLoad} disabled={load.isPending}>
            {load.isPending ? "Loading…" : "Try it with a sample career"}
          </Button>
          <Button variant="outline" asChild>
            <Link to="/cv/jobs">Add a job first</Link>
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          The sample is a made-up person, loaded in one click, so you can see a
          real run without typing a CV. Its entries are marked (sample): nothing
          made from them is meant to be sent.
        </p>
      </CardContent>
    </Card>
  );
}

/** Stays up for as long as sample entries are in the career. */
export function SampleCareerNotice() {
  return (
    <p className="rounded-md border p-3 text-sm">
      You're trying jac with a sample career: a made-up person, marked (sample)
      in the CV. Don't send what comes out of it.{" "}
      <Link to="/cv" className="underline">
        Replace it with your own
      </Link>
    </p>
  );
}
