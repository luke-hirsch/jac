import { useState } from "react";
import { toast } from "sonner";
import {
  ArrowDown,
  ArrowUp,
  AlignLeft,
  Minus,
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
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { ApiError } from "@/lib/api";
import { drfFieldError } from "@/lib/field-save";
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
  pinnedIds,
  removeEntry,
  toggleDeselect,
  togglePin,
  toggleSection,
  activeContent,
  setDetail,
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
  contactLine,
  type LetterMeta,
} from "@/lib/letter-doc";
import { useProfile } from "@/lib/queries/profile";
import { effectiveCaps } from "@/lib/render/fit";
import { entryDetail } from "@/lib/render/parts";
import { useLayoutSpec } from "@/lib/render/spec";
import { LetterEditor } from "./letter-editor";
import { type Fresh } from "./use-fresh-highlight";
import { usePreflight } from "./use-preflight";

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
  const [sectionsOff, setSectionsOff] = useState<string[]>(
    app.sections_off ?? [],
  );
  const serverMeta = JSON.stringify(
    normalizeLetterMeta(app.letter_meta, app.posting_detail.language),
  );
  const [letterMeta, setLetterMeta] = useState<LetterMeta>(() =>
    normalizeLetterMeta(app.letter_meta, app.posting_detail.language),
  );

  // "Adjusting state during render" (React docs, same pattern as usePagedList):
  // re-seed the local drafts when the server copy changes (apply / auto-fill),
  // discarding any unsaved edits in favour of the fresher server state. cv_content is
  // compared by value — a refetch returning identical JSON must not clobber the draft.
  const serverCv = JSON.stringify(app.cv_content ?? {});
  const serverOff = JSON.stringify(app.sections_off ?? []);
  const [prevServer, setPrevServer] = useState({
    cover: app.cover_letter,
    status: app.status,
    cv: serverCv,
    off: serverOff,
    meta: serverMeta,
  });
  if (
    prevServer.cover !== app.cover_letter ||
    prevServer.status !== app.status ||
    prevServer.cv !== serverCv ||
    prevServer.off !== serverOff ||
    prevServer.meta !== serverMeta
  ) {
    setPrevServer({
      cover: app.cover_letter,
      status: app.status,
      cv: serverCv,
      off: serverOff,
      meta: serverMeta,
    });
    setCoverLetter(app.cover_letter);
    setStatus(app.status);
    setCvDraft(app.cv_content ?? {});
    setSectionsOff(app.sections_off ?? []);
    setLetterMeta(
      normalizeLetterMeta(app.letter_meta, app.posting_detail.language),
    );
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
    JSON.stringify(sectionsOff) !== serverOff ||
    JSON.stringify(letterMeta) !== serverMeta;

  function onSave() {
    if (
      (app.status === "sent" || app.status === "follow_up") &&
      hasStub(coverLetter)
    ) {
      toast.warning(
        "The letter is a placeholder — regenerate it before marking the application sent.",
      );
    }
    update.mutate(
      {
        id: app.id,
        body: {
          cover_letter: coverLetter,
          cv_content: cvDraft,
          letter_meta: letterMeta,
          pinned_entries: pinnedIds(cvDraft),
          sections_off: sectionsOff,
        },
      },
      {
        onSuccess: () => toast.success("Application saved"),
        onError: (e) => {
          const pinMsg =
            e instanceof ApiError &&
            e.status === 400 &&
            (e.data as Record<string, unknown>)?.pinned_entries;
          toast.error(
            pinMsg
              ? drfFieldError(e, "pinned_entries")
              : "Could not save the application",
          );
        },
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
  // Switching a section off loosens the others — show the caps the export will use,
  // not the template's untouched numbers.
  const maxEntries = effectiveCaps(
    spec.data?.cv.max_entries ?? {},
    sectionsOff,
  );

  // The real page fit, measured in the background off the live draft — not the crude
  // template cap. It is what the export will do, so the editor can show it up front.
  const preflight = usePreflight({
    spec: spec.data,
    db: careerDb.data,
    content: activeContent(cvDraft, sectionsOff),
    sectionsOff,
    name: letterMeta.sender.name || "CV",
    contact: contactLine(letterMeta.sender, {
      socials: profile.data?.show_socials ?? false,
    }),
    summary: profile.data?.bio ?? "",
    meta: letterMeta,
    body: coverLetter,
  });
  const willCut = new Set(preflight.result?.droppedIds ?? []);
  // Past the section's template budget and not bought back by the grow pass: also
  // absent from the CV, but for a different reason, so the tooltip differs.
  const overBudget = new Set(preflight.result?.cutIds ?? []);
  const willShorten = new Set(preflight.result?.demotedIds ?? []);
  const willAdd = new Set(preflight.result?.addedIds ?? []);

  return (
    <Card id="curate">
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
                detailed={spec.data?.cv.detailed ?? {}}
                willCut={willCut}
                overBudget={overBudget}
                willShorten={willShorten}
                willAdd={willAdd}
                freshIds={fresh.ids}
                off={sectionsOff.includes(section)}
                onToggleSection={() =>
                  setSectionsOff((s) => toggleSection(s, section))
                }
              />
            ))}
            <p className="text-xs text-muted-foreground">
              {preflight.measuring
                ? "measuring the layout…"
                : preflight.result
                  ? `${preflight.result.pages} of ${spec.data?.cv.pages ?? 1} page(s) used` +
                    (preflight.result.addedIds.length
                      ? ` — ${preflight.result.addedIds.length} extra entr${
                          preflight.result.addedIds.length === 1 ? "y" : "ies"
                        } added to fill it`
                      : "")
                  : ""}
            </p>
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
            letterPages={preflight.letterPages}
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
  freshIds,
  willCut,
  overBudget,
  willShorten,
  willAdd,
  detailed,
  off,
  onToggleSection,
}: {
  section: SectionKey;
  entries: CvEntry[];
  db: CvEntriesResponse | undefined;
  onEdit: (fn: (c: CvContent) => CvContent) => void;
  cap: number | undefined;
  detailed: Record<string, number>;
  willCut: Set<string>;
  overBudget: Set<string>;
  willShorten: Set<string>;
  willAdd: Set<string>;
  freshIds: Set<string>;
  off: boolean;
  onToggleSection: () => void;
}) {
  const missing = missingEntries(db, section, entries);
  if (entries.length === 0 && missing.length === 0) return null;
  const active = entries.filter((e) => !e.deselected).length;
  const over = !off && cap != null && active > cap;
  return (
    <div className={off ? "opacity-50" : undefined}>
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Checkbox
          checked={!off}
          onCheckedChange={onToggleSection}
          aria-label={`include ${SECTION_TITLES[section]}`}
        />
        {SECTION_TITLES[section]}
        {off ? (
          <span className="text-xs font-normal text-muted-foreground">
            not on this CV — its budget goes to the other sections
          </span>
        ) : (
          cap != null &&
          entries.length > 0 && (
            <span
              className={`text-xs font-normal ${
                over ? "text-amber-600" : "text-muted-foreground"
              }`}
            >
              {active}/{cap} in the layout
            </span>
          )
        )}
      </h3>
      {!off && (
        <>
          <ul className="space-y-1">
            {entries.map((e, i) => {
              const row = db ? joinEntry(db, section, e) : null;
              const gone = db != null && row == null; // deleted from the career DB
              const isOver = willCut.has(e.id) || overBudget.has(e.id);
              const isFresh = freshIds.has(e.id);
              const detail = entryDetail(e, i, section, detailed, willShorten);
              return (
                <li
                  key={e.id}
                  className={`flex items-center gap-1 rounded px-1 text-sm transition-colors duration-1000 ${
                    e.deselected ? "opacity-50" : ""
                  } ${isFresh ? "bg-emerald-100 dark:bg-emerald-900/40" : isOver ? "bg-amber-50 dark:bg-amber-900/20" : ""}`}
                >
                  {isOver && (
                    <span
                      title={
                        overBudget.has(e.id)
                          ? `Past this section's layout budget (${cap}) — it is not on the rendered CV. Reorder it up, or pin it to force it in.`
                          : "The page fit has to cut this entry — deselect or shorten something else to keep it."
                      }
                    >
                      <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-amber-600" />
                    </span>
                  )}
                  {e.pinned && (
                    <span title="Pinned — survives applying a new generation run.">
                      <Pin className="h-3.5 w-3.5 shrink-0 text-sky-600" />
                      {e.warning && (
                        <span title={e.warning}>
                          <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-amber-600" />
                        </span>
                      )}
                    </span>
                  )}
                  <span
                    className={`flex-1 ${e.deselected ? "line-through" : ""}`}
                  >
                    {row ? labelFor(section, row) : e.label}
                    {gone && (
                      <span className="ml-1 text-xs text-destructive">
                        (no longer in the career DB)
                      </span>
                    )}
                  </span>
                  {e.relevance_score != null && (
                    <Badge variant="outline">
                      {e.relevance_score.toFixed(2)}
                    </Badge>
                  )}
                  {isOver && (
                    <Badge variant="outline" className="text-amber-600">
                      won't fit
                    </Badge>
                  )}
                  {willShorten.has(e.id) && (
                    <Badge variant="outline" className="text-muted-foreground">
                      title only
                    </Badge>
                  )}
                  {willAdd.has(e.id) && (
                    <Badge variant="outline" className="text-emerald-600">
                      fills the page
                    </Badge>
                  )}
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={
                      detail === "full" ? "Show title only" : "Show description"
                    }
                    title={
                      detail === "full"
                        ? "Show the title only — keeps the position, drops the description."
                        : "Show the full description."
                    }
                    onClick={() =>
                      onEdit((c) =>
                        setDetail(
                          c,
                          section,
                          i,
                          detail === "full" ? "compact" : "full",
                        ),
                      )
                    }
                  >
                    {detail === "full" ? (
                      <AlignLeft className="h-4 w-4" />
                    ) : (
                      <Minus className="h-4 w-4" />
                    )}
                  </Button>
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
        </>
      )}
    </div>
  );
}
