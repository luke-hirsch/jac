import { describe, it, expect } from "vitest";
import { ApiError } from "@/lib/api";
import { drfFieldError, shouldSend } from "@/lib/field-save";

/**
 * Pure logic behind line-by-line saving (per-field PATCH in the CRUD editors):
 * DRF error → the message for exactly the failed field, and the gate deciding
 * whether a blur is worth a request at all.
 */

describe("drfFieldError", () => {
  it("returns the field's own message first", () => {
    const err = new ApiError(400, {
      started: ["Date has wrong format."],
      non_field_errors: ["something else"],
    });
    expect(drfFieldError(err, "started")).toBe("Date has wrong format.");
  });

  it("falls back to non_field_errors, then detail", () => {
    expect(
      drfFieldError(new ApiError(400, { non_field_errors: ["nope"] }), "title"),
    ).toBe("nope");
    expect(
      drfFieldError(new ApiError(403, { detail: "Forbidden." }), "title"),
    ).toBe("Forbidden.");
  });

  it("never returns empty — generic message for unknown shapes", () => {
    expect(drfFieldError(new Error("boom"), "title")).toBe("Save failed");
    expect(drfFieldError(new ApiError(500, "html error page"), "title")).toBe(
      "Save failed",
    );
  });
});

describe("shouldSend", () => {
  it("skips unchanged values (blur fires on every tab-through)", () => {
    expect(shouldSend("Dev", "Dev", [])).toBe(false);
    expect(shouldSend([1, 2], [1, 2], [])).toBe(false);
  });

  it("sends changed values, including null-ing a field", () => {
    expect(shouldSend("Senior Dev", "Dev", [])).toBe(true);
    expect(shouldSend(null, "2024-01-01", [])).toBe(true);
  });

  it("never sends a value the client-side validator rejects", () => {
    expect(shouldSend("not-a-date", "", ["YYYY-MM-DD"])).toBe(false);
  });
});
