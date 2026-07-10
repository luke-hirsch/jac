import { useEffect, useReducer, useState, useRef } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowDown, ArrowUp, Eye, EyeOff, Trash2 } from "lucide-react";
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
import { Textarea } from "@/components/ui/textarea";
import { useLLMConfigs } from "@/lib/queries/llm";
import {
  useRewriteParagraph,
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

import {
  SECTION_ORDER,
  SECTION_TITLES,
  addEntry,
  fromCareerDb,
  joinEntry,
  labelFor,
  missingEntries,
  moveEntry,
  removeEntry,
  toggleDeselect,
  type CvContent,
  type SectionKey,
} from "@/lib/cv-doc";
import {
  useCvEntries,
  useFullList,
  type CvEntriesResponse,
  type LayoutRow,
} from "@/lib/queries/jac";
import {
  appendParagraph,
  editableBody,
  hasStub,
  normalizeLetterMeta,
  replaceRange,
  replaceStub,
  type LetterMeta,
} from "@/lib/letter-doc";
import { Input } from "@/components/ui/input";
import { type ResumeSnippetRow } from "@/lib/queries/jac";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { activeContent } from "@/lib/cv-doc";
import { fitCv, type FitResult } from "@/lib/render/fit";
import { isFavouriteLookup } from "@/lib/render/parts";
import { useLayoutSpec } from "@/lib/render/spec";
import {
  ApplicationDocument,
  CvDocument,
  LetterDocument,
  pdfPages,
  renderPdfBlob,
} from "@/lib/render/templates";
import {
  cvToMarkdown,
  downloadBlob,
  downloadText,
  exportBlocker,
  exportJson,
  letterToMarkdown,
  type ExportFormat,
  type ExportScope,
} from "@/lib/export";

export const Route = createFileRoute(
  "/_authenticated/applications/$applicationId",
)({
  component: ApplicationDetailPage,
});

const INITIAL: RunState = {
  status: "pending",
  stage: "",
  result: null,
  error: "",
};

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
            app.data.cover_letter === editableBody(state.result.cover_letter)
          }
        />
      )}
      <ApplicationContentCard app={app.data} />
      <ExportCard app={app.data} />
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
            <Button
              size="sm"
              onClick={onApply}
              disabled={applied || update.isPending}
            >
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

