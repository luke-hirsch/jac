import { describe, expect, it } from "vitest";
import {
  personaliseView,
  type PersonaliseInput,
} from "@/lib/portfolio/personalise";
import type { PersonaliseStatus } from "@/lib/queries/portfolio";

/**
 * What a questionnaire visit shows while its AI job runs: a waiting card the visitor can leave for
 * the standard portfolio, then the personal page, or the standard one with a word why.
 */

const RANKED = [{ id: "job:1", score: 0.9 }];

const job = (over: Partial<PersonaliseStatus> = {}): PersonaliseStatus => ({
  status: "running",
  waking: false,
  intro: "",
  ...over,
});

const input = (over: Partial<PersonaliseInput> = {}): PersonaliseInput => ({
  lucky: false,
  hasQuery: true,
  startFailed: false,
  job: undefined,
  woke: false,
  expired: false,
  ...over,
});

const steps = (over: Partial<PersonaliseInput>) => {
  const view = personaliseView(input(over));
  if (view.phase !== "working") throw new Error(`phase ${view.phase}`);
  return view.steps.map((s) => `${s.key}:${s.state}`);
};

describe("personaliseView", () => {
  it("a lucky draw has nothing to wait for", () => {
    expect(personaliseView(input({ lucky: true }))).toEqual({ phase: "off" });
  });

  it("a start that failed (throttled, server down) is the standard page", () => {
    expect(personaliseView(input({ startFailed: true }))).toEqual({
      phase: "failed",
    });
  });

  it("before the first poll answers, the first step is under way", () => {
    expect(steps({})).toEqual(["rank:active", "write:todo"]);
    expect(steps({ hasQuery: false })).toEqual(["write:active"]);
  });

  it("a sleeping model server comes first, and holds the rest", () => {
    expect(steps({ job: job({ waking: true }), woke: true })).toEqual([
      "wake:active",
      "rank:todo",
      "write:todo",
    ]);
  });

  it("once awake, the wake stays ticked off", () => {
    expect(steps({ job: job(), woke: true })).toEqual([
      "wake:done",
      "rank:active",
      "write:todo",
    ]);
  });

  it("a published ranking ticks its step off, whatever it found", () => {
    expect(steps({ job: job({ ranked: RANKED }) })).toEqual([
      "rank:done",
      "write:active",
    ]);
    expect(steps({ job: job({ ranked: null }) })).toEqual([
      "rank:done",
      "write:active",
    ]);
  });

  it("a queued job is still working on its first step", () => {
    expect(steps({ job: job({ status: "pending" }) })).toEqual([
      "rank:active",
      "write:todo",
    ]);
  });

  it("done: the ranking and the welcome", () => {
    expect(
      personaliseView(
        input({
          job: job({ status: "done", ranked: RANKED, intro: "Hi" }),
        }),
      ),
    ).toEqual({ phase: "ready", ranked: RANKED, intro: "Hi" });
  });

  it("done with only a welcome: the usual order stays", () => {
    expect(
      personaliseView(
        input({ job: job({ status: "done", ranked: null, intro: "Hi" }) }),
      ),
    ).toEqual({ phase: "ready", ranked: null, intro: "Hi" });
  });

  it("without a question, a ranking is ignored", () => {
    expect(
      personaliseView(
        input({
          hasQuery: false,
          job: job({ status: "done", ranked: RANKED, intro: "Hi" }),
        }),
      ),
    ).toEqual({ phase: "ready", ranked: null, intro: "Hi" });
  });

  it("done with nothing personal is the standard page", () => {
    expect(
      personaliseView(
        input({ job: job({ status: "done", ranked: [], intro: "" }) }),
      ),
    ).toEqual({ phase: "failed" });
  });

  it("a failed job is the standard page", () => {
    expect(personaliseView(input({ job: job({ status: "failed" }) }))).toEqual({
      phase: "failed",
    });
  });

  it("past the deadline, what has arrived is shown", () => {
    expect(
      personaliseView(input({ expired: true, job: job({ ranked: RANKED }) })),
    ).toEqual({ phase: "ready", ranked: RANKED, intro: "" });
    expect(
      personaliseView(input({ expired: true, job: job({ waking: true }) })),
    ).toEqual({ phase: "failed" });
  });
});
