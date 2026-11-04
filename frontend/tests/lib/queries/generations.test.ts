import { describe, it, expect } from "vitest";
import { registerBadge } from "@/lib/queries/generations";

/**
 * [fullstack]-letter-register-de: the German form-of-address flag next to the
 * grounding badge. The server only ever flags a slipped pronoun, so the badge is
 * the whole surface of that check.
 */

describe("registerBadge", () => {
  it("says nothing for English letters and pre-register runs", () => {
    expect(registerBadge(undefined)).toBeNull();
    expect(registerBadge({ form: "", leaks: [] })).toBeNull();
  });

  it("names the form quietly when the letter is clean", () => {
    expect(registerBadge({ form: "ihr", leaks: [] })).toEqual({
      tone: "muted",
      label: '"ihr" form',
    });
    expect(registerBadge({ form: "sie", leaks: [] })?.label).toBe('"Sie" form');
  });

  it("flags the contradicting pronouns in amber, in order", () => {
    expect(registerBadge({ form: "ihr", leaks: ["Sie", "Ihnen"] })).toEqual({
      tone: "amber",
      label: 'wrong "you": Sie, Ihnen',
    });
  });
});
