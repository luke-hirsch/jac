import { useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { ApplicationContentCard } from "@/components/applications/content-card";
import { ExportCard } from "@/components/applications/export-card";
import { GeneratePanel } from "@/components/applications/generate-panel";
import { PostingCard } from "@/components/applications/posting-card";
import { ResultView } from "@/components/applications/result-view";
import { useRunLifecycle } from "@/components/applications/use-run-lifecycle";
import { editableBody } from "@/lib/letter-doc";
import { useApplication } from "@/lib/queries/applications";

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

      <PostingCard app={app.data} />
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
      />
      {run.state.result && (
        <ResultView
          applicationId={id}
          state={run.state}
          applied={
            app.data.cover_letter ===
            editableBody(run.state.result.cover_letter)
          }
        />
      )}
      <ApplicationContentCard app={app.data} />
      <ExportCard app={app.data} />
    </div>
  );
}
