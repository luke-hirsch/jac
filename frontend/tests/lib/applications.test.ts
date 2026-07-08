import { describe, it, expect } from "vitest";
import {
  toApplicationPayload,
  runToApplicationPatch,
} from "@/lib/queries/applications";
import type { TailoredResult } from "@/lib/queries/generations";

/**
 * Pure helpers behind the applications pages: pasted-posting→payload and the
 * "apply this run" PATCH body (cv JSON + full letter text, nothing else — the
 * user's status/layout choices must survive an apply).
 */

describe("toApplicationPayload", () => {
  it("trims the pasted posting text", () => {
    expect(toApplicationPayload("  we need a dev  ")).toEqual({
      posting_text: "we need a dev",
    });
  });
});

describe("runToApplicationPatch", () => {
  it("maps the run result onto cv_content + cover_letter only", () => {
    const result = {
      meta: { grade: "light", alias: "default" },
      cv: { skills: [{ id: "skill:1", label: "Python", relevance_score: 0.9 }] },
      cover_letter: { text: "Dear team, …", ai_share: 0.1 },
    } as unknown as TailoredResult;

    const patch = runToApplicationPatch(result);
    expect(patch).toEqual({
      cv_content: result.cv,
      cover_letter: "Dear team, …",
    });
  });
});
