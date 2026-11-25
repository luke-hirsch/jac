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
  needed: true,
  checked: true,
  started: false,
  refused: false,
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
  it("a lucky draw or the standard page has nothing to wait for", () => {
    expect(personaliseView(input({ needed: false }))).toEqual({ phase: "off" });
    expect(
      personaliseView(
        input({ needed: false, job: job({ status: "done", intro: "Hi" }) }),
      ),
    ).toEqual({ phase: "off" });
  });

  it("a start that failed (throttled, server down) is the standard page", () => {
    expect(personaliseView(input({ startFailed: true }))).toEqual({
      phase: "failed",
    });
  });

  it("an answer without a solved check gets no AI: the stamp, a shared address", () => {
    expect(personaliseView(input({ checked: false }))).toEqual({
      phase: "unchecked",
    });
  });

  it("a check the server refused reads the same: spent by an earlier load, or expired", () => {
    expect(
      personaliseView(input({ refused: true, startFailed: true })),
    ).toEqual({ phase: "unchecked" });
  });

  it("a job this tab already queued carries on without the payload", () => {
    // Back from the about page: the address restores the answer, the cache holds its job.
    expect(
      personaliseView(
        input({
          checked: false,
          started: true,
          job: job({ status: "done", ranked: RANKED, intro: "Hi" }),
        }),
      ),
    ).toEqual({ phase: "ready", ranked: RANKED, intro: "Hi" });
    expect(steps({ checked: false, started: true })).toEqual([
      "rank:active",
      "write:todo",
    ]);
  });

  it("a lucky draw or the standard page needs no check", () => {
    expect(personaliseView(input({ needed: false, checked: false }))).toEqual({
      phase: "off",
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
