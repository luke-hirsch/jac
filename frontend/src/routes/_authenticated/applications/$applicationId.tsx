import { useEffect, useRef, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { ApplicationContentCard } from "@/components/applications/content-card";
import { ExportCard } from "@/components/applications/export-card";
import { GeneratePanel } from "@/components/applications/generate-panel";
import { PostingCard } from "@/components/applications/posting-card";
import { useFreshHighlight } from "@/components/applications/use-fresh-highlight";
import { useRunLifecycle } from "@/components/applications/use-run-lifecycle";
import { editableBody } from "@/lib/letter-doc";
import { useApplication } from "@/lib/queries/applications";
import { LifecycleCard } from "@/components/applications/lifecycle-card";

export const Route = createFileRoute(
  "/_authenticated/applications/$applicationId",
)({
  component: ApplicationDetailPage,
});

function ApplicationDetailPage() {
  const { applicationId } = Route.useParams();
  const id = Number(applicationId);
  const app = useApplication(id);

  // The user's explicit pick wins; otherwise the latest run (refresh-safe rehydrate).
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const runId = selectedRunId ?? app.data?.runs[0]?.id ?? null;
  const run = useRunLifecycle(runId);

  // A finishing run arms the highlight; the auto-fill/apply that follows glows for a
  // minute in the content card, then fades — no separate result card.
  const highlight = useFreshHighlight(app.data);
  const prevStatus = useRef(run.state.status);
  useEffect(() => {
    if (run.state.status === "done" && prevStatus.current !== "done") {
      highlight.arm();
    }
    prevStatus.current = run.state.status;
  });

  if (app.isLoading)
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (!app.data)
    return <p className="text-sm text-destructive">Application not found.</p>;

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
        <div className="space-y-6">
          <ApplicationContentCard app={app.data} fresh={highlight.fresh} />
          <ExportCard app={app.data} />
        </div>
      </div>
    </div>
  );
}
