import { describe, expect, it } from "vitest";
import {
  moveAttachment,
  withPositions,
  type AttachmentLike,
} from "@/lib/render/attachments";

// Starts RED: @/lib/render/attachments does not exist until [frontend]-cert-attachments lands.
// mergePdfs is impure (fetch + pdf-lib) and verified live, not here.

const items: AttachmentLike[] = [
  { id: 10, position: 0 },
  { id: 20, position: 1 },
  { id: 30, position: 2 },
];

describe("withPositions", () => {
  it("renumbers to contiguous 0-based positions", () => {
    const shuffled: AttachmentLike[] = [
      { id: 30, position: 5 },
      { id: 10, position: 9 },
    ];
    expect(withPositions(shuffled).map((a) => a.position)).toEqual([0, 1]);
  });
});

describe("moveAttachment", () => {
  it("swaps down and renumbers", () => {
    const out = moveAttachment(items, 0, 1);
    expect(out.map((a) => a.id)).toEqual([20, 10, 30]);
    expect(out.map((a) => a.position)).toEqual([0, 1, 2]);
  });

  it("swaps up", () => {
    const out = moveAttachment(items, 2, -1);
    expect(out.map((a) => a.id)).toEqual([10, 30, 20]);
  });

  it("is a no-op at the boundaries and out of range", () => {
    expect(moveAttachment(items, 0, -1)).toBe(items);
    expect(moveAttachment(items, 2, 1)).toBe(items);
    expect(moveAttachment(items, 5, 1)).toBe(items);
  });
});
