import { useState } from "react";
import { toast } from "sonner";
import {
  ArrowDown,
  ArrowUp,
  Eye,
  EyeOff,
  Pin,
  PinOff,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import {
  STATUS_LABELS,
  useUpdateApplication,
  type ApplicationRow,
  type ApplicationStatus,
} from "@/lib/queries/applications";
import { type CvEntry } from "@/lib/queries/generations";
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
  togglePin,
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
  fillBlanks,
  hasStub,
  normalizeLetterMeta,
  senderFromProfile,
  type LetterMeta,
} from "@/lib/letter-doc";
import { useProfile } from "@/lib/queries/profile";
import { overCapIds } from "@/lib/render/fit";
import { useLayoutSpec } from "@/lib/render/spec";
import { LetterEditor } from "./letter-editor";
import { type Fresh } from "./use-fresh-highlight";

export function ApplicationContentCard({
  app,
  fresh,
}: {
  app: ApplicationRow;
  fresh: Fresh;
}) {
  const update = useUpdateApplication();
  const layouts = useFullList<LayoutRow>("layouts");
  const spec = useLayoutSpec(layouts.data?.find((l) => l.id === app.layout));
  const careerDb = useCvEntries();
  const profile = useProfile();
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

  // Sender defaults come from the user profile (same source the pipeline's
  // _sender() uses) — filled into *blank* fields only, so explicit edits and
  // run-provided values always win. Render-adjust: converges once merged.
  if (profile.data) {
    const merged = {
      ...letterMeta,
      sender: fillBlanks(letterMeta.sender, senderFromProfile(profile.data)),
    };
    if (JSON.stringify(merged.sender) !== JSON.stringify(letterMeta.sender)) {
      setLetterMeta(merged);
    }
  }

  const dirty =
    coverLetter !== app.cover_letter ||
    status !== app.status ||
    JSON.stringify(cvDraft) !== serverCv ||
    JSON.stringify(letterMeta) !== serverMeta;

  function onSave() {
    if (
      (app.status === "sent" || app.status === "follow_up") &&
      hasStub(coverLetter)
    ) {
      toast.warning(
        "The letter still contains the personal-paragraph stub — it is not sendable.",
      );
    }
    update.mutate(
      {
        id: app.id,
        body: {
          cover_letter: coverLetter,
          cv_content: cvDraft,
          letter_meta: letterMeta,
        },
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
  // Entries past the layout's per-section budget (live on the draft): they render
  // nowhere unless something else is trimmed, so the editor flags them.
  const maxEntries = spec.data?.cv.max_entries ?? {};
  const overCap = overCapIds(cvDraft, maxEntries);

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
                cap={maxEntries[section]}
                overIds={overCap}
                freshIds={fresh.ids}
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
        <div
          className={`rounded-lg transition-shadow duration-1000 ${
            fresh.letter ? "ring-2 ring-emerald-300" : ""
          }`}
        >
          <LetterEditor
            applicationId={app.id}
            meta={letterMeta}
            onMeta={setLetterMeta}
            body={coverLetter}
            onBody={setCoverLetter}
            runs={app.runs}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function CvEditorSection({
  section,
  entries,
  db,
  onEdit,
  cap,
  overIds,
  freshIds,
}: {
  section: SectionKey;
  entries: CvEntry[];
  db: CvEntriesResponse | undefined;
  onEdit: (fn: (c: CvContent) => CvContent) => void;
  cap: number | undefined;
  overIds: Set<string>;
  freshIds: Set<string>;
}) {
  const missing = missingEntries(db, section, entries);
  if (entries.length === 0 && missing.length === 0) return null;
  const active = entries.filter((e) => !e.deselected).length;
  const over = cap != null && active > cap;
  return (
    <div>
      <h3 className="text-sm font-semibold">
        {SECTION_TITLES[section]}
        {cap != null && entries.length > 0 && (
          <span
            className={`ml-2 text-xs font-normal ${
              over ? "text-amber-600" : "text-muted-foreground"
            }`}
          >
            {active}/{cap} in the layout
          </span>
        )}
      </h3>
      <ul className="space-y-1">
        {entries.map((e, i) => {
          const row = db ? joinEntry(db, section, e) : null;
          const gone = db != null && row == null; // deleted from the career DB
          const isOver = overIds.has(e.id);
          const isFresh = freshIds.has(e.id);
          return (
            <li
              key={e.id}
              className={`flex items-center gap-1 rounded px-1 text-sm transition-colors duration-1000 ${
                e.deselected ? "opacity-50" : ""
              } ${isFresh ? "bg-emerald-100 dark:bg-emerald-900/40" : isOver ? "bg-amber-50 dark:bg-amber-900/20" : ""}`}
            >
              {isOver && (
                <span
                  title={`Beyond the layout's ${SECTION_TITLES[section].toLowerCase()} budget (${cap}) — it won't make the rendered CV.`}
                >
                  <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-amber-600" />
                </span>
              )}
              {e.pinned && (
                <span title="Pinned — survives applying a new generation run.">
                  <Pin className="h-3.5 w-3.5 shrink-0 text-sky-600" />
                </span>
              )}
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
                aria-label={e.pinned ? "Unpin" : "Pin"}
                title={
                  e.pinned
                    ? "Unpin — a new run may replace this entry again."
                    : "Pin — keep this entry when a new run is applied."
                }
                onClick={() => onEdit((c) => togglePin(c, section, i))}
              >
                {e.pinned ? (
                  <PinOff className="h-4 w-4" />
                ) : (
                  <Pin className="h-4 w-4" />
                )}
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
