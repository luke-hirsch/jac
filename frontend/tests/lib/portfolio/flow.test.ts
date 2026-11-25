import { describe, expect, it } from "vitest";
import {
  DEFAULT_FOCUS,
  DEFAULT_TONE,
  EMPTY_FORM,
  checkedAnswer,
  exploreSearchSchema,
  formToSearch,
  hasAnswer,
  luckySearch,
  needsPersonalise,
  searchToForm,
  splitQuery,
  standardSearch,
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
  const base = {
    domains: [] as string[],
    focus: "balanced",
    tone: "neutral",
    query: "",
  };

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

describe("splitQuery", () => {
  it("keeps the visitor's own words out of what goes in the URL", () => {
    const { url, q } = splitQuery({
      d: ["music"],
      focus: "technical",
      tone: "personal",
      q: "jazz",
    });
    expect(q).toBe("jazz");
    expect(url).toEqual({ d: ["music"], focus: "technical", tone: "personal" });
    expect("q" in url).toBe(false);
  });

  it("leaves an answer without free text as it is", () => {
    expect(splitQuery({ lucky: true })).toEqual({
      url: { lucky: true },
      q: undefined,
    });
  });

  it("still leaves the URL with an answer in it", () => {
    const form = {
      domains: [],
      focus: "balanced",
      tone: "neutral",
      query: "jazz",
    };
    expect(hasAnswer(splitQuery(formToSearch(form)).url)).toBe(true);
  });

  it("keeps the check's payload out of the URL too: it's single use, and a shared link mustn't carry it", () => {
    const { url, q, captcha } = splitQuery({
      d: ["music"],
      focus: "technical",
      tone: "personal",
      q: "jazz",
      captcha: "cGF5bG9hZA==",
    });
    expect(captcha).toBe("cGF5bG9hZA==");
    expect(q).toBe("jazz");
    expect(url).toEqual({ d: ["music"], focus: "technical", tone: "personal" });
    expect("captcha" in url).toBe(false);
  });
});

describe("checkedAnswer", () => {
  const PAYLOAD = "cGF5bG9hZA==";

  it("an answer the AI works on waits for the tick", () => {
    for (const form of [
      { ...EMPTY_FORM, domains: ["music"] },
      { ...EMPTY_FORM, query: "jazz" },
      { ...EMPTY_FORM, tone: "personal" },
    ]) {
      expect(checkedAnswer(form, null)).toBeNull();
    }
  });

  it("once ticked, the answer carries the check's payload", () => {
    const form = { ...EMPTY_FORM, domains: ["music"], query: "jazz" };
    expect(checkedAnswer(form, PAYLOAD)).toEqual({
      ...formToSearch(form),
      captcha: PAYLOAD,
    });
  });

  it("an answer without AI needs no tick and carries none: it is the standard page", () => {
    expect(checkedAnswer(EMPTY_FORM, null)).toEqual(standardSearch());
    expect(checkedAnswer(EMPTY_FORM, PAYLOAD)).toEqual(standardSearch());
  });
});

describe("luckySearch", () => {
  it("is lucky and nothing else", () => {
    expect(luckySearch()).toEqual({ lucky: true });
  });
});

describe("standardSearch", () => {
  it("is the questionnaire answered with nothing picked", () => {
    expect(standardSearch()).toEqual({
      focus: DEFAULT_FOCUS,
      tone: DEFAULT_TONE,
    });
  });

  it("is an answer, so the owner's home shows the page and not the questions", () => {
    expect(hasAnswer(standardSearch())).toBe(true);
  });

  it("is what the backend's standard address says (spa.portfolio.standard_page_url)", () => {
    const params = new URLSearchParams("focus=balanced&tone=neutral");
    expect(exploreSearchSchema.parse(Object.fromEntries(params))).toEqual(
      standardSearch(),
    );
  });
});

describe("needsPersonalise", () => {
  const style = { focus: "technical", tone: "personal" };

  it("is false for the standard page", () => {
    expect(needsPersonalise(standardSearch())).toBe(false);
    expect(needsPersonalise({ ...standardSearch(), d: [] })).toBe(false);
    expect(needsPersonalise({ ...standardSearch(), q: "   " })).toBe(false);
  });

  it("is true for a style other than the default: the welcome is written in it", () => {
    expect(needsPersonalise(style)).toBe(true);
    expect(needsPersonalise({ ...standardSearch(), tone: "formal" })).toBe(
      true,
    );
    expect(needsPersonalise({ ...standardSearch(), focus: "technical" })).toBe(
      true,
    );
  });

  it("is true once a topic is picked or a question written", () => {
    expect(needsPersonalise({ ...style, d: ["music"] })).toBe(true);
    expect(needsPersonalise({ ...style, q: "jazz" })).toBe(true);
  });

  it("is false for a lucky draw, picks or not", () => {
    expect(needsPersonalise({ lucky: true })).toBe(false);
    expect(needsPersonalise({ lucky: true, d: ["music"], q: "jazz" })).toBe(
      false,
    );
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
      search: {
        d: ["music"],
        lucky: false,
        focus: "technical",
        tone: "personal",
      },
    });
  });

  it("never stores the check's payload: a return visit has to tick again", () => {
    expect(
      nativeStamp({
        d: ["music"],
        focus: "technical",
        captcha: "cGF5bG9hZA==",
      }),
    ).toEqual({
      kind: "native",
      search: { d: ["music"], focus: "technical" },
    });
  });

  it("round-trips focus/tone through write/read + the zod schema", () => {
    const storage = memStorage();
    writeStamp(
      "jane",
      nativeStamp({
        d: ["software development"],
        focus: "soft_skill",
        tone: "formal",
        q: "drop me",
      }),
      storage,
    );
    expect(readStamp("jane", storage)).toEqual({
      kind: "native",
      search: {
        d: ["software development"],
        focus: "soft_skill",
        tone: "formal",
      },
    });
  });
});
