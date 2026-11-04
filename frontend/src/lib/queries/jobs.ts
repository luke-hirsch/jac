import { api, ApiError } from "@/lib/api";

/** A short AI job: the request answers 202 at once, a worker does the work, the page polls this
 *  row. Mirrors llm_connector AiJobSerializer. */
export type AiJob<T> = {
  id: string;
  kind: string;
  status: "pending" | "running" | "done" | "failed";
  stage: string;
  queue_ahead: number;
  result: T | null;
  error: string;
};

/** The server's stage while the model server is off or starting (llm_connector/base.py). */
const WAITING_STAGE = "waiting for compute";

/** Longer than the worker's own wait for compute: the job ends first, and says why. */
export const AI_JOB_TIMEOUT_MS = 8 * 60_000;

/** Quick while a commercial reply is seconds away, then settling down. */
export function pollDelayMs(elapsedMs: number): number {
  return elapsedMs < 15_000 ? 1_000 : 3_000;
}

/** What to show beside the spinner; null while there's nothing to add. */
export function jobStatusLabel(
  job: Pick<AiJob<unknown>, "status" | "stage" | "queue_ahead">,
): string | null {
  if (job.status === "pending" && job.queue_ahead > 0)
    return `In line · ${job.queue_ahead} ahead`;
  if (job.stage === WAITING_STAGE) return "Waiting for the AI to wake…";
  return null;
}

type RunOptions = {
  onUpdate?: (job: AiJob<unknown>) => void;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
};

/** Start a job and poll it to the end. A failed job throws an ApiError carrying its message, so
 *  callers keep their ordinary error handling. */
export async function runAiJob<T>(
  start: () => Promise<AiJob<T>>,
  {
    onUpdate,
    now = Date.now,
    sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
  }: RunOptions = {},
): Promise<T> {
  const startedAt = now();
  let job = await start();
  for (;;) {
    onUpdate?.(job);
    if (job.status === "done") return job.result as T;
    if (job.status === "failed") throw new ApiError(502, { detail: job.error });
    const elapsed = now() - startedAt;
    if (elapsed > AI_JOB_TIMEOUT_MS)
      throw new ApiError(504, {
        detail: "This is taking too long — try again later.",
      });
    await sleep(pollDelayMs(elapsed));
    job = await api<AiJob<T>>(`/api/llm/jobs/${job.id}/`);
  }
}
