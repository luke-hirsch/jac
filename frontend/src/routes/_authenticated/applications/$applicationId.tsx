import { useEffect, useReducer, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { Textarea } from "@/components/ui/textarea";
import { useLLMConfigs } from "@/lib/queries/llm";
import {
  STATUS_LABELS,
  runToApplicationPatch,
  useApplication,
  useUpdateApplication,
  type ApplicationRow,
  type ApplicationStatus,
} from "@/lib/queries/applications";
import {
  aiShareBadge,
  groundingBadge,
  isStalePending,
  pendingAgeSeconds,
  runReducer,
  useCancelGeneration,
  useCreateGeneration,
  useGeneration,
  type CoverLetterResult,
  type CvEntry,
  type Grade,
  type RunState,
  type WsEvent,
} from "@/lib/queries/generations";
import { openGenerationSocket, type SocketStatus } from "@/lib/ws";

export const Route = createFileRoute("/_authenticated/applications/$applicationId")({
  component: ApplicationDetailPage,
});

const INITIAL: RunState = { status: "pending", stage: "", result: null, error: "" };

function ApplicationDetailPage() {
  const { applicationId } = Route.useParams();
  const id = Number(applicationId);
  const qc = useQueryClient();
  const app = useApplication(id);

  // The user's explicit pick wins; otherwise the latest run (refresh-safe rehydrate).
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const runId = selectedRunId ?? app.data?.runs[0]?.id ?? null;
  const [state, dispatch] = useReducer(runReducer, INITIAL);
  // Starts as "connecting" so the closed-socket notice never flashes before the
  // socket effect has run.
  const [socket, setSocket] = useState<SocketStatus>({ kind: "connecting" });
  const snapshot = useGeneration(runId); // REST rehydrate (refresh-safe)
  const cancel = useCancelGeneration();

  // A 1s clock while a run is in flight, for the elapsed/stale-queue display.
  const active =
    runId != null && (state.status === "pending" || state.status === "running");
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, [active]);

  // Seed from the REST snapshot whenever it (re)loads.
  useEffect(() => {
    if (snapshot.data) {
      dispatch({
        event: "snapshot",
        status: snapshot.data.status,
        stage: snapshot.data.stage,
        result: snapshot.data.result,
        error: snapshot.data.error,
      });
    }
  }, [snapshot.data]);

  // Live socket while a run is selected; on a terminal event refresh the
  // application — the fill-if-empty hand-off may have landed content.
  useEffect(() => {
    if (runId == null) return;
    return openGenerationSocket(
      runId,
      (d) => {
        const e = d as WsEvent;
        dispatch(e);
        if (e.event === "done" || e.event === "failed") {
          qc.invalidateQueries({ queryKey: ["jac", "applications"] });
        }
      },
      setSocket,
    );
  }, [runId, qc]);

  function onAbort() {
    if (runId == null) return;
    cancel.mutate(runId, {
      onSuccess: (run) => {
        dispatch({
          event: "snapshot",
          status: run.status,
          stage: run.stage,
          result: run.result,
          error: run.error,
        });
        qc.invalidateQueries({ queryKey: ["jac", "applications"] });
        qc.invalidateQueries({ queryKey: ["jac", "generations", runId] });
      },
      onError: () => toast.error("Could not cancel the run"),
    });
  }

  if (app.isLoading) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (!app.data) return <p className="text-sm text-destructive">Application not found.</p>;

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
        runState={state}
        runCreatedAt={snapshot.data?.created_at ?? null}
        now={now}
        socket={socket}
        onAbort={onAbort}
        aborting={cancel.isPending}
      />
      {state.result && (
        <ResultView
          applicationId={id}
          state={state}
          applied={
            app.data.cover_letter === state.result.cover_letter.text
          }
        />
      )}
      <ApplicationContentCard app={app.data} />
    </div>
  );
}

/* ---------- posting ---------- */

function PostingCard({ app }: { app: ApplicationRow }) {
  const [open, setOpen] = useState(false);
  const posting = app.posting_detail;
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Job posting</CardTitle>
        <div className="flex items-center gap-2">
          <Badge variant="outline">{posting.language}</Badge>
          {!posting.active && <Badge variant="destructive">inactive</Badge>}
        </div>
      </CardHeader>
      <CardContent>
        <pre
          className={`whitespace-pre-wrap font-sans text-sm text-muted-foreground ${
            open ? "" : "max-h-32 overflow-hidden"
          }`}
        >
          {posting.posting_text}
        </pre>
        <Button variant="ghost" size="sm" onClick={() => setOpen(!open)}>
          {open ? "Collapse" : "Show full posting"}
        </Button>
      </CardContent>
    </Card>
  );
}

/* ---------- generate ---------- */

