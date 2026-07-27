import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

/** `/api/spa/personality/` — the questionnaire the personal paragraph grounds "you" in. */
export type PersonalityQuestion = {
  pk: number;
  slug: string;
  prompt: string;
  editable: boolean;
};

export type LetterTone = "personal" | "neutral" | "formal";
export type LetterFocus = "soft_skill" | "balanced" | "technical";

export type PersonalityRow = {
  id: number;
  answers: Record<string, string>;
  dossier: string;
  questions: PersonalityQuestion[];
  answers_updated_at: string | null;
  dossier_built_at: string | null;
  letter_tone: LetterTone;
  letter_focus: LetterFocus;
  writing_sample: string;
  style_dossier: string;
  sample_updated_at: string | null;
  style_built_at: string | null;
  updated_at: string;
};

export const TONE_OPTIONS: { value: LetterTone; label: string }[] = [
  { value: "personal", label: "Personal" },
  { value: "neutral", label: "Neutral" },
  { value: "formal", label: "Formal" },
];
export const FOCUS_OPTIONS: { value: LetterFocus; label: string }[] = [
  { value: "soft_skill", label: "Soft-skill focus" },
  { value: "balanced", label: "Balanced" },
  { value: "technical", label: "Technical focus" },
];

const URL = "/api/spa/personality/";
const KEY = ["personality"];

/** Mirrors the serializer's per-answer cap (spa/personality_questions.py). */
export const MAX_ANSWER_LEN = 280;
/** Mirrors the CRUD serializer's validate_prompt (spa/serializers.py). */
export const MAX_QUESTION_LEN = 280;

/** Add-a-question input validation — mirror of the serializer. Error string or null. */
export function validateQuestionPrompt(prompt: string): string | null {
  const text = prompt.trim();
  if (!text) return "A question needs a prompt.";
  if (text.length > MAX_QUESTION_LEN)
    return `Keep it under ${MAX_QUESTION_LEN} characters.`;
  return null;
}
/* ---------- pure helpers (unit-tested) ---------- */

/** Trim + drop blanks — exactly what the backend will store from a PATCH. */
export function cleanAnswers(
  draft: Record<string, string>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [id, text] of Object.entries(draft)) {
    const t = (text ?? "").trim();
    if (t) out[id] = t;
  }
  return out;
}

export function answeredCount(answers: Record<string, string>): number {
  return Object.keys(cleanAnswers(answers)).length;
}

/** Ids whose draft answer exceeds the cap — save stays disabled while non-empty. */
export function overlongAnswers(draft: Record<string, string>): string[] {
  return Object.entries(draft)
    .filter(([, text]) => (text ?? "").trim().length > MAX_ANSWER_LEN)
    .map(([id]) => id);
}

/** True when saving would change what the server stores (blank edits don't count). */
export function answersDirty(
  saved: Record<string, string>,
  draft: Record<string, string>,
): boolean {
  const a = cleanAnswers(saved);
  const b = cleanAnswers(draft);
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) if (a[k] !== b[k]) return true;
  return false;
}

/** Dossier freshness, mirroring PersonalityProfile.dossier_stale server-side.
 *  "stale" is informational — generation rebuilds via ensure_dossier on its own. */
export function dossierState(
  row: Pick<
    PersonalityRow,
    "answers" | "dossier" | "answers_updated_at" | "dossier_built_at"
  >,
): "none" | "stale" | "fresh" {
  if (answeredCount(row.answers) === 0) return "none"; // nothing to distil
  if (!row.dossier || !row.dossier_built_at) return "stale";
  if (
    row.answers_updated_at &&
    Date.parse(row.answers_updated_at) > Date.parse(row.dossier_built_at)
  )
    return "stale";
  return "fresh";
}

/** Style-dossier freshness — mirrors PersonalityProfile.style_stale server-side.
 *  Generation rebuilds it automatically on the next run (ensure_style_dossier). */
export function styleState(
  row: Pick<
    PersonalityRow,
    "writing_sample" | "style_dossier" | "sample_updated_at" | "style_built_at"
  >,
): "none" | "stale" | "fresh" {
  if (!row.writing_sample.trim()) return "none";
  if (!row.style_dossier || !row.style_built_at) return "stale";
  if (
    row.sample_updated_at &&
    Date.parse(row.sample_updated_at) > Date.parse(row.style_built_at)
  )
    return "stale";
  return "fresh";
}

export function personalityHint(
  capable: boolean,
  row: PersonalityRow | undefined,
): string | null {
  if (!capable || !row) return null;
  if (answeredCount(row.answers) > 0) return null;
  return (
    "No personality answers yet — the letter can't reflect who you are. " +
    "Fill the questionnaire under Account → Personality."
  );
}

/* ---------- query hooks ---------- */

export function usePersonality(enabled = true) {
  return useQuery({
    queryKey: KEY,
    queryFn: () => api<PersonalityRow>(URL),
    enabled,
  });
}

export function useUpdateAnswers() {
  const qc = useQueryClient();
  return useMutation({
    // PATCH replaces the whole answers dict — always send the full cleaned draft.
    mutationFn: (answers: Record<string, string>) =>
      api<PersonalityRow>(URL, {
        method: "PATCH",
        body: JSON.stringify({ answers }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useUpdateLetterSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (
      patch: Partial<
        Pick<PersonalityRow, "letter_tone" | "letter_focus" | "writing_sample">
      >,
    ) =>
      api<PersonalityRow>(URL, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useRebuildDossier() {
  const qc = useQueryClient();
  return useMutation({
    // Any executor can distil — no grade gate, unlike generation. Blank body =
    // the user's default executor ([fullstack]-chat-assistant-rework can add a pick).
    mutationFn: () =>
      api<{ dossier: string }>(`${URL}rebuild/`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

/** Same endpoint as useRebuildDossier; kind="style" force-rebuilds the writing-style
 *  dossier (ensure_style_dossier) instead of the personality one. */
export function useRebuildStyleDossier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<{ dossier: string }>(`${URL}rebuild/`, {
        method: "POST",
        body: JSON.stringify({ kind: "style" }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

const QUESTIONS_URL = "/api/spa/personality/questions/";

export function useCreateQuestion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (prompt: string) =>
      api<PersonalityQuestion>(QUESTIONS_URL, {
        method: "POST",
        body: JSON.stringify({ prompt }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useDeleteQuestion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pk: number) =>
      api<void>(`${QUESTIONS_URL}${pk}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
