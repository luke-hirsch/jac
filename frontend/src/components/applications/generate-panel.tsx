import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { useLLMConfigs } from "@/lib/queries/llm";
import { type ApplicationRow } from "@/lib/queries/applications";
import {
  isStalePending,
  pendingAgeSeconds,
  useCreateGeneration,
  type Grade,
  type RunState,
} from "@/lib/queries/generations";
import { type SocketStatus } from "@/lib/ws";

export function GeneratePanel({
  app,
  activeRunId,
  onRunSelected,
  runState,
  runCreatedAt,
  now,
  socket,
  onAbort,
  aborting,
}: {
  app: ApplicationRow;
  activeRunId: number | null;
  onRunSelected: (id: number) => void;
  runState: RunState;
  runCreatedAt: string | null;
  now: Date;
  socket: SocketStatus;
  onAbort: () => void;
  aborting: boolean;
}) {
  const configs = useLLMConfigs();
  const create = useCreateGeneration();
  const [grade, setGrade] = useState<Grade | "">("");
  const [alias, setAlias] = useState("default");
  const [verifyGrounding, setVerifyGrounding] = useState(false);
  const [personalParagraph, setPersonalParagraph] = useState(false);

  const aliases = Array.from(
    new Set(["default", ...(configs.data?.map((c) => c.alias) ?? [])]),
  );
  const running =
    activeRunId != null &&
    (runState.status === "pending" || runState.status === "running");
  const ageSeconds = runCreatedAt ? pendingAgeSeconds(runCreatedAt, now) : 0;
  const staleQueue =
    running &&
    runCreatedAt != null &&
    isStalePending(runState.status, runCreatedAt, now);

  async function onGenerate() {
    try {
      const run = await create.mutateAsync({
        job_application: app.id,
        grade,
        alias,
        verify_grounding: verifyGrounding,
        personal_paragraph: personalParagraph,
      });
      onRunSelected(run.id);
    } catch {
      toast.error("Could not start generation");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Generate</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-4">
          <div className="space-y-1">
            <Label>Grade</Label>
            <Select
              value={grade || "auto"}
              onValueChange={(v) => setGrade(v === "auto" ? "" : (v as Grade))}
            >
              <SelectTrigger className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="auto">Auto (alias strength)</SelectItem>
                <SelectItem value="light">Light</SelectItem>
                <SelectItem value="standard">Standard</SelectItem>
                <SelectItem value="strong">Strong</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>Model alias</Label>
            <Select value={alias} onValueChange={setAlias}>
              <SelectTrigger className="w-44">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {aliases.map((a) => (
                  <SelectItem key={a} value={a}>
                    {a}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <label className="flex items-center gap-2 pb-2 text-sm">
            <Checkbox
              checked={verifyGrounding}
              onCheckedChange={(v) => setVerifyGrounding(v === true)}
            />
            Verify grounding
          </label>
          <label className="flex items-center gap-2 pb-2 text-sm">
            <Checkbox
              checked={personalParagraph}
              onCheckedChange={(v) => setPersonalParagraph(v === true)}
            />
            Personal paragraph
          </label>
          <Button onClick={onGenerate} disabled={running || create.isPending}>
            {running
              ? `Generating… ${runState.stage || "queued"} · ${ageSeconds}s`
              : "Generate"}
          </Button>
          {running && (
            <Button variant="outline" onClick={onAbort} disabled={aborting}>
              {aborting ? "Aborting…" : "Abort"}
            </Button>
          )}
        </div>

        {staleQueue && (
          <p className="text-sm text-amber-700">
            Queued for {ageSeconds}s with no progress — the generation worker
            may not be running. Abort and retry, or start the worker.
          </p>
        )}

        {running && socket.kind === "retrying" && (
          <p className="text-sm text-amber-700">
            Live connection lost — retrying in{" "}
            {Math.max(1, Math.round(socket.delayMs / 1000))}s…
          </p>
        )}
        {running && socket.kind === "closed" && (
          <p className="text-sm text-muted-foreground">
            Live updates unavailable — refresh the page to see progress.
          </p>
        )}

        {runState.status === "failed" && activeRunId != null && (
          <p className="text-sm text-destructive">
            Generation failed: {runState.error}
          </p>
        )}

        {app.runs.length > 0 && (
          <>
            <Separator />
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">Runs</p>
              <ul className="space-y-1">
                {app.runs.map((r) => (
                  <li key={r.id}>
                    <button
                      type="button"
                      onClick={() => onRunSelected(r.id)}
                      className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-muted ${
                        r.id === activeRunId ? "bg-muted" : ""
                      }`}
                    >
                      <Badge variant="outline">{r.status}</Badge>
                      <span>
                        {r.grade || "auto"} · {r.alias}
                      </span>
                      <span className="ml-auto text-xs text-muted-foreground">
                        {new Date(r.created_at).toLocaleString()}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