function CvSection({
  section,
  entries,
}: {
  section: string;
  entries: CvEntry[];
}) {
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
        <pre className="whitespace-pre-wrap font-sans text-sm">
          {letter.text}
        </pre>
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
  const layouts = useFullList<LayoutRow>("layouts");
  const careerDb = useCvEntries();
  const [coverLetter, setCoverLetter] = useState(app.cover_letter);
  const [status, setStatus] = useState<ApplicationStatus>(app.status);
  const [cvDraft, setCvDraft] = useState<CvContent>(app.cv_content ?? {});
  const serverMeta = JSON.stringify(normalizeLetterMeta(app.letter_meta));
  const [letterMeta, setLetterMeta] = useState<LetterMeta>(() =>
    normalizeLetterMeta(app.letter_meta),
  );

  // "Adjusting state during render" (React docs, same pattern as usePagedList):
  // re-seed the local drafts when the server copy changes (apply / auto-fill),
  // discarding any unsaved edits in favour of the fresher server state. cv_content is
  // compared by value — a refetch returning identical JSON must not clobber the draft.
  const serverCv = JSON.stringify(app.cv_content ?? {});
  const [prevServer, setPrevServer] = useState({
    cover: app.cover_letter,
    status: app.status,
    cv: serverCv,
    meta: serverMeta,
  });
  if (
    prevServer.cover !== app.cover_letter ||
    prevServer.status !== app.status ||
    prevServer.cv !== serverCv ||
    prevServer.meta !== serverMeta
  ) {
    setPrevServer({
      cover: app.cover_letter,
      status: app.status,
      cv: serverCv,
      meta: serverMeta,
    });
    setCoverLetter(app.cover_letter);
    setStatus(app.status);
    setCvDraft(app.cv_content ?? {});
    setLetterMeta(normalizeLetterMeta(app.letter_meta));
  }

  const dirty =
    coverLetter !== app.cover_letter ||
    status !== app.status ||
    JSON.stringify(cvDraft) !== serverCv ||
    JSON.stringify(letterMeta) !== serverMeta;

  function onSave() {
    if ((status === "sent" || status === "follow_up") && hasStub(coverLetter)) {
      toast.warning(
        "The letter still contains the personal-paragraph stub — it is not sendable.",
      );
    }
    update.mutate(
      {
        id: app.id,
        body: { cover_letter: coverLetter, status, cv_content: cvDraft },
      },
      {
        onSuccess: () => toast.success("Application saved"),
        onError: () => toast.error("Could not save the application"),
      },
    );
  }

  // The layout is a FK pick, not a draft — persist it immediately.
  function onLayoutChange(v: string) {
    update.mutate(
      { id: app.id, body: { layout: Number(v) } },
      { onError: () => toast.error("Could not change the layout") },
    );
  }

  const hasCv = SECTION_ORDER.some((s) => (cvDraft[s] ?? []).length > 0);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Application content</CardTitle>
        <div className="flex items-center gap-2">
          <Select value={String(app.layout)} onValueChange={onLayoutChange}>
            <SelectTrigger className="w-40">
              <SelectValue placeholder="Layout" />
            </SelectTrigger>
            <SelectContent>
              {(layouts.data ?? []).map((l) => (
                <SelectItem key={l.id} value={String(l.id)}>
                  {l.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
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
          <Button
            size="sm"
            onClick={onSave}
            disabled={!dirty || update.isPending}
          >
            Save
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {hasCv ? (
          <div className="space-y-4">
            {/* Every section renders (the section component hides itself only when it has
                neither entries nor addable rows), so an AI run that kept no project still
                offers the project add-picker. */}
            {SECTION_ORDER.map((section) => (
              <CvEditorSection
                key={section}
                section={section}
                entries={cvDraft[section] ?? []}
                db={careerDb.data}
                onEdit={setCvDraft}
              />
            ))}
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              No CV content yet — generate a run above, or build it by hand:
            </p>
            <Button
              variant="outline"
              size="sm"
              disabled={!careerDb.data}
              onClick={() =>
                careerDb.data && setCvDraft(fromCareerDb(careerDb.data))
              }
            >
              Start from full career DB
            </Button>
          </div>
        )}

        <Separator />
        <LetterEditor
          applicationId={app.id}
          meta={letterMeta}
          onMeta={setLetterMeta}
          body={coverLetter}
          onBody={setCoverLetter}
        />
      </CardContent>
    </Card>
  );
}

function CvEditorSection({
  section,
  entries,
  db,
  onEdit,
}: {
  section: SectionKey;
  entries: CvEntry[];
  db: CvEntriesResponse | undefined;
  onEdit: (fn: (c: CvContent) => CvContent) => void;
}) {
  const missing = missingEntries(db, section, entries);
  if (entries.length === 0 && missing.length === 0) return null;
  return (
    <div>
      <h3 className="text-sm font-semibold">{SECTION_TITLES[section]}</h3>
      <ul className="space-y-1">
        {entries.map((e, i) => {
          const row = db ? joinEntry(db, section, e) : null;
          const gone = db != null && row == null; // deleted from the career DB
          return (
            <li
              key={e.id}
              className={`flex items-center gap-1 text-sm ${
                e.deselected ? "opacity-50" : ""
              }`}
            >
              <span className={`flex-1 ${e.deselected ? "line-through" : ""}`}>
                {row ? labelFor(section, row) : e.label}
                {gone && (
                  <span className="ml-1 text-xs text-destructive">
                    (no longer in the career DB)
                  </span>
                )}
              </span>
              {e.relevance_score != null && (
                <Badge variant="outline">{e.relevance_score.toFixed(2)}</Badge>
              )}
              <Button
                variant="ghost"
                size="icon"
                aria-label="Move up"
                disabled={i === 0}
                onClick={() => onEdit((c) => moveEntry(c, section, i, -1))}
              >
                <ArrowUp className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Move down"
                disabled={i === entries.length - 1}
                onClick={() => onEdit((c) => moveEntry(c, section, i, 1))}
              >
                <ArrowDown className="h-4 w-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label={e.deselected ? "Reselect" : "Deselect"}
                onClick={() => onEdit((c) => toggleDeselect(c, section, i))}
              >
                {e.deselected ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Delete"
                onClick={() => onEdit((c) => removeEntry(c, section, i))}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          );
        })}
      </ul>
      {missing.length > 0 && (
        <Select
          value=""
          onValueChange={(v) => {
            const row = missing.find((r) => String(r.id) === v);
            if (row) onEdit((c) => addEntry(c, section, row));
          }}
        >
          <SelectTrigger className="mt-1 h-8 w-72 text-xs">
            <SelectValue
              placeholder={`Add ${SECTION_TITLES[section].toLowerCase()}…`}
            />
          </SelectTrigger>
          <SelectContent>
            {missing.map((r) => (
              <SelectItem key={r.id} value={String(r.id)}>
                {labelFor(section, r)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </div>
  );
}

/* ---------- letter editor ---------- */

function MetaField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      <Input value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

const RECIPIENT_FIELDS: [string, string][] = [
  ["company", "Company"],
  ["contact_name", "Contact"],
  ["street", "Street"],
  ["zip", "ZIP"],
  ["city", "City"],
  ["country", "Country"],
  ["email", "Email"],
];

const SENDER_FIELDS: [string, string][] = [
  ["name", "Name"],
  ["street", "Street"],
  ["zip", "ZIP"],
  ["city", "City"],
  ["email", "Email"],
  ["phone", "Phone"],
];

function LetterEditor({
  applicationId,
  meta,
  onMeta,
  body,
  onBody,
}: {
  applicationId: number;
  meta: LetterMeta;
  onMeta: (m: LetterMeta) => void;
  body: string;
  onBody: (b: string) => void;
}) {
  const snippets = useFullList<ResumeSnippetRow>("snippets");
  const rewrite = useRewriteParagraph();
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const [stubDraft, setStubDraft] = useState("");
  const [snippetId, setSnippetId] = useState("");
  const [instruction, setInstruction] = useState("");

  const setField = (field: keyof LetterMeta) => (v: string) =>
    onMeta({ ...meta, [field]: v });
  const setBlockField =
    (block: "recipient" | "sender", field: string) => (v: string) =>
      onMeta({ ...meta, [block]: { ...meta[block], [field]: v } });

  function onAppendSnippet() {
    const s = snippets.data?.find((r) => String(r.id) === snippetId);
    if (!s) return;
    onBody(appendParagraph(body, s.content));
    setSnippetId("");
  }

  function onRewrite() {
    const el = bodyRef.current;
    if (!el) return;
    // The selection indexes into the *draft* string the textarea renders, so the splice
    // below is exact. The selection is read at click time — mutate on the captured range.
    const start = el.selectionStart;
    const end = el.selectionEnd;
    if (!body.slice(start, end).trim()) {
      toast.error("Select the passage to rewrite in the body first");
      return;
    }
    rewrite.mutate(
      { id: applicationId, text: body.slice(start, end), instruction },
      {
        onSuccess: (r) => onBody(replaceRange(body, start, end, r.text)),
        onError: () =>
          toast.error("Rewrite failed — is the model server running?"),
      },
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <div className="col-span-2">
          <MetaField
            label="Subject"
            value={meta.subject}
            onChange={setField("subject")}
          />
        </div>
        <MetaField label="Date" value={meta.date} onChange={setField("date")} />
        <MetaField
          label="Language"
          value={meta.language}
          onChange={setField("language")}
        />
        <div className="col-span-2">
          <MetaField
            label="Salutation"
            value={meta.salutation}
            onChange={setField("salutation")}
          />
        </div>
        <div className="col-span-2">
          <MetaField
            label="Closing"
            value={meta.closing}
            onChange={setField("closing")}
          />
        </div>
      </div>

      <details className="rounded border p-3">
        <summary className="cursor-pointer text-sm font-medium">
          Recipient
        </summary>
        <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-3">
          {RECIPIENT_FIELDS.map(([field, label]) => (
            <MetaField
              key={field}
              label={label}
              value={meta.recipient[field] ?? ""}
              onChange={setBlockField("recipient", field)}
            />
          ))}
        </div>
      </details>

      <details className="rounded border p-3">
        <summary className="cursor-pointer text-sm font-medium">Sender</summary>
        <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-3">
          {SENDER_FIELDS.map(([field, label]) => (
            <MetaField
              key={field}
              label={label}
              value={meta.sender[field] ?? ""}
              onChange={setBlockField("sender", field)}
            />
          ))}
        </div>
      </details>

      <div className="space-y-1">
        <Label>Cover letter body</Label>
        <Textarea
          ref={bodyRef}
          rows={12}
          value={body}
          onChange={(e) => onBody(e.target.value)}
          placeholder="The applied run's letter body lands here — or write your own."
        />
      </div>

      <div className="flex items-end gap-2">
        <div className="flex-1 space-y-1">
          <Label className="text-xs">
            AI rewrite (select text in the body first)
          </Label>
          <Input
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder="Optional instruction — e.g. shorter, more formal…"
          />
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={onRewrite}
          disabled={rewrite.isPending}
        >
          {rewrite.isPending ? "Rewriting…" : "Rewrite selection"}
        </Button>
      </div>

      {hasStub(body) && (
        <div className="space-y-2 rounded border border-destructive/50 bg-destructive/10 p-3">
          <p className="text-xs font-medium">
            The body still contains the personal-paragraph stub — not sendable.
            Write your own paragraph to replace it:
          </p>
          <Textarea
            rows={4}
            value={stubDraft}
            onChange={(e) => setStubDraft(e.target.value)}
            placeholder="Why this company, in your own words…"
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={!stubDraft.trim()}
              onClick={() => {
                onBody(replaceStub(body, stubDraft));
                setStubDraft("");
              }}
            >
              Replace stub
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => onBody(replaceStub(body, ""))}
            >
              Remove stub
            </Button>
          </div>
        </div>
      )}

      <div className="flex items-end gap-2">
        <div className="space-y-1">
          <Label className="text-xs">Append a snippet</Label>
          <Select value={snippetId} onValueChange={setSnippetId}>
            <SelectTrigger className="w-64">
              <SelectValue placeholder="Pick a snippet…" />
            </SelectTrigger>
            <SelectContent>
              {(snippets.data ?? []).map((s) => (
                <SelectItem key={s.id} value={String(s.id)}>
                  {s.kind}: {s.title}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          size="sm"
          variant="outline"
          disabled={!snippetId}
          onClick={onAppendSnippet}
        >
          Append
        </Button>
      </div>
    </div>
  );
}

/* ---------- export ---------- */

type BuiltPdf = {
  blob: Blob;
  fit: FitResult | null; // null for letter-only
  letterPages: number | null;
};

function ExportCard({ app }: { app: ApplicationRow }) {
  const layouts = useFullList<LayoutRow>("layouts");
  const layout = layouts.data?.find((l) => l.id === app.layout);
  const spec = useLayoutSpec(layout);
  const careerDb = useCvEntries();
  const [scope, setScope] = useState<ExportScope>("complete");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{
    url: string;
    info: BuiltPdf;
  } | null>(null);

  const meta = normalizeLetterMeta(app.letter_meta);
  const name = meta.sender.name || "CV";
  const stem = `application-${app.id}-${scope}`;

  async function buildPdf(): Promise<BuiltPdf> {
    if (!spec.data) throw new Error("layout spec not loaded");
    const s = spec.data;
    const db = careerDb.data;
    const active = activeContent(app.cv_content ?? {});

    const fit =
      scope === "letter"
        ? null
        : await fitCv(
            active,
            s.cv.pages,
            (c) =>
              pdfPages(<CvDocument spec={s} name={name} content={c} db={db} />),
            isFavouriteLookup(db),
          );
    const letterPages =
      scope === "cv"
        ? null
        : await pdfPages(
            <LetterDocument spec={s} meta={meta} body={app.cover_letter} />,
          );

    const doc =
      scope === "cv" ? (
        <CvDocument spec={s} name={name} content={fit!.content} db={db} />
      ) : scope === "letter" ? (
        <LetterDocument spec={s} meta={meta} body={app.cover_letter} />
      ) : (
        <ApplicationDocument
          cv={{ spec: s, name, content: fit!.content, db }}
          letter={{ spec: s, meta, body: app.cover_letter }}
        />
      );
    return { blob: await renderPdfBlob(doc), fit, letterPages };
  }

  async function withBusy<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    try {
      return await fn();
    } catch {
      toast.error("Export failed");
    } finally {
      setBusy(false);
    }
  }

  // Send-time stub gate: refuses (with the reason) before any rendering happens.
  function blockedBy(format: ExportFormat): boolean {
    const reason = exportBlocker(scope, format, app.cover_letter);
    if (reason) toast.error(reason);
    return reason != null;
  }

  function onDownloadPdf() {
    if (blockedBy("pdf")) return;
    void withBusy(async () => {
      const built = await buildPdf();
      downloadBlob(built.blob, `${stem}.pdf`);
      notify(built);
    });
  }

  function onPreview() {
    if (blockedBy("pdf")) return;
    void withBusy(async () => {
      const built = await buildPdf();
      setPreview({ url: URL.createObjectURL(built.blob), info: built });
    });
  }

  function onDownloadMd() {
    if (blockedBy("md")) return;
    const db = careerDb.data;
    const active = activeContent(app.cv_content ?? {});
    const cvMd = cvToMarkdown(name, active, db);
    const letterMd = letterToMarkdown(meta, app.cover_letter);
    const md =
      scope === "cv"
        ? cvMd
        : scope === "letter"
          ? letterMd
          : `${letterMd}\n---\n\n${cvMd}`;
    downloadText(md, `${stem}.md`, "text/markdown");
  }

  function onDownloadJson() {
    downloadText(
      exportJson(scope, {
        content: activeContent(app.cv_content ?? {}),
        meta,
        body: app.cover_letter,
        db: careerDb.data,
      }),
      `${stem}.json`,
      "application/json",
    );
  }

  function notify(built: BuiltPdf) {
    if (built.fit && !built.fit.fits) {
      toast.warning("The CV overflows the layout even at minimum content.");
    } else if (built.fit && built.fit.droppedIds.length > 0) {
      toast.info(
        `${built.fit.droppedIds.length} lowest-ranked entr${
          built.fit.droppedIds.length === 1 ? "y was" : "ies were"
        } dropped to fit ${spec.data?.cv.pages} page(s). Deselect or reorder to override.`,
      );
    }
    if (built.letterPages != null && built.letterPages > 1) {
      toast.warning("The cover letter exceeds one page — shorten the body.");
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Export</CardTitle>
        <Badge variant="outline">{layout?.name ?? "layout…"}</Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Exports the saved application content — save your edits first. The CV
          is auto-fitted to the layout's page budget by dropping the
          lowest-ranked entries; the letter is never cut, only flagged.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <div className="space-y-1">
            <Label className="text-xs">Scope</Label>
            <Select
              value={scope}
              onValueChange={(v) => setScope(v as ExportScope)}
            >
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="complete">Complete</SelectItem>
                <SelectItem value="cv">CV only</SelectItem>
                <SelectItem value="letter">Letter only</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button size="sm" onClick={onPreview} disabled={busy || !spec.data}>
            {busy ? "Rendering…" : "Preview PDF"}
          </Button>
          <Button
            size="sm"
            onClick={onDownloadPdf}
            disabled={busy || !spec.data}
          >
            Download PDF
          </Button>
          <Button size="sm" variant="outline" onClick={onDownloadMd}>
            Markdown
          </Button>
          <Button size="sm" variant="outline" onClick={onDownloadJson}>
            JSON
          </Button>
        </div>
      </CardContent>

      <Dialog
        open={preview != null}
        onOpenChange={(open) => {
          if (!open && preview) {
            URL.revokeObjectURL(preview.url);
            setPreview(null);
          }
        }}
      >
        <DialogContent className="h-[85vh] max-w-4xl">
          <DialogHeader>
            <DialogTitle>PDF preview — {scope}</DialogTitle>
          </DialogHeader>
          {preview && (
            <iframe
              src={preview.url}
              title="PDF preview"
              className="h-full w-full"
            />
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}
