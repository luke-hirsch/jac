import { useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { EmptyCareerCard, SampleCareerNotice } from "@/components/cv/first-run";
import { QueryError } from "@/components/query-error";
import { ApiError } from "@/lib/api";
import { prewarmForEditing } from "@/lib/prewarm";
import {
  postingsToBatch,
  STATUS_LABELS,
  useApplications,
  useCreateApplication,
  useCreateApplicationsBatch,
  useDeleteApplication,
  type ApplicationRow,
} from "@/lib/queries/applications";
import {
  hasSampleEntries,
  isCareerEmpty,
  useCvEntries,
} from "@/lib/queries/jac";
import { hasOwnName, useProfile } from "@/lib/queries/profile";

export const Route = createFileRoute("/_authenticated/applications/")({
  component: ApplicationsPage,
});

const RUN_BADGES = {
  pending: "queued",
  running: "generating",
  failed: "run failed",
} as const;

function runBadge(row: ApplicationRow): string | null {
  const status = row.runs[0]?.status;
  return status && status !== "done" ? RUN_BADGES[status] : null;
}

function ApplicationsPage() {
  const navigate = useNavigate();
  const apps = useApplications();
  const create = useCreateApplication();
  const batch = useCreateApplicationsBatch();
  const destroy = useDeleteApplication();
  const career = useCvEntries();
  const profile = useProfile();
  const careerEmpty = career.data ? isCareerEmpty(career.data) : false;
  const [postingText, setPostingText] = useState("");
  const [several, setSeveral] = useState(false);
  const postings = postingsToBatch(postingText, several);

  async function onCreateBatch() {
    try {
      const created = await batch.mutateAsync(postings);
      setPostingText("");
      toast.success(
        `${created.length} runs queued. You can leave: you'll get one mail when they're done.`,
      );
    } catch (e) {
      const detail =
        e instanceof ApiError ? (e.data as { detail?: string })?.detail : null;
      toast.error(detail ?? "Could not queue the postings");
    }
  }

  async function onCreate() {
    let app;
    try {
      app = await create.mutateAsync(postingText);
      setPostingText("");
    } catch {
      toast.error("Could not create the application");
      return;
    }
    // The backend queues a standard generation on the user's default executor
    // (perform_create) — the SPA no longer POSTs a run here. The detail page seeds from
    // runs[0] and streams it.
    navigate({
      to: "/applications/$applicationId",
      params: { applicationId: String(app.id) },
    });
  }

  return (
    <div className="space-y-6">
      {career.data && hasSampleEntries(career.data) && <SampleCareerNotice />}
      {profile.data && !hasOwnName(profile.data) && (
        <p className="text-sm text-muted-foreground">
          Your CV and letter carry your username for now.{" "}
          <Link to="/account/profile" className="underline">
            Add your name
          </Link>
        </p>
      )}

      {careerEmpty && <EmptyCareerCard />}
      {/* A failed career check keeps the form: this page never locks anyone out over it. */}
      {!career.isPending && !careerEmpty && (
        <Card>
          <CardHeader>
            <CardTitle>New application</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Textarea
              aria-label="Job posting"
              rows={8}
              placeholder="Paste the job posting…"
              value={postingText}
              onChange={(e) => {
                setPostingText(e.target.value);
                prewarmForEditing(); // a posting on its way means a run
              }}
            />
            <div className="flex items-center gap-2">
              <Checkbox
                id="several-postings"
                checked={several}
                onCheckedChange={(v) => setSeveral(!!v)}
              />
              <Label htmlFor="several-postings">Several postings</Label>
            </div>
            {several && (
              <p className="text-xs text-muted-foreground">
                Separate the postings with a line of three dashes (---). They
                all wake the AI once, and you get one mail at the end.
              </p>
            )}
            {postings.length > 0 ? (
              <Button onClick={onCreateBatch} disabled={batch.isPending}>
                {batch.isPending
                  ? "Queueing…"
                  : `Queue ${postings.length} applications`}
              </Button>
            ) : (
              <Button
                onClick={onCreate}
                disabled={create.isPending || !postingText.trim()}
              >
                {create.isPending ? "Creating…" : "Create application"}
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Applications</CardTitle>
        </CardHeader>
        <CardContent>
          {apps.data?.length ? (
            <ul className="divide-y">
              {apps.data.map((a) => (
                <li
                  key={a.id}
                  className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-3"
                >
                  <div className="min-w-0 max-w-full">
                    <Link
                      to="/applications/$applicationId"
                      params={{ applicationId: String(a.id) }}
                      className="font-medium hover:underline"
                    >
                      {a.posting_detail.title || "Untitled posting"}
                    </Link>
                    <p className="truncate text-xs text-muted-foreground">
                      {a.posting_detail.posting_text.slice(0, 120)}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    {a.deadline && (
                      <span className="text-xs text-muted-foreground">
                        due {new Date(a.deadline).toLocaleDateString()}
                      </span>
                    )}
                    {runBadge(a) && (
                      <Badge variant="secondary">{runBadge(a)}</Badge>
                    )}
                    <Badge variant="outline">{STATUS_LABELS[a.status]}</Badge>
                    <span className="text-xs text-muted-foreground">
                      {a.runs.length} run{a.runs.length === 1 ? "" : "s"} ·{" "}
                      {new Date(a.created_at).toLocaleDateString()}
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        if (!confirm("Delete this application and its runs?"))
                          return;
                        destroy.mutate(a.id);
                      }}
                    >
                      Delete
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          ) : apps.isError ? (
            <QueryError
              error={apps.error}
              what="your applications"
              onRetry={() => apps.refetch()}
              retrying={apps.isFetching}
            />
          ) : (
            <p className="text-sm text-muted-foreground">
              {apps.isLoading
                ? "Loading…"
                : "No applications yet — paste a posting above to start one."}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
