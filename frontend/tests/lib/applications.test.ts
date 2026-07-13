import { describe, it, expect } from "vitest";
import {
  toApplicationPayload,
  runToApplicationPatch,
} from "@/lib/queries/applications";
import type { TailoredResult } from "@/lib/queries/generations";

/**
 * Pure helpers behind the applications pages: pasted-posting→payload and the
 * "apply this run" PATCH body (cv JSON + body-only letter + letter_meta furniture,
 * nothing else — the user's status/layout choices must survive an apply).
 */

describe("toApplicationPayload", () => {
  it("trims the pasted posting text", () => {
    expect(toApplicationPayload("  we need a dev  ")).toEqual({
      posting_text: "we need a dev",
    });
  });
});

describe("runToApplicationPatch", () => {
  it("maps the run result onto cv_content + body-only letter + letter_meta", () => {
    const result = {
      meta: { grade: "light", alias: "default" },
      cv: { skills: [{ id: "skill:1", label: "Python", relevance_score: 0.9 }] },
      cover_letter: {
        language: "en",
        subject: "Application for Dev",
        salutation: "Dear team,",
        body: "I build things.",
        sender: { name: "Ada" },
        recipient: { company: "ACME" },
        date: "2026-07-09",
        closing: "Kind regards,",
        personal_paragraph: "I admire ACME.",
        text: "…full furnished text (not what gets stored)…",
        ai_share: 0.1,
      },
    } as unknown as TailoredResult;

    const patch = runToApplicationPatch(result);
    expect(patch).toEqual({
      cv_content: result.cv,
      // The editable body: personal paragraph first (it opens the letter), then the
      // letter body — never the furnished text.
      cover_letter: "I admire ACME.\n\nI build things.",
      letter_meta: {
        language: "en",
        subject: "Application for Dev",
        salutation: "Dear team,",
        date: "2026-07-09",
        closing: "Kind regards,",
        sender: { name: "Ada" },
        recipient: { company: "ACME" },
      },
    });
  });
});
