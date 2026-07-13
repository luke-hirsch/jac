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
import { aliasesForGrade, useLLMAliases } from "@/lib/queries/llm";
import {
  runToApplicationPatch,
  useUpdateApplication,
  type ApplicationRow,
} from "@/lib/queries/applications";
import {
  aiShareBadge,
  groundingBadge,
  isStalePending,
  pendingAgeSeconds,
  qualityBadge,
  useCreateGeneration,
  type Grade,
  type RunState,
} from "@/lib/queries/generations";
import { type SocketStatus } from "@/lib/ws";
import { personalityHint, usePersonality } from "@/lib/queries/personality";

function toneClass(tone: "green" | "amber" | "muted") {
  return tone === "green"
    ? "bg-green-100 text-green-800"
    : tone === "amber"
      ? "bg-amber-100 text-amber-900"
      : "bg-muted text-muted-foreground";
}

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
  applied,
  onApplied,
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
  applied: boolean;
  onApplied: () => void;
}) {
  const aliases = useLLMAliases();
  const create = useCreateGeneration();
  const update = useUpdateApplication();
  const [grade, setGrade] = useState<Grade | "">("");
  const [alias, setAlias] = useState("default");
  const [verifyGrounding, setVerifyGrounding] = useState(false);
  const [personalParagraph, setPersonalParagraph] = useState(false);

  // Only aliases that can actually run the chosen grade are offered; a pick that
  // a grade change invalidated is snapped to the first fitting one (adjust-state-
  // during-render, same pattern as the content card's server re-seed).
  const allowed = aliasesForGrade(aliases.data ?? [], grade);
  const aliasFits = allowed.some((a) => a.alias === alias);
  if (aliases.data && !aliasFits && allowed.length > 0) {
    setAlias(allowed[0].alias);
  }
  const selected = aliases.data?.find((a) => a.alias === alias);

  const running =
    activeRunId != null &&
    (runState.status === "pending" || runState.status === "running");
  const ageSeconds = runCreatedAt ? pendingAgeSeconds(runCreatedAt, now) : 0;
  const staleQueue =
    running &&
    runCreatedAt != null &&
    isStalePending(runState.status, runCreatedAt, now);

  const personality = usePersonality(personalParagraph);
  const hint = personalityHint(personalParagraph, personality.data);

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

  function onApply() {
    if (!runState.result) return;
    onApplied(); // arm the fresh-highlight before the refetched content lands
    update.mutate(
      { id: app.id, body: runToApplicationPatch(runState.result) },
      {
        onSuccess: () => toast.success("Run applied to the application"),
        onError: () => toast.error("Could not apply the run"),
      },
    );
  }

  const result = runState.status === "done" ? runState.result : null;
  const ai = result ? aiShareBadge(result.cover_letter.ai_share) : null;
  const grounding = result
    ? groundingBadge(result.cover_letter.grounding)
    : null;
  const quality = result ? qualityBadge(result.cover_letter.critique) : null;

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
                <SelectItem value="auto">Auto (model strength)</SelectItem>
                <SelectItem value="light">Light</SelectItem>
                <SelectItem value="standard">Standard</SelectItem>
                <SelectItem value="strong">Strong</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <Label>Model</Label>
            <Select
              value={aliasFits ? alias : ""}
              onValueChange={setAlias}
              disabled={allowed.length === 0}
            >
              <SelectTrigger className="w-64">
                <SelectValue
                  placeholder={
                    aliases.isLoading ? "Loading models…" : "No fitting model"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {allowed.map((a) => (
                  <SelectItem key={a.alias} value={a.alias}>
                    {a.alias} — {a.model} ({a.strength})
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
          <Button
            onClick={onGenerate}
            disabled={running || create.isPending || !aliasFits}
          >
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

        {grade && allowed.length === 0 && aliases.data && (
          <p className="text-sm text-amber-700">
            {grade === "light"
              ? "No configured model can embed — the light rung needs an embedding-capable (Ollama) config."
              : `No configured model is ${grade} enough for this grade — add one under Account → LLM.`}
          </p>
        )}
        {personalParagraph && selected && !selected.supports_web_search && (
          <p className="text-xs text-muted-foreground">
            {selected.alias} cannot web-search — the personal paragraph will
            come out as a stub to replace by hand.
          </p>
        )}
        {hint && <p className="text-xs text-amber-700">{hint}</p>}

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

        {result && ai && grounding && (
          <div className="flex flex-wrap items-center gap-2 rounded border bg-muted/40 p-2 text-sm">
            <Badge variant="outline">
              {result.meta.grade} · {result.meta.alias}
            </Badge>
            <span
              className={`rounded px-2 py-0.5 text-xs ${toneClass(ai.tone)}`}
            >
              {ai.label}
            </span>
            <span
              className={`rounded px-2 py-0.5 text-xs ${toneClass(grounding.tone)}`}
              title={result.cover_letter.grounding.claims.join("\n")}
            >
              {grounding.label}
            </span>
            {quality && (
              <span
                className={`rounded px-2 py-0.5 text-xs ${toneClass(quality.tone)}`}
                title={(result.cover_letter.critique?.claims ?? []).join("\n")}
              >
                {quality.label}
              </span>
            )}
            {result.cover_letter.snippet_ranking && (
              <span
                className="rounded bg-muted px-2 py-0.5 text-xs text-muted-foreground"
                title="How the letter's snippets were picked: embedding = ranked against the posting; structural = no embedder was reachable, career-DB links decided."
              >
                snippets: {result.cover_letter.snippet_ranking}
              </span>
            )}
            {result.cover_letter.personal_paragraph_is_stub && (
              <span className="rounded bg-destructive/10 px-2 py-0.5 text-xs text-destructive">
                personal paragraph is a stub
              </span>
            )}
            <span className="text-xs text-muted-foreground">
              {applied
                ? "Result is in the application below."
                : "Apply to load this result into the application below."}
            </span>
            <Button
              size="sm"
              className="ml-auto"
              onClick={onApply}
              disabled={applied || update.isPending}
            >
              {applied ? "Applied" : "Apply to application"}
            </Button>
          </div>
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
