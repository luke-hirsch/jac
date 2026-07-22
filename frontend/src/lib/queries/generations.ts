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
};

export type Grounding = {
  count: number | null;
  claims: string[];
  /** Strong runs only: a repair pass replaced the body (true) or failed to (false). */
  repaired?: boolean;
};

export type Critique = {
  count: number | null;
  claims: string[];
  /** Standard+ only: a quality repair replaced the body (true) or the rewrite failed (false). */
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
  snippets_used: string[];
  snippet_provenance: { native: string[]; translated: string[] };
  ai_share: number;
  grounding: Grounding;
  /** Prose-quality critique (standard+strong): advisory, feeds the repair pass. */
  critique?: Critique;
  /** How the snippets were picked: embedding cosine vs the structural fallback. */
  snippet_ranking?: "embedding" | "structural";
  personal_paragraph: string;
  personal_paragraph_is_stub: boolean;
  personal_paragraph_sources: string[];
  personal_paragraph_grounding: Grounding;
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
};

export type GenerationPayload = {
  job_application: number;
  mode?: string;
  provider?: string;
  model?: string;
  params?: GenerationParams;
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
  // params is optional (the auto-run path and no-knob picks omit it) — guard
  // before Object.keys, and only send a non-empty object.
  if (f.params && Object.keys(f.params).length) p.params = f.params;
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

export function aiShareBadge(share: number): Badge {
  const pct = Math.round(share * 100);
  return { tone: pct <= 25 ? "green" : "amber", label: `${pct}% AI` };
}

export function groundingBadge(g: Grounding): Badge {
  if (g.count === null) return { tone: "muted", label: "not checked" };
  const suffix = g.repaired ? " · repaired" : "";
  if (g.count === 0) return { tone: "green", label: `grounded${suffix}` };
  return {
    tone: "amber",
    label: `${g.count} claim${g.count === 1 ? "" : "s"}${suffix}`,
  };
}

export function qualityBadge(c: Critique | undefined): Badge | null {
  // TODO: when the critic didn't run (light grade, old runs, critic down)
  // there is nothing to say — no badge, unlike grounding's explicit "not checked".
  if (!c || c.count === null) return null;
  const suffix = c.repaired ? " · repaired" : "";
  if (c.count === 0) return { tone: "green", label: "quality ok" };
  return {
    tone: "amber",
    label: `${c.count} issue${c.count === 1 ? "" : "s"}${suffix}`,
  };
}
