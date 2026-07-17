import { describe, expect, it } from "vitest";
import {
  REWRITE_STYLES,
  chatPayload,
  seedDiscussion,
} from "@/lib/letter-chat";

/**
 * Pure helpers behind the letter refine popover + chat. Executor era
 * ([frontend]-model-first-generate-panel): the alias strength gate died with the
 * rework — any executor chats, requests carry no model pick (blank = the user's
 * default executor; guide 6 gives chat its executor picker back). No DOM, no
 * network.
 */

describe("REWRITE_STYLES", () => {
  it("offers exactly the three preset styles", () => {
    expect(REWRITE_STYLES).toHaveLength(3);
    expect(REWRITE_STYLES.map((s) => s.key)).toEqual([
      "shorter",
      "formal",
      "natural",
    ]);
  });
  it("each style carries a label and a non-empty instruction", () => {
    for (const s of REWRITE_STYLES) {
      expect(s.label.length).toBeGreaterThan(0);
      expect(s.instruction.length).toBeGreaterThan(10);
    }
  });
});

describe("seedDiscussion", () => {
  it("wraps the highlighted passage into an opening user message", () => {
    const m = seedDiscussion("I shipped the billing service.");
    expect(m.role).toBe("user");
    expect(m.content).toContain("I shipped the billing service.");
  });
});

describe("chatPayload", () => {
  it("pins the request contract: draft body + transcript, no model pick", () => {
    const messages = [{ role: "user" as const, content: "Make it warmer." }];
    const p = chatPayload("Body.", messages);
    expect(p).toEqual({ body: "Body.", messages });
    expect("alias" in p).toBe(false);
  });
});
