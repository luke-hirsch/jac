import { describe, it, expect } from "vitest";
import {
  toPayload,
  groundingBadge,
  knobParams,
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
 * WS-event→state contracts the page relies on. Executor era
 * ([frontend]-model-first-generate-panel): a run is `{mode, provider, model}`
 * against a JobApplication pk; grade/alias are dead vocabulary.
 */

function form(over: Partial<GenerationForm> = {}): GenerationForm {
  return {
    job_application: 7,
    mode: "standard",
    provider: "",
    model: "",
    ...over,
  };
}

const EMPTY: RunState = { status: "pending", stage: "", result: null, error: "" };
const RESULT = {
  meta: { mode: "standard", provider: "ollama", model: "" },
  cv: {},
  cover_letter: {},
} as unknown as TailoredResult;

describe("toPayload", () => {
  it("omits every blank field — the server owns those defaults (mode always travels)", () => {
    expect(toPayload(form())).toEqual({ job_application: 7, mode: "standard" });
  });

  it("passes an explicit executor pick through verbatim", () => {
    expect(
      toPayload(
        form({ mode: "high", provider: "anthropic", model: "claude-sonnet-5" }),
      ),
    ).toEqual({
      job_application: 7,
      mode: "high",
      provider: "anthropic",
      model: "claude-sonnet-5",
    });
  });

  it("omits a blank model (HirschAI pick — the tower model is server-fixed)", () => {
    const p = toPayload(form({ mode: "standard", provider: "ollama" }));
    expect(p).toEqual({ job_application: 7, mode: "standard", provider: "ollama" });
    expect("model" in p).toBe(false);
  });
});

describe("result meta / cv row shapes (compile-time contract)", () => {
  it("meta speaks mode/provider/model; cv rows may carry pinned + warning", () => {
    const r: TailoredResult = {
      meta: { mode: "high", provider: "anthropic", model: "claude-sonnet-5" },
      cv: {
        jobs: [
          {
            id: "job:1",
            label: "x",
            relevance_score: null,
            pinned: true,
            warning: "pinned but filtered by scope",
          },
        ],
      },
      cover_letter: RESULT.cover_letter,
    };
    expect(r.cv.jobs[0].pinned).toBe(true);
    expect(r.cv.jobs[0].warning).toContain("pinned");
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
  it("marks a repaired-clean letter", () => {
    const b = groundingBadge({ count: 0, claims: [], repaired: true });
    expect(b.tone).toBe("green");
    expect(b.label).toBe("grounded · repaired");
  });
  it("marks surviving claims after a repair pass", () => {
    const b = groundingBadge({ count: 2, claims: ["a", "b"], repaired: true });
    expect(b.tone).toBe("amber");
    expect(b.label).toBe("2 claims · repaired");
  });
  it("a failed repair attempt adds no suffix", () => {
    // repaired=false means the rewrite never replaced the body — same badge as before.
    expect(
      groundingBadge({ count: 1, claims: ["a"], repaired: false }).label,
    ).toBe("1 claim");
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

/**
 * [fullstack]-model-knobs — per-run knobs (effort/temperature) travel as a
 * `params` object; blanks never travel.
 */
describe("model knobs ([fullstack]-model-knobs)", () => {
  it("builds params from the panel's knob state, omitting blanks", () => {
    expect(knobParams({})).toEqual({});
    expect(knobParams({ effort: "high" })).toEqual({ effort: "high" });
    expect(knobParams({ temperature: "0.3" })).toEqual({ temperature: 0.3 });
  });

  it("drops garbage temperature input — the server owns validation, not NaN", () => {
    expect(knobParams({ temperature: "warm" })).toEqual({});
    expect(knobParams({ temperature: "  " })).toEqual({});
  });

  it("toPayload carries non-empty params and omits empty ones", () => {
    const withKnobs = toPayload({
      ...form({ mode: "high", provider: "anthropic", model: "claude-sonnet-5" }),
      params: { effort: "high" },
    } as unknown as GenerationForm);
    expect((withKnobs as { params?: object }).params).toEqual({ effort: "high" });

    const bare = toPayload({
      ...form(),
      params: {},
    } as unknown as GenerationForm);
    expect("params" in bare).toBe(false);
  });
});
