import { describe, expect, it } from "vitest";
import {
  REWRITE_STYLES,
  chatPayload,
  parseSseLine,
  seedDiscussion,
  splitRevision,
} from "@/lib/letter-chat";

/**
 * Pure helpers behind the letter refine popover + chat. Chat-assistant-rework: the
 * endpoint streams SSE deltas and requests carry an explicit executor pick again
 * (blank = the user's default executor). No DOM, no network.
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

describe("chat assistant helpers ([fullstack]-chat-assistant-rework)", () => {
  it("chatPayload carries an explicit executor pick and omits it when null", () => {
    const msgs = [{ role: "user" as const, content: "hi" }];
    expect(
      chatPayload("B.", msgs, { provider: "anthropic", model: "claude-sonnet-5" }),
    ).toEqual({
      body: "B.",
      messages: msgs,
      provider: "anthropic",
      model: "claude-sonnet-5",
    });
    const bare = chatPayload("B.", msgs);
    expect("provider" in bare).toBe(false);
    expect("model" in bare).toBe(false);
  });

  it("parseSseLine reads data lines and skips everything else", () => {
    expect(parseSseLine('data: {"delta": "He"}')).toEqual({ delta: "He" });
    expect(parseSseLine('data: {"done": true}')).toEqual({ done: true });
    expect(parseSseLine("")).toBeNull();
    expect(parseSseLine(": keepalive")).toBeNull();
    expect(parseSseLine("data: not json")).toBeNull();
  });

  it("splitRevision splits on a line-anchored marker, same-line content accepted", () => {
    expect(splitRevision("Sure.\nREVISED BODY:\nNew body.")).toEqual({
      reply: "Sure.",
      revision: "New body.",
    });
    expect(splitRevision("Sure.\nREVISED BODY: New body.")).toEqual({
      reply: "Sure.",
      revision: "New body.",
    });
  });

  it("no marker / mid-line mention / empty revision", () => {
    expect(splitRevision("Just advice.")).toEqual({
      reply: "Just advice.",
      revision: null,
    });
    expect(
      splitRevision("I would end with REVISED BODY: only when proposing."),
    ).toEqual({
      reply: "I would end with REVISED BODY: only when proposing.",
      revision: null,
    });
    expect(splitRevision("Sure.\nREVISED BODY:\n")).toEqual({
      reply: "Sure.",
      revision: null,
    });
  });
});
