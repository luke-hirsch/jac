import { describe, expect, it } from "vitest";
import { consentGiven } from "@/lib/queries/consent";

/** The consent card shows once: until the user agreed, or saved something on an agreement. */
describe("consentGiven", () => {
  it("is false with nothing agreed and nothing saved", () => {
    expect(consentGiven(null, [])).toBe(false);
    expect(consentGiven(undefined, [null, undefined])).toBe(false);
  });

  it("is true once the consent is kept", () => {
    expect(consentGiven("2026-11-21T16:00:00Z", [])).toBe(true);
  });

  it("is true when a saved key or token carries a stamp, even before the kept one loads", () => {
    expect(consentGiven(undefined, [null, "2026-11-20T09:00:00Z"])).toBe(true);
  });
});
