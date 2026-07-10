import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type Grade = "light" | "standard" | "strong";
export type RunStatus = "pending" | "running" | "done" | "failed";

export type Grounding = { count: number | null; claims: string[] };

export type CoverLetterResult = {
  language: string;
  subject: string;
  salutation: string;
  body: string;
  sender: Record<string, string>;
  recipient: Record<string, string>;
  date: string;
  closing: string;
  snippets_used: string[];
  snippet_provenance: { native: string[]; translated: string[] };
  ai_share: number;
  grounding: Grounding;
  personal_paragraph: string;
  personal_paragraph_is_stub: boolean;
  personal_paragraph_sources: string[];
  personal_paragraph_grounding: Grounding;
  text: string;
};

export type CvEntry = {
  id: string;
  label: string;
  relevance_score: number | null;
  deselected?: boolean;
};

export type TailoredResult = {
  meta: { grade: string; alias: string };
  cv: Record<string, CvEntry[]>;
  cover_letter: CoverLetterResult;
};

export type GenerationRun = {
  id: number;
  job_application: number;
  status: RunStatus;
  stage: string;
  error: string;
  result: TailoredResult | null;
  grade: string;
  alias: string;
  personal_paragraph: boolean;
  verify_grounding: boolean;
  evaluation: string;
  score: string;
  posting_title: string;
  created_at: string;
  updated_at: string;
};

export type GenerationForm = {
  job_application: number;
  grade: Grade | ""; // "" => the task auto-detects from the alias strength
  alias: string;
  verify_grounding: boolean;
  personal_paragraph: boolean;
};

export type GenerationPayload = {
  job_application: number;
  alias: string;
  verify_grounding: boolean;
  personal_paragraph: boolean;
  grade?: Grade;
};

const URL = "/api/jac/generations/";

/* ---------- pure helpers (unit-tested) ---------- */

export function toPayload(f: GenerationForm): GenerationPayload {
  const p: GenerationPayload = {
    job_application: f.job_application,
    alias: f.alias,
    verify_grounding: f.verify_grounding,
    personal_paragraph: f.personal_paragraph,
  };
  if (f.grade) p.grade = f.grade; // omit when "" so the server auto-detects
  return p;
}

export type Badge = { tone: "green" | "amber" | "muted"; label: string };

export function aiShareBadge(share: number): Badge {
  const pct = Math.round(share * 100);
  // Low AI share = mostly the candidate's own words; high = heavily machine-written.
  return { tone: pct <= 25 ? "green" : "amber", label: `${pct}% AI` };
}

export function groundingBadge(g: Grounding): Badge {
  if (g.count === null) return { tone: "muted", label: "not checked" };
  if (g.count === 0) return { tone: "green", label: "grounded" };
  return {
    tone: "amber",
    label: `${g.count} claim${g.count === 1 ? "" : "s"}`,
  };
}

/** Fold a WS event (or a REST snapshot reshaped as one) into run state. */
export type WsEvent =
  | {
      event: "snapshot";
      status: RunStatus;
      stage: string;
      result: TailoredResult | null;
      error: string;
    }
  | { event: "progress"; status: RunStatus; stage: string }
  | { event: "done"; status: RunStatus; result: TailoredResult }
  | { event: "failed"; status: RunStatus; error: string };

export type RunState = {
  status: RunStatus;
  stage: string;
  result: TailoredResult | null;
  error: string;
};

/** A run still `pending` after this long was never picked up — the worker is
 *  probably down (the enqueued task itself expires server-side after 15 min). */
export const STALE_PENDING_AFTER_S = 30;

export function pendingAgeSeconds(createdAt: string, now: Date): number {
  const created = Date.parse(createdAt);
  if (Number.isNaN(created)) return 0;
  return Math.max(0, Math.floor((now.getTime() - created) / 1000));
}

export function isStalePending(
  status: RunStatus,
  createdAt: string,
  now: Date,
): boolean {
  return (
    status === "pending" &&
    pendingAgeSeconds(createdAt, now) > STALE_PENDING_AFTER_S
  );
}

export function runReducer(state: RunState, e: WsEvent): RunState {
  switch (e.event) {
    case "snapshot":
      return {
        status: e.status,
        stage: e.stage,
        result: e.result,
        error: e.error,
      };
    case "progress":
      return { ...state, status: e.status, stage: e.stage };
    case "done":
      return { ...state, status: e.status, result: e.result, stage: "done" };
    case "failed":
      return { ...state, status: e.status, error: e.error };
    default:
      return state;
  }
}

/* ---------- query hooks ---------- */

export function useCreateGeneration() {
  return useMutation({
    mutationFn: (form: GenerationForm) =>
      api<GenerationRun>(URL, {
        method: "POST",
        body: JSON.stringify(toPayload(form)),
      }),
  });
}

export function useGeneration(id: number | null) {
  return useQuery({
    queryKey: ["jac", "generations", id],
    queryFn: () => api<GenerationRun>(`${URL}${id}/`),
    enabled: id != null,
  });
}

export function useCancelGeneration() {
  return useMutation({
    mutationFn: (id: number) =>
      api<GenerationRun>(`${URL}${id}/cancel/`, { method: "POST" }),
  });
}
