import { describe, it, expect } from "vitest";
import {
  MAX_ANSWER_LEN,
  answeredCount,
  answersDirty,
  cleanAnswers,
  dossierState,
  overlongAnswers,
  personalityHint,
  type PersonalityRow,
} from "@/lib/queries/personality";

/**
 * Unit tests for the pure helpers behind the personality questionnaire page and the
 * generate-panel stub hint (`[frontend]-personality-questionnaire`). No DOM, no
 * network — per the frontend test layout the page component stays untested until
 * styling settles.
 */

function row(over: Partial<PersonalityRow> = {}): PersonalityRow {
  return {
    id: 1,
    answers: {},
    dossier: "",
    questions: [{ id: "flow", prompt: "What do you lose track of time doing?" }],
    answers_updated_at: null,
    dossier_built_at: null,
    updated_at: "2026-07-12T00:00:00Z",
    ...over,
  };
}

describe("cleanAnswers", () => {
  it("trims and drops blank answers (mirror of the serializer)", () => {
    expect(
      cleanAnswers({ flow: "  code  ", admire: "   ", childhood: "" }),
    ).toEqual({ flow: "code" });
  });
});

describe("answeredCount", () => {
  it("counts only non-blank answers", () => {
    expect(answeredCount({ a: "x", b: "  ", c: "" })).toBe(1);
    expect(answeredCount({})).toBe(0);
  });
});

describe("overlongAnswers", () => {
  it("flags only answers beyond the cap — exactly at the cap is fine", () => {
    const at = "x".repeat(MAX_ANSWER_LEN);
    const over = "x".repeat(MAX_ANSWER_LEN + 1);
    expect(overlongAnswers({ a: at, b: over })).toEqual(["b"]);
  });
});

describe("answersDirty", () => {
  it("ignores blank-only edits (the server would drop them anyway)", () => {
    expect(answersDirty({ flow: "code" }, { flow: "code", admire: "  " })).toBe(
      false,
    );
  });
  it("sees changed, added, and cleared answers", () => {
    expect(answersDirty({ flow: "code" }, { flow: "music" })).toBe(true);
    expect(answersDirty({}, { flow: "code" })).toBe(true);
    expect(answersDirty({ flow: "code" }, { flow: "" })).toBe(true);
  });
});

describe("dossierState", () => {
  it("none without any answers — nothing to distil", () => {
    expect(dossierState(row())).toBe("none");
    expect(dossierState(row({ dossier: "leftover text" }))).toBe("none");
  });
  it("stale when answers exist but the dossier is missing or outdated", () => {
    expect(dossierState(row({ answers: { flow: "code" } }))).toBe("stale");
    expect(
      dossierState(
        row({
          answers: { flow: "code" },
          dossier: "d",
          dossier_built_at: "2026-07-01T00:00:00Z",
          answers_updated_at: "2026-07-02T00:00:00Z",
        }),
      ),
    ).toBe("stale");
  });
  it("fresh when the dossier postdates the answers", () => {
    expect(
      dossierState(
        row({
          answers: { flow: "code" },
          dossier: "d",
          answers_updated_at: "2026-07-01T00:00:00Z",
          dossier_built_at: "2026-07-02T00:00:00Z",
        }),
      ),
    ).toBe("fresh");
  });
});

describe("personalityHint", () => {
  it("silent when unticked or while the row is still loading", () => {
    expect(personalityHint(false, row())).toBeNull();
    expect(personalityHint(true, undefined)).toBeNull();
  });
  it("nags when ticked with zero answers — a stub is guaranteed", () => {
    const hint = personalityHint(true, row());
    expect(hint).toMatch(/stub/i);
    expect(hint).toMatch(/questionnaire/i);
  });
  it("silent once any answer exists", () => {
    expect(personalityHint(true, row({ answers: { flow: "code" } }))).toBeNull();
  });
});
