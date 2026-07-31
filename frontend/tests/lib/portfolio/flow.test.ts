import { describe, expect, it } from "vitest";
import {
  DEFAULT_FOCUS,
  DEFAULT_TONE,
  formToSearch,
  hasAnswer,
  luckySearch,
  searchToForm,
} from "@/lib/portfolio/questionnaire";
import { nativeStamp, readStamp, writeStamp } from "@/lib/portfolio/stamp";

/**
 * Red-first unit tests for the pure form↔search helpers behind the [frontend]-portfolio-flow
 * rework. No DOM, no network — the routes/components are click-through verified (the tests/
 * regime is node-env pure-lib only, [[frontend-test-layout]]).
 */

// Injectable in-memory storage — the stamp.ts idiom (never stub globals).
function memStorage() {
  const store = new Map<string, string>();
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
  };
}

describe("formToSearch", () => {
  const base = { domains: [] as string[], focus: "balanced", tone: "neutral", query: "" };

  it("always carries focus and tone (so the result URL is never param-empty)", () => {
    const s = formToSearch({ ...base, focus: "technical", tone: "personal" });
    expect(s.focus).toBe("technical");
    expect(s.tone).toBe("personal");
  });

  it("adds d only when domains are chosen", () => {
    expect(formToSearch(base).d).toBeUndefined();
    expect(formToSearch({ ...base, domains: ["music"] }).d).toEqual(["music"]);
  });

  it("trims the query and drops it when blank", () => {
    expect(formToSearch({ ...base, query: "  jazz  " }).q).toBe("jazz");
    expect(formToSearch({ ...base, query: "   " }).q).toBeUndefined();
  });
});

describe("luckySearch", () => {
  it("is lucky and nothing else", () => {
    expect(luckySearch()).toEqual({ lucky: true });
  });
});

describe("hasAnswer", () => {
  it("is false for an empty search (show the questionnaire)", () => {
    expect(hasAnswer({})).toBe(false);
    expect(hasAnswer({ d: [] })).toBe(false);
  });

  it("is true for lucky / focus / tone / domains / query", () => {
    expect(hasAnswer({ lucky: true })).toBe(true);
    expect(hasAnswer({ focus: "balanced" })).toBe(true);
    expect(hasAnswer({ tone: "neutral" })).toBe(true);
    expect(hasAnswer({ d: ["music"] })).toBe(true);
    expect(hasAnswer({ q: "jazz" })).toBe(true);
  });
});

describe("searchToForm", () => {
  it("round-trips a formToSearch result", () => {
    const form = {
      domains: ["music"],
      focus: "technical",
      tone: "personal",
      query: "gigs",
    };
    expect(searchToForm(formToSearch(form))).toEqual(form);
  });

  it("fills defaults when the style axis is absent", () => {
    const f = searchToForm({ d: ["music"] });
    expect(f).toEqual({
      domains: ["music"],
      focus: DEFAULT_FOCUS,
      tone: DEFAULT_TONE,
      query: "",
    });
  });
});

describe("nativeStamp (style preserved)", () => {
  it("keeps focus/tone, still drops the free-text q", () => {
    expect(
      nativeStamp({
        d: ["music"],
        lucky: false,
        focus: "technical",
        tone: "personal",
        q: "drop me",
      }),
    ).toEqual({
      kind: "native",
      search: { d: ["music"], lucky: false, focus: "technical", tone: "personal" },
    });
  });

  it("round-trips focus/tone through write/read + the zod schema", () => {
    const storage = memStorage();
    writeStamp(
      nativeStamp({
        d: ["software development"],
        focus: "soft_skill",
        tone: "formal",
        q: "drop me",
      }),
      storage,
    );
    expect(readStamp(storage)).toEqual({
      kind: "native",
      search: { d: ["software development"], focus: "soft_skill", tone: "formal" },
    });
  });
});
