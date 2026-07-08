import { describe, it, expect } from "vitest";
import {
  toPayload,
  aiShareBadge,
  groundingBadge,
  isStalePending,
  pendingAgeSeconds,
  runReducer,
  STALE_PENDING_AFTER_S,
  type GenerationForm,
  type RunState,
  type TailoredResult,
} from "@/lib/queries/generations";

/**
 * Unit tests for the pure helpers behind the application detail page.
 * No DOM, no network, no WebSocket — just the form→payload, badge, and
 * WS-event→state contracts the page relies on. Application-centric: a run
 * is created against a JobApplication pk, not raw posting text.
 */

function form(over: Partial<GenerationForm> = {}): GenerationForm {
  return {
    job_application: 7,
    grade: "",
    alias: "default",
    verify_grounding: false,
    personal_paragraph: false,
    ...over,
  };
}

const EMPTY: RunState = { status: "pending", stage: "", result: null, error: "" };
const RESULT = { meta: { grade: "light", alias: "default" }, cv: {}, cover_letter: {} } as unknown as TailoredResult;

describe("toPayload", () => {
  it("passes the application pk and omits grade when blank (server auto-detects)", () => {
    const p = toPayload(form());
    expect(p.job_application).toBe(7);
    expect("grade" in p).toBe(false);
  });

  it("includes grade when set", () => {
    expect(toPayload(form({ grade: "strong" })).grade).toBe("strong");
  });
});

describe("aiShareBadge", () => {
  it("is green at or below 25%", () => {
    expect(aiShareBadge(0.1).tone).toBe("green");
    expect(aiShareBadge(0.25).tone).toBe("green");
  });
  it("is amber above 25%", () => {
    expect(aiShareBadge(0.4).tone).toBe("amber");
  });
  it("labels a rounded percentage", () => {
    expect(aiShareBadge(0.37).label).toBe("37% AI");
  });
});

describe("groundingBadge", () => {
  it("muted when not checked (null)", () => {
    expect(groundingBadge({ count: null, claims: [] }).tone).toBe("muted");
  });
  it("green when fully grounded (0)", () => {
    expect(groundingBadge({ count: 0, claims: [] }).tone).toBe("green");
  });
  it("amber with a pluralised count", () => {
    expect(groundingBadge({ count: 1, claims: ["a"] }).label).toBe("1 claim");
    expect(groundingBadge({ count: 3, claims: ["a", "b", "c"] }).label).toBe("3 claims");
  });
});

describe("runReducer", () => {
  it("snapshot seeds the whole state", () => {
    const s = runReducer(EMPTY, {
      event: "snapshot", status: "running", stage: "writing letter", result: null, error: "",
    });
    expect(s.status).toBe("running");
    expect(s.stage).toBe("writing letter");
  });

  it("progress updates status + stage only", () => {
    const s = runReducer(EMPTY, { event: "progress", status: "running", stage: "filtering CV" });
    expect(s.stage).toBe("filtering CV");
    expect(s.result).toBeNull();
  });

  it("done attaches the result", () => {
    const s = runReducer(EMPTY, { event: "done", status: "done", result: RESULT });
    expect(s.status).toBe("done");
    expect(s.result).toBe(RESULT);
  });

  it("failed records the error", () => {
    const s = runReducer(EMPTY, { event: "failed", status: "failed", error: "boom" });
    expect(s.status).toBe("failed");
    expect(s.error).toBe("boom");
  });
});

describe("pendingAgeSeconds", () => {
  const now = new Date("2026-07-09T12:00:00Z");

  it("counts whole seconds since creation", () => {
    expect(pendingAgeSeconds("2026-07-09T11:59:15Z", now)).toBe(45);
  });
  it("clamps future timestamps and garbage to 0", () => {
    expect(pendingAgeSeconds("2026-07-09T12:00:05Z", now)).toBe(0);
    expect(pendingAgeSeconds("not a date", now)).toBe(0);
  });
});

describe("isStalePending", () => {
  const now = new Date("2026-07-09T12:00:00Z");
  const oldEnough = new Date(
    now.getTime() - (STALE_PENDING_AFTER_S + 5) * 1000,
  ).toISOString();
  const fresh = new Date(now.getTime() - 2000).toISOString();

  it("flags a pending run older than the threshold (worker likely down)", () => {
    expect(isStalePending("pending", oldEnough, now)).toBe(true);
  });
  it("never flags fresh pending runs or runs that made it past pending", () => {
    expect(isStalePending("pending", fresh, now)).toBe(false);
    expect(isStalePending("running", oldEnough, now)).toBe(false);
    expect(isStalePending("done", oldEnough, now)).toBe(false);
    expect(isStalePending("failed", oldEnough, now)).toBe(false);
  });
});