function GeneratePanel({
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
    running && runCreatedAt != null && isStalePending(runState.status, runCreatedAt, now);

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
            Queued for {ageSeconds}s with no progress — the generation worker may
            not be running. Abort and retry, or start the worker.
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

/* ---------- result ---------- */

function toneClass(tone: "green" | "amber" | "muted") {
  return tone === "green"
    ? "bg-green-100 text-green-800"
    : tone === "amber"
      ? "bg-amber-100 text-amber-900"
      : "bg-muted text-muted-foreground";
}

function ResultView({
  applicationId,
  state,
  applied,
}: {
  applicationId: number;
  state: RunState;
  applied: boolean;
}) {
  const update = useUpdateApplication();
  const result = state.result!;

  function onApply() {
    update.mutate(
      { id: applicationId, body: runToApplicationPatch(result) },
      {
        onSuccess: () => toast.success("Run applied to the application"),
        onError: () => toast.error("Could not apply the run"),
      },
    );
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Tailored CV</CardTitle>
          <div className="flex items-center gap-2">
            <Badge variant="outline">
              {result.meta.grade} · {result.meta.alias}
            </Badge>
            <Button size="sm" onClick={onApply} disabled={applied || update.isPending}>
              {applied ? "Applied" : "Apply to application"}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {Object.entries(result.cv).map(([section, entries]) =>
            entries.length === 0 ? null : (
              <CvSection key={section} section={section} entries={entries} />
            ),
          )}
        </CardContent>
      </Card>

      <CoverLetterCard letter={result.cover_letter} />
    </div>
  );
}

function CvSection({ section, entries }: { section: string; entries: CvEntry[] }) {
  return (
    <div>
      <h3 className="text-sm font-semibold capitalize">{section}</h3>
      <ul className="space-y-1">
        {entries.map((e) => (
          <li
            key={e.id}
            className="flex items-center justify-between gap-2 text-sm"
          >
            <span>{e.label}</span>
            {e.relevance_score != null && (
              <Badge variant="outline">{e.relevance_score.toFixed(2)}</Badge>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function CoverLetterCard({ letter }: { letter: CoverLetterResult }) {
  const ai = aiShareBadge(letter.ai_share);
  const g = groundingBadge(letter.grounding);
  const pg = groundingBadge(letter.personal_paragraph_grounding);
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Cover letter</CardTitle>
        <div className="flex gap-2">
          <span className={`rounded px-2 py-0.5 text-xs ${toneClass(ai.tone)}`}>
            {ai.label}
          </span>
          <span
            className={`rounded px-2 py-0.5 text-xs ${toneClass(g.tone)}`}
            title={letter.grounding.claims.join("\n")}
          >
            {g.label}
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <pre className="whitespace-pre-wrap font-sans text-sm">{letter.text}</pre>
        {(letter.personal_paragraph_is_stub || letter.personal_paragraph) && (
          <div
            className={
              letter.personal_paragraph_is_stub
                ? "rounded border border-destructive/50 bg-destructive/10 p-3"
                : "rounded border bg-muted/40 p-3"
            }
          >
            <div className="mb-1 flex items-center gap-2">
              <p className="text-xs font-medium">
                {letter.personal_paragraph_is_stub
                  ? "Personal paragraph — STUB (not sendable)"
                  : "Personal paragraph"}
              </p>
              {!letter.personal_paragraph_is_stub && (
                <span
                  className={`rounded px-2 py-0.5 text-xs ${toneClass(pg.tone)}`}
                  title={letter.personal_paragraph_grounding.claims.join("\n")}
                >
                  {pg.label}
                </span>
              )}
            </div>
            <p className="text-sm">{letter.personal_paragraph}</p>
            {!letter.personal_paragraph_is_stub &&
              letter.personal_paragraph_sources.length > 0 && (
                <ul className="mt-2 text-xs text-muted-foreground">
                  {letter.personal_paragraph_sources.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/* ---------- application content (the editable artefact) ---------- */

function ApplicationContentCard({ app }: { app: ApplicationRow }) {
  const update = useUpdateApplication();
  const [coverLetter, setCoverLetter] = useState(app.cover_letter);
  const [status, setStatus] = useState<ApplicationStatus>(app.status);

  // "Adjusting state during render" (React docs, same pattern as usePagedList):
  // re-seed the local draft when the server copy changes (apply / auto-fill),
  // discarding any unsaved edits in favour of the fresher server state.
  const [prevServer, setPrevServer] = useState({
    cover: app.cover_letter,
    status: app.status,
  });
  if (prevServer.cover !== app.cover_letter || prevServer.status !== app.status) {
    setPrevServer({ cover: app.cover_letter, status: app.status });
    setCoverLetter(app.cover_letter);
    setStatus(app.status);
  }

  const dirty = coverLetter !== app.cover_letter || status !== app.status;

  function onSave() {
    update.mutate(
      { id: app.id, body: { cover_letter: coverLetter, status } },
      {
        onSuccess: () => toast.success("Application saved"),
        onError: () => toast.error("Could not save the application"),
      },
    );
  }

  const cvSections = Object.entries(app.cv_content ?? {}).filter(
    ([, entries]) => Array.isArray(entries) && entries.length > 0,
  );

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Application content</CardTitle>
        <div className="flex items-center gap-2">
          <Select
            value={status}
            onValueChange={(v) => setStatus(v as ApplicationStatus)}
          >
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(STATUS_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button size="sm" onClick={onSave} disabled={!dirty || update.isPending}>
            Save
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {cvSections.length > 0 ? (
          <div className="space-y-4">
            {cvSections.map(([section, entries]) => (
              <CvSection key={section} section={section} entries={entries} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No CV content yet — generate a run; the first result fills this
            automatically.
          </p>
        )}
        <Separator />
        <div className="space-y-1">
          <Label>Cover letter</Label>
          <Textarea
            rows={12}
            value={coverLetter}
            onChange={(e) => setCoverLetter(e.target.value)}
            placeholder="The applied run's cover letter lands here — edit freely."
          />
        </div>
      </CardContent>
    </Card>
  );
}
