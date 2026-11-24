import { useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { EmptyCareerCard, SampleCareerNotice } from "@/components/cv/first-run";
import { QueryError } from "@/components/query-error";
import { ExportButton, ImportButton } from "@/components/transfer-buttons";
import { ApiError } from "@/lib/api";
import { prewarmForEditing } from "@/lib/prewarm";
import {
  filterByStatus,
  postingsToBatch,
  STATUS_LABELS,
  statusCounts,
  useApplications,
  useCreateApplication,
  useCreateApplicationsBatch,
  useDeleteApplication,
  type ApplicationRow,
  type ApplicationStatus,
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
  const { status } = Route.useSearch();
  const shown = filterByStatus(apps.data ?? [], status);
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
            <div className="flex flex-wrap items-center gap-2">
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
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Applications</CardTitle>
          {apps.data?.length ? (
            <CardAction>
              <ExportButton
                kind="applications"
                label="Download applications (.json)"
              />
            </CardAction>
          ) : null}
        </CardHeader>
        <CardContent>
          {apps.data?.length ? (
            <StatusFilter rows={apps.data} active={status} />
          ) : null}
          {apps.data?.length && status && !shown.length ? (
            <p className="py-3 text-sm text-muted-foreground">
              No application has the status {STATUS_LABELS[status]}.
            </p>
          ) : apps.data?.length ? (
            <ul className="divide-y">
              {shown.map((a) => (
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
          ) : apps.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                No applications yet — paste a posting above to start one.
              </p>
              {!careerEmpty && (
                <div className="flex flex-wrap items-center gap-3">
                  <ImportButton
                    kind="applications"
                    label="Import applications"
                  />
                  <p className="max-w-sm text-xs text-muted-foreground">
                    The file Download applications wrote, here or on another
                    jac. Its CVs are matched to your career by name.
                  </p>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

/** One button per status the list holds, with its count. Hidden while every application
 *  has the same status, unless a filter is on (it stays, to switch it off). */
function StatusFilter({
  rows,
  active,
}: {
  rows: ApplicationRow[];
  active: ApplicationStatus | undefined;
}) {
  const navigate = useNavigate({ from: Route.fullPath });
  const counts = statusCounts(rows);
  if (counts.length < 2 && !active) return null;

  const pick = (status: ApplicationStatus | undefined) =>
    navigate({ search: (prev) => ({ ...prev, status }), replace: true });

  const option = (
    key: string,
    label: string,
    count: number,
    status: ApplicationStatus | undefined,
  ) => (
    <Button
      key={key}
      size="sm"
      variant={active === status ? "secondary" : "ghost"}
      aria-pressed={active === status}
      onClick={() => pick(status)}
    >
      {label}
      <span className="text-muted-foreground tabular-nums">{count}</span>
    </Button>
  );

  return (
    <div
      role="group"
      aria-label="Filter by status"
      className="-mx-2 flex flex-wrap gap-1 pb-2"
    >
      {option("all", "All", rows.length, undefined)}
      {counts.map((c) =>
        option(c.status, STATUS_LABELS[c.status], c.count, c.status),
      )}
      {active && !counts.some((c) => c.status === active)
        ? option(active, STATUS_LABELS[active], 0, active)
        : null}
    </div>
  );
}
