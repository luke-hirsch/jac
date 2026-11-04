import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { FORM_WORD, type AddressForm } from "@/lib/register";

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

/** German letters only: the form of "you" the letter uses ("sie" | "ihr" | "du";
 *  "" for English) and the pronouns in the body that contradict it. A flag — the
 *  server never rewrites a letter on a regex. */
export type Register = { form: string; leaks: string[] };

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
  /** Absent on runs made before the register existed. */
  register?: Register;
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
  /** Runs standing before this one in its lane, whoever's they are. 0 once it left the queue. */
  queue_ahead: number;
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
      queue_ahead?: number;
    }
  | { event: "progress"; status: RunStatus; stage: string }
  | { event: "done"; status: RunStatus; result: TailoredResult }
  | { event: "failed"; status: RunStatus; error: string };

export type RunState = {
  /** "unseeded": no snapshot yet, so nothing is known about the run. Deliberately not
   *  "pending", which would flash Queued and Abort on a run that ended long ago. */
  status: RunStatus | "unseeded";
  stage: string;
  result: TailoredResult | null;
  error: string;
  queueAhead: number;
};

export const UNSEEDED_RUN: RunState = {
  status: "unseeded",
  stage: "",
  result: null,
  error: "",
  queueAhead: 0,
};

/* ---------- query hooks ---------- */
const URL = "/api/jac/generations/";
export function useCreateGeneration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (form: GenerationForm) =>
      api<GenerationRun>(URL, {
        method: "POST",
        body: JSON.stringify(toPayload(form)),
      }),
    onSuccess: (run) => {
      // Its own answer seeds the lifecycle at once: no unseeded gap in which Generate is
      // clickable again. The application lists its runs, so it has to hear of this one.
      qc.setQueryData(["jac", "generations", run.id], run);
      qc.invalidateQueries({ queryKey: ["jac", "applications"] });
    },
  });
}

export function useGeneration(id: number | null) {
  return useQuery({
    queryKey: ["jac", "generations", id],
    queryFn: () => api<GenerationRun>(`${URL}${id}/`),
    enabled: id != null,
    // Other people's runs move this one up the line; no event says so, so read it while queued.
    refetchInterval: (query) =>
      query.state.data?.status === "pending" ? QUEUE_POLL_MS : false,
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

const QUEUE_POLL_MS = 15_000;

/** Queued with nobody ahead for this long: nothing is picking runs up, so the worker is probably
 *  down. Being behind other runs is never suspicious, however long it takes. */
export const STUCK_QUEUE_AFTER_S = 120;

/** The server's stage while the model server is off or starting (jac/tasks.py WAITING_STAGE). */
export const WAITING_STAGE = "waiting for compute";

export function pendingAgeSeconds(createdAt: string, now: Date): number {
  const created = Date.parse(createdAt);
  if (Number.isNaN(created)) return 0;
  return Math.max(0, Math.floor((now.getTime() - created) / 1000));
}

export function isRunInFlight(status: RunState["status"]): boolean {
  return status === "pending" || status === "running";
}

export function isStuckInQueue(
  status: RunState["status"],
  queueAhead: number,
  createdAt: string,
  now: Date,
): boolean {
  return (
    status === "pending" &&
    queueAhead === 0 &&
    pendingAgeSeconds(createdAt, now) > STUCK_QUEUE_AFTER_S
  );
}

export function isWaitingForCompute(state: Pick<RunState, "status" | "stage">) {
  return state.status === "running" && state.stage === WAITING_STAGE;
}

/** What a run in flight is doing. It feeds a live region, so no elapsed time in here: a
 *  screen reader would read out every second of it. */
export function runStatusLine(state: RunState): string {
  if (state.status === "pending") {
    const ahead =
      state.queueAhead === 1
        ? " · 1 run ahead"
        : state.queueAhead > 1
          ? ` · ${state.queueAhead} runs ahead`
          : "";
    return `Queued${ahead}`;
  }
  if (isWaitingForCompute(state)) return "Waiting for compute";
  return `Generating… ${state.stage || "starting"}`;
}

/** `reset`: another run was selected. What's known about the old one says nothing of it. */
export type RunAction = WsEvent | { event: "reset" };

export function runReducer(state: RunState, e: RunAction): RunState {
  switch (e.event) {
    case "reset":
      return UNSEEDED_RUN;
    case "snapshot":
      return {
        status: e.status,
        stage: e.stage,
        result: e.result,
        error: e.error,
        queueAhead: e.queue_ahead ?? 0,
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

/** null = nothing to say: an English letter, or a run from before the register. */
export function registerBadge(r: Register | undefined): Badge | null {
  if (!r?.form) return null;
  const form = FORM_WORD[r.form as AddressForm] ?? r.form;
  if (r.leaks.length)
    return { tone: "amber", label: `wrong "you": ${r.leaks.join(", ")}` };
  return { tone: "muted", label: `"${form}" form` };
}
