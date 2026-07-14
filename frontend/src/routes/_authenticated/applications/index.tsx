import { useState } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  STATUS_LABELS,
  useApplications,
  useCreateApplication,
  useDeleteApplication,
} from "@/lib/queries/applications";
import { useCreateGeneration } from "@/lib/queries/generations";

export const Route = createFileRoute("/_authenticated/applications/")({
  component: ApplicationsPage,
});

function ApplicationsPage() {
  const navigate = useNavigate();
  const apps = useApplications();
  const create = useCreateApplication();
  const createRun = useCreateGeneration();
  const destroy = useDeleteApplication();
  const [postingText, setPostingText] = useState("");

  async function onCreate() {
    let app;
    try {
      app = await create.mutateAsync(postingText);
      setPostingText("");
    } catch {
      toast.error("Could not create the application");
      return;
    }
    // Kick off the zero-cost default run right away — it fills the still-empty
    // application (task-side fill-if-empty), so the detail page opens with a
    // draft already generating instead of an empty shell.
    try {
      await createRun.mutateAsync({
        job_application: app.id,
        grade: "light",
        alias: "default",
        verify_grounding: false,
        personal_paragraph: false,
      });
    } catch {
      toast.warning(
        "Could not start the automatic light run — generate manually.",
      );
    }
    navigate({
      to: "/applications/$applicationId",
      params: { applicationId: String(app.id) },
    });
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>New application</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            rows={8}
            placeholder="Paste the job posting…"
            value={postingText}
            onChange={(e) => setPostingText(e.target.value)}
          />
          <Button
            onClick={onCreate}
            disabled={create.isPending || !postingText.trim()}
          >
            {create.isPending ? "Creating…" : "Create application"}
          </Button>
        </CardContent>
      </Card>

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
                  className="flex items-center justify-between gap-3 py-3"
                >
                  <div className="min-w-0">
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
                  <div className="flex shrink-0 items-center gap-2 text-sm">
                    {a.deadline && (
                      <span className="text-xs text-muted-foreground">
                        due {new Date(a.deadline).toLocaleDateString()}
                      </span>
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
