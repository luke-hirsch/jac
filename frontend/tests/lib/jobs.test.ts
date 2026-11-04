import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { postingsToBatch, splitPostings } from "@/lib/queries/applications";
import {
  AI_JOB_TIMEOUT_MS,
  jobStatusLabel,
  pollDelayMs,
  runAiJob,
  type AiJob,
} from "@/lib/queries/jobs";

function job(
  fields: Partial<AiJob<{ text: string }>>,
): AiJob<{ text: string }> {
  return {
    id: "abc",
    kind: "letter_rewrite",
    status: "pending",
    stage: "",
    queue_ahead: 0,
    result: null,
    error: "",
    ...fields,
  };
}

function polling(...answers: AiJob<{ text: string }>[]) {
  const fetchMock = vi.fn(async () => ({
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: async () => answers.shift(),
    text: async () => "",
  }));
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("document", { cookie: "" });
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("runAiJob", () => {
  const noWait = { sleep: async () => {} };

  it("polls until the job is done and returns its result", async () => {
    const fetchMock = polling(
      job({ status: "running" }),
      job({ status: "done", result: { text: "New." } }),
    );
    const result = await runAiJob(async () => job({}), noWait);
    expect(result).toEqual({ text: "New." });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("needs no poll when the first answer is already final", async () => {
    const fetchMock = polling();
    const result = await runAiJob(
      async () => job({ status: "done", result: { text: "Quick." } }),
      noWait,
    );
    expect(result).toEqual({ text: "Quick." });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("a failed job throws its message, for ordinary error handling", async () => {
    polling(job({ status: "failed", error: "The AI isn't available." }));
    await expect(runAiJob(async () => job({}), noWait)).rejects.toMatchObject({
      status: 502,
      data: { detail: "The AI isn't available." },
    });
  });

  it("reports every state it sees", async () => {
    polling(
      job({ status: "running", stage: "waiting for compute" }),
      job({ status: "done", result: { text: "x" } }),
    );
    const stages: string[] = [];
    await runAiJob(async () => job({ queue_ahead: 2 }), {
      ...noWait,
      onUpdate: (j) => stages.push(`${j.status}:${j.stage}`),
    });
    expect(stages).toEqual([
      "pending:",
      "running:waiting for compute",
      "done:",
    ]);
  });

  it("gives up with a message rather than polling forever", async () => {
    polling(job({}), job({}));
    let t = 0;
    const attempt = runAiJob(async () => job({}), {
      now: () => {
        t += AI_JOB_TIMEOUT_MS;
        return t;
      },
      ...noWait,
    });
    await expect(attempt).rejects.toBeInstanceOf(ApiError);
  });
});

describe("pollDelayMs", () => {
  it("is quick at first, then settles down", () => {
    expect(pollDelayMs(1_000)).toBeLessThan(pollDelayMs(60_000));
  });
});

describe("jobStatusLabel", () => {
  it("names the wait, and stays out of the way otherwise", () => {
    expect(jobStatusLabel(job({ queue_ahead: 2 }))).toBe("In line · 2 ahead");
    expect(
      jobStatusLabel(job({ status: "running", stage: "waiting for compute" })),
    ).toBe("Waiting for the AI to wake…");
    expect(jobStatusLabel(job({}))).toBeNull();
    expect(jobStatusLabel(job({ status: "running" }))).toBeNull();
  });
});

describe("splitPostings", () => {
  it("splits on a line of three or more dashes", () => {
    expect(
      splitPostings("Cook wanted\n---\nPilot wanted\n-----\nDriver"),
    ).toEqual(["Cook wanted", "Pilot wanted", "Driver"]);
  });
  it("one posting is one, however long", () => {
    expect(splitPostings("Cook wanted\nGood pay")).toEqual([
      "Cook wanted\nGood pay",
    ]);
  });
  it("dashes inside a line don't split", () => {
    expect(splitPostings("Back-end --- front-end")).toEqual([
      "Back-end --- front-end",
    ]);
  });
  it("ignores blanks and stray separators", () => {
    expect(splitPostings("---\nCook\n---\n\n---\n")).toEqual(["Cook"]);
    expect(splitPostings("   ")).toEqual([]);
  });
});

describe("postingsToBatch", () => {
  const AD = "Cook wanted\n---\nGood pay";

  it("a separator alone never makes a batch: the ad stays one ad", () => {
    expect(postingsToBatch(AD, false)).toEqual([]);
  });
  it("splits behind the explicit checkbox", () => {
    expect(postingsToBatch(AD, true)).toEqual(["Cook wanted", "Good pay"]);
  });
  it("one posting with the box ticked is still a single application", () => {
    expect(postingsToBatch("Cook wanted", true)).toEqual([]);
    expect(postingsToBatch("---\nCook wanted\n---", true)).toEqual([]);
  });
});
