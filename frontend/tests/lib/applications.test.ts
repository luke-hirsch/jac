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
  it("maps the run result onto cv_content + body-only letter + letter_meta + pins", () => {
    const result = {
      meta: { mode: "standard", provider: "ollama", model: "" },
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
        text: "…full furnished text (not what gets stored)…",
      },
    } as unknown as TailoredResult;

    const patch = runToApplicationPatch(result);
    expect(patch).toEqual({
      cv_content: result.cv,
      // The editable body is the letter body verbatim — never the furnished `text`.
      // Any personal paragraph is already woven into `body` by the backend.
      cover_letter: "I build things.",
      letter_meta: {
        language: "en",
        subject: "Application for Dev",
        salutation: "Dear team,",
        date: "2026-07-09",
        closing: "Kind regards,",
        sender: { name: "Ada" },
        recipient: { company: "ACME" },
      },
      // No entry carried a pin, so nothing is force-kept for the next run.
      pinned_entries: [],
    });
  });

  it("carries pinned entries from the current cv_content into the new result", () => {
    const result = {
      meta: { grade: "light", alias: "default" },
      cv: { jobs: [{ id: "job:1", label: "kept", relevance_score: 0.8 }] },
      cover_letter: {
        language: "en",
        subject: "s",
        salutation: "Dear,",
        body: "b",
        sender: {},
        recipient: {},
        date: "2026-07-13",
        closing: "Regards,",
        personal_paragraph: "",
        text: "…",
        ai_share: 0,
      },
    } as unknown as TailoredResult;
    const current = {
      jobs: [
        { id: "job:1", label: "old", relevance_score: 0.5, pinned: true },
        { id: "job:2", label: "unpinned, dropped", relevance_score: 0.4 },
      ],
      skills: [{ id: "skill:9", label: "Rust", relevance_score: null, pinned: true }],
    };

    const patch = runToApplicationPatch(result, current);
    expect(patch.cv_content).toEqual({
      jobs: [{ id: "job:1", label: "kept", relevance_score: 0.8, pinned: true }],
      skills: [
        { id: "skill:9", label: "Rust", relevance_score: null, pinned: true },
      ],
    });
  });
});

/**
 * [frontend]-entry-pins-ui — unskip as that guide's step 0: the apply PATCH must
 * carry the pin set the merged document implies, or the next run force-keeps
 * nothing server-side.
 */
describe.skip("pin sync on apply ([frontend]-entry-pins-ui)", () => {
  it("runToApplicationPatch derives pinned_entries from the merged doc", () => {
    const result = {
      meta: { mode: "standard", provider: "ollama", model: "" },
      cv: {
        jobs: [{ id: "job:1", label: "kept", relevance_score: 0.9 }],
      },
      cover_letter: {
        language: "en",
        subject: "s",
        salutation: "Dear",
        body: "Body.",
        sender: {},
        recipient: {},
        date: "2026-07-18",
        closing: "Best,",
        personal_paragraph: "",
        personal_paragraph_is_stub: false,
      },
    } as unknown as Parameters<typeof runToApplicationPatch>[0];
    const current = {
      jobs: [{ id: "job:99", label: "mine", relevance_score: null, pinned: true }],
    };
    const patch = runToApplicationPatch(result, current) as {
      pinned_entries?: string[];
    };
    expect(patch.pinned_entries).toEqual(["job:99"]);
  });

  it("no pins → an empty pin set travels (clears stale server pins)", () => {
    const result = {
      meta: { mode: "standard", provider: "ollama", model: "" },
      cv: { jobs: [{ id: "job:1", label: "kept", relevance_score: 0.9 }] },
      cover_letter: {
        language: "en",
        subject: "",
        salutation: "",
        body: "Body.",
        sender: {},
        recipient: {},
        date: "",
        closing: "",
        personal_paragraph: "",
        personal_paragraph_is_stub: false,
      },
    } as unknown as Parameters<typeof runToApplicationPatch>[0];
    const patch = runToApplicationPatch(result) as { pinned_entries?: string[] };
    expect(patch.pinned_entries).toEqual([]);
  });
});
