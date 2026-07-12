import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

/** `/api/spa/personality/` — the questionnaire the personal paragraph grounds "you" in. */
export type PersonalityQuestion = { id: string; prompt: string };

export type PersonalityRow = {
  id: number;
  answers: Record<string, string>;
  dossier: string;
  /** Server-owned question pool — render from this, never hardcode it. */
  questions: PersonalityQuestion[];
  answers_updated_at: string | null;
  dossier_built_at: string | null;
  updated_at: string;
};

const URL = "/api/spa/personality/";
const KEY = ["personality"];

/** Mirrors the serializer's per-answer cap (spa/personality_questions.py). */
export const MAX_ANSWER_LEN = 280;

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

/** Generate-panel nag: ticking "Personal paragraph" with zero personality answers
 *  guarantees a stub — say so before the run, not after. Silent while loading. */
export function personalityHint(
  personalParagraph: boolean,
  row: PersonalityRow | undefined,
): string | null {
  if (!personalParagraph || !row) return null;
  if (answeredCount(row.answers) > 0) return null;
  return (
    "No personality answers yet — the personal paragraph will come out as a stub. " +
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

export function useRebuildDossier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<{ dossier: string }>(`${URL}rebuild/`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
