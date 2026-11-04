import { describe, expect, it } from "vitest";
import { moveId } from "@/lib/render/attachments";

// mergePdfs is impure (fetch + pdf-lib) and verified live, not here.
// The application's `attachments` field is an ordered id list; moveId reorders it.

const ids = [10, 20, 30];

describe("moveId", () => {
  it("swaps down", () => {
    expect(moveId(ids, 0, 1)).toEqual([20, 10, 30]);
  });

  it("swaps up", () => {
    expect(moveId(ids, 2, -1)).toEqual([10, 30, 20]);
  });

  it("does not mutate the input", () => {
    moveId(ids, 0, 1);
    expect(ids).toEqual([10, 20, 30]);
  });

  it("is a no-op (same reference) at the boundaries and out of range", () => {
    expect(moveId(ids, 0, -1)).toBe(ids);
    expect(moveId(ids, 2, 1)).toBe(ids);
    expect(moveId(ids, 5, 1)).toBe(ids);
  });
});
