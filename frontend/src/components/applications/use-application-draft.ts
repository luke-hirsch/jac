/**
 * The editable copy of an application: what the content card edits and Save persists. It
 * lives above the cards because the export (save first) and the leave guard read it too.
 */
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { pinnedIds, type CvContent } from "@/lib/cv-doc";
import { reseedDrafts, storedText } from "@/lib/draft-merge";
import { drfFieldError } from "@/lib/field-save";
import {
  fillBlanks,
  hasStub,
  normalizeLetterMeta,
  senderFromProfile,
  type LetterMeta,
} from "@/lib/letter-doc";
import {
  useUpdateApplication,
  type ApplicationRow,
} from "@/lib/queries/applications";
import { useProfile } from "@/lib/queries/profile";

/** The drafted fields in the form they're compared in: cv_content by value, because a
 *  refetch returning identical JSON is not a change. */
function snapshot(
  cover: string,
  cv: CvContent,
  off: string[],
  meta: LetterMeta,
) {
  return {
    cover: storedText(cover),
    cv: JSON.stringify(cv),
    off: JSON.stringify(off),
    meta: JSON.stringify(meta),
  };
}

type Snapshot = ReturnType<typeof snapshot>;
const FIELDS: (keyof Snapshot)[] = ["cover", "cv", "off", "meta"];

export function useApplicationDraft(app: ApplicationRow) {
  const update = useUpdateApplication();
  const profile = useProfile();

  // Sender defaults come from the user profile (same source the pipeline's _sender() uses),
  // filled into *blank* fields only, so explicit edits and run-provided values always win.
  // The fill counts as the server's side of every comparison below: a sender nobody typed
  // is not an unsaved edit (the leave guard would nag on a fresh application) and must not
  // read as one that blocks a run's letter_meta from landing.
  const normalized = normalizeLetterMeta(
    app.letter_meta,
    app.posting_detail.language,
  );
  const serverMeta = profile.data
    ? {
        ...normalized,
        sender: fillBlanks(normalized.sender, senderFromProfile(profile.data)),
      }
    : normalized;

  const [coverLetter, setCoverLetter] = useState(app.cover_letter);
  const [cvDraft, setCvDraft] = useState<CvContent>(app.cv_content ?? {});
  const [sectionsOff, setSectionsOff] = useState<string[]>(
    app.sections_off ?? [],
  );
  const [letterMeta, setLetterMeta] = useState<LetterMeta>(serverMeta);

  const server = snapshot(
    app.cover_letter,
    app.cv_content ?? {},
    app.sections_off ?? [],
    serverMeta,
  );
  const draft = snapshot(coverLetter, cvDraft, sectionsOff, letterMeta);
  const dirty = FIELDS.some((f) => draft[f] !== server[f]);

  const [prev, setPrev] = useState(server);
  const [sent, setSent] = useState<Snapshot | null>(null);
  // A kept draft sits over a server copy that moved: say so, or Apply looks broken.
  const [conflict, setConflict] = useState(false);
  if (conflict && !dirty) setConflict(false);

  // "Adjusting state during render" (React docs, same pattern as usePagedList): when the
  // server copy moves (apply / auto-fill / a Save's echo), each field follows it only if
  // its draft has no edits of its own.
  if (FIELDS.some((f) => prev[f] !== server[f])) {
    const { take, conflicts } = reseedDrafts(prev, server, draft, sent);
    setPrev(server);
    if (take.includes("cover")) setCoverLetter(app.cover_letter);
    if (take.includes("cv")) setCvDraft(app.cv_content ?? {});
    if (take.includes("off")) setSectionsOff(app.sections_off ?? []);
    if (take.includes("meta")) setLetterMeta(serverMeta);
    if (conflicts.length > 0) setConflict(true);
  } else if (profile.data) {
    // Render-adjust: converges once merged. `else`, so it can't overwrite a take above with
    // this render's stale draft; the re-render right after gets here anyway.
    const sender = fillBlanks(
      letterMeta.sender,
      senderFromProfile(profile.data),
    );
    if (JSON.stringify(sender) !== JSON.stringify(letterMeta.sender)) {
      setLetterMeta({ ...letterMeta, sender });
    }
  }

  /** Persist the drafts. Resolves to the saved row, or null if it failed (already toasted). */
  async function save(): Promise<ApplicationRow | null> {
    if (
      (app.status === "sent" || app.status === "follow_up") &&
      hasStub(coverLetter)
    ) {
      toast.warning(
        "The letter is a placeholder — regenerate it before marking the application sent.",
      );
    }
    setSent(draft);
    setConflict(false);
    try {
      const row = await update.mutateAsync({
        id: app.id,
        body: {
          cover_letter: coverLetter,
          cv_content: cvDraft,
          letter_meta: letterMeta,
          pinned_entries: pinnedIds(cvDraft),
          sections_off: sectionsOff,
        },
      });
      toast.success("Application saved");
      return row;
    } catch (e) {
      setSent(null); // it never landed: the draft is still the only copy of these edits
      const pinMsg =
        e instanceof ApiError &&
        e.status === 400 &&
        (e.data as Record<string, unknown>)?.pinned_entries;
      toast.error(
        pinMsg
          ? drfFieldError(e, "pinned_entries")
          : "Could not save the application",
      );
      return null;
    }
  }

  /** The way out of a conflict: drop the unsaved edits, show the saved copy. */
  function discard() {
    setCoverLetter(app.cover_letter);
    setCvDraft(app.cv_content ?? {});
    setSectionsOff(app.sections_off ?? []);
    setLetterMeta(serverMeta);
    setConflict(false);
  }

  return {
    coverLetter,
    setCoverLetter,
    cvDraft,
    setCvDraft,
    sectionsOff,
    setSectionsOff,
    letterMeta,
    setLetterMeta,
    dirty,
    conflict,
    saving: update.isPending,
    save,
    discard,
  };
}

export type ApplicationDraft = ReturnType<typeof useApplicationDraft>;
