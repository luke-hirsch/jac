import type { PersonaliseStatus, RankedId } from "@/lib/queries/portfolio";

/** What a questionnaire visit shows while its AI job runs (spa.ai_jobs.portfolio_personalise): a
 *  waiting card with the steps, which the visitor may leave for the standard portfolio; then the
 *  personal page, or the standard one with a word why. Pure, so the tests/ regime covers it. */

export type StepKey = "wake" | "rank" | "write";
export type StepState = "done" | "active" | "todo";
export type Step = { key: StepKey; state: StepState };

export type PersonaliseView =
  /** Nothing to wait for: the standard page and a lucky draw use no AI. */
  | { phase: "off" }
  | { phase: "working"; steps: Step[]; waking: boolean }
  /** What arrived. `ranked` null: the page keeps its usual order. */
  | { phase: "ready"; ranked: RankedId[] | null; intro: string }
  /** Nothing personal came: the standard page, with a word why. */
  | { phase: "failed" };

export type PersonaliseInput = {
  /** needsPersonalise(search): a topic, a question, or a style other than the default. */
  needed: boolean;
  /** The visitor wrote what they're looking for, so there's a ranking to wait for. */
  hasQuery: boolean;
  /** The job couldn't be queued: throttled, or the server is down. */
  startFailed: boolean;
  job: PersonaliseStatus | undefined;
  /** Some poll saw the model server waking. Sticky, so its step stays ticked once it's up. */
  woke: boolean;
  /** Past PERSONALISE_GIVE_UP_MS. */
  expired: boolean;
};

export function personaliseView(i: PersonaliseInput): PersonaliseView {
  if (!i.needed) return { phase: "off" };
  if (i.startFailed) return { phase: "failed" };
  const job = i.job;

  if (job?.status === "done" || job?.status === "failed" || i.expired) {
    const ranked = i.hasQuery ? (job?.ranked ?? null) : null;
    const intro = job?.intro ?? "";
    if (!ranked?.length && !intro) return { phase: "failed" };
    return { phase: "ready", ranked, intro };
  }

  const waking = Boolean(job?.waking);
  const ranked = job !== undefined && "ranked" in job;
  const steps: Step[] = [];
  if (waking || i.woke)
    steps.push({ key: "wake", state: waking ? "active" : "done" });
  if (i.hasQuery) {
    steps.push({
      key: "rank",
      state: ranked ? "done" : waking ? "todo" : "active",
    });
  }
  const writing = !waking && (!i.hasQuery || ranked);
  steps.push({ key: "write", state: writing ? "active" : "todo" });
  return { phase: "working", steps, waking };
}
