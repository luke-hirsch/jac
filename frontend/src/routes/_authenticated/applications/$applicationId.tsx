import { useEffect, useRef, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { toast } from "sonner";
import { DraftedCards } from "@/components/applications/drafted-cards";
import { GeneratePanel } from "@/components/applications/generate-panel";
import { PostingCard } from "@/components/applications/posting-card";
import { useFreshHighlight } from "@/components/applications/use-fresh-highlight";
import { useRunLifecycle } from "@/components/applications/use-run-lifecycle";
import { editableBody } from "@/lib/letter-doc";
import { prewarmForEditing } from "@/lib/prewarm";
import { useApplication } from "@/lib/queries/applications";
import { isRunInFlight } from "@/lib/queries/generations";
import { LifecycleCard } from "@/components/applications/lifecycle-card";
import { QueryError } from "@/components/query-error";
import { ApiError } from "@/lib/api";

export const Route = createFileRoute(
  "/_authenticated/applications/$applicationId",
)({
  component: ApplicationDetailPage,
});

function ApplicationDetailPage() {
  const { applicationId } = Route.useParams();
  const id = Number(applicationId);
  const app = useApplication(id);

  // A draft is about to be edited or run. One that went out is usually just being looked at.
  const isDraft = app.data?.status === "draft";
  useEffect(() => {
    if (isDraft) prewarmForEditing();
  }, [isDraft]);

  // The user's explicit pick wins; otherwise the latest run (refresh-safe rehydrate).
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const runId = selectedRunId ?? app.data?.runs[0]?.id ?? null;
  const run = useRunLifecycle(runId);

  // A finishing run arms the highlight; the auto-fill/apply that follows glows for a
  // minute in the content card, then fades — no separate result card. Only a run seen in
  // flight counts: opening an old one seeds "done" without anything having finished. The id
  // keeps a stale poll's done → running → done from toasting twice.
  const highlight = useFreshHighlight(app.data);
  const prevStatus = useRef(run.state.status);
  useEffect(() => {
    if (run.state.status === "done" && isRunInFlight(prevStatus.current)) {
      highlight.arm();
      toast.success("Generation finished", { id: `run-done-${runId}` });
    }
    prevStatus.current = run.state.status;
  });

  if (app.isLoading)
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (!app.data) {
    if (app.error instanceof ApiError && app.error.status === 404)
      return <p className="text-sm text-destructive">Application not found.</p>;
    return (
      <QueryError
        error={app.error}
        what="this application"
        onRetry={() => app.refetch()}
        retrying={app.isFetching}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">
          {app.data.posting_detail.title || "Untitled posting"}
        </h1>
        <Link to="/applications" className="text-sm hover:underline">
          ← All applications
        </Link>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-[2fr_3fr]">
        <div className="space-y-6">
          <PostingCard app={app.data} />
          <LifecycleCard app={app.data} />
          <GeneratePanel
            app={app.data}
            activeRunId={runId}
            onRunSelected={setSelectedRunId}
            runState={run.state}
            runCreatedAt={run.runCreatedAt}
            now={run.now}
            socket={run.socket}
            onAbort={run.abort}
            aborting={run.aborting}
            applied={
              run.state.result != null &&
              app.data.cover_letter ===
                editableBody(run.state.result.cover_letter)
            }
            onApplied={highlight.arm}
          />
        </div>
        <DraftedCards
          key={app.data.id}
          app={app.data}
          fresh={highlight.fresh}
        />
      </div>
    </div>
  );
}
