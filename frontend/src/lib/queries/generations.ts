import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

import type { Mode } from "./llm"; // single definition; no cycle

export type RunStatus = "pending" | "running" | "done" | "failed";

export type RunMeta = { mode: string; provider: string; model: string };

export type CvEntry = {
  id: string;
  label: string;
  relevance_score: number | null;
  deselected?: boolean;
  /** Entry pin: force-kept by every rung; survives applying a new run. */
  pinned?: boolean;
  /** Selection warning from the run — rendered by [frontend]-entry-pins-ui. */
  warning?: string;
  detail?: "full" | "compact";
};

export type Grounding = {
  count: number | null;
  claims: string[];
  /** Strong runs only: a repair pass replaced the body (true) or failed to (false). */
  repaired?: boolean;
};

export type CoverLetterResult = {
  language: string;
  subject: string;
  salutation: string;
  body: string;
  sender: Record<string, string>;
  recipient: Record<string, string>;
  date: string;
  closing: string;
  tone: string;
  focus: string;
  grounding: Grounding;
  /** Company-research source URLs (commercial web-search runs); [] otherwise. */
  sources: string[];
  /** The writer produced nothing — body is LETTER_STUB, must be regenerated. */
  is_stub: boolean;
  text: string;
};

export type TailoredResult = {
  meta: RunMeta;
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
  mode: string;
  provider: string;
  model: string;
  posting_title: string;
  created_at: string;
  updated_at: string;
};

export type GenerationForm = {
  job_application: number;
  mode: Exclude<Mode, ""> | "manual"; // "" = server default (standard)
  provider: string; // "" = the user's default executor
  model: string; // "" = catalog default
  params?: GenerationParams;
  letter_tone?: string; // "" = the profile default
  letter_focus?: string;
};

export type GenerationPayload = {
  job_application: number;
  mode?: string;
  provider?: string;
  model?: string;
  params?: GenerationParams;
  letter_tone?: string;
  letter_focus?: string;
};
export type GenerationParams = Record<string, string | number>;

// Webseocket event
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

/* ---------- query hooks ---------- */
const URL = "/api/jac/generations/";
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

/* ---------- pure helpers (unit-tested) ---------- */

export function toPayload(f: GenerationForm): GenerationPayload {
  const p: GenerationPayload = { job_application: f.job_application };
  if (f.mode) p.mode = f.mode;
  if (f.provider) p.provider = f.provider;
  if (f.model) p.model = f.model;
  if (f.params && Object.keys(f.params).length) p.params = f.params;
  if (f.letter_tone) p.letter_tone = f.letter_tone;
  if (f.letter_focus) p.letter_focus = f.letter_focus;
  return p;
}

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
export function knobParams(input: {
  effort?: string;
  temperature?: string;
}): GenerationParams {
  const p: GenerationParams = {};
  if (input.effort) p.effort = input.effort;
  const t = (input.temperature ?? "").trim();
  if (t !== "" && !Number.isNaN(Number(t))) p.temperature = Number(t);
  return p;
}
/* ---------- Badges---------- */

export type Badge = { tone: "green" | "amber" | "muted"; label: string };

export function groundingBadge(g: Grounding): Badge {
  if (g.count === null) return { tone: "muted", label: "not checked" };
  const suffix = g.repaired ? " · repaired" : "";
  if (g.count === 0) return { tone: "green", label: `grounded${suffix}` };
  return {
    tone: "amber",
    label: `${g.count} claim${g.count === 1 ? "" : "s"}${suffix}`,
  };
}
