import { describe, expect, it } from "vitest";
import { hasOwnName } from "@/lib/queries/profile";

const BLANK = { display_name: "", first_name: "", last_name: "" };

describe("hasOwnName", () => {
  it("is false when the header would fall back to the username", () => {
    expect(hasOwnName(BLANK)).toBe(false);
    expect(hasOwnName({ ...BLANK, display_name: "   " })).toBe(false);
  });

  it("takes a display name or a first or last name, like the letter's sender does", () => {
    expect(hasOwnName({ ...BLANK, display_name: "Alex" })).toBe(true);
    expect(hasOwnName({ ...BLANK, first_name: "Alex" })).toBe(true);
    expect(hasOwnName({ ...BLANK, last_name: "Muster" })).toBe(true);
  });
});
