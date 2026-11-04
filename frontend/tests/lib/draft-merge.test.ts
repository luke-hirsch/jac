import { describe, expect, it } from "vitest";
import { reseedDrafts, reseedField, storedText } from "@/lib/draft-merge";

/**
 * The application editor's drafts must never lose unsaved typing to a server refetch: the
 * old all-or-nothing re-seed wiped every draft when any compared field changed.
 */

describe("reseedField", () => {
  it("keeps the draft while the server copy has not moved", () => {
    expect(reseedField("a", "a", "a")).toBe("keep");
    expect(reseedField("a", "a", "typed")).toBe("keep");
  });

  it("follows the server when the draft has no edits", () => {
    expect(reseedField("a", "b", "a")).toBe("take");
  });

  it("calls it a conflict when both moved apart, and never takes", () => {
    expect(reseedField("a", "b", "typed")).toBe("conflict");
  });

  it("has nothing to do when both moved to the same value", () => {
    expect(reseedField("a", "b", "b")).toBe("keep");
  });
});

describe("reseedDrafts", () => {
  const server = { cover: "old", cv: "{}" };

  it("re-seeds per field: an edited letter survives a CV change beside it", () => {
    const out = reseedDrafts(
      server,
      { cover: "old", cv: '{"jobs":[]}' },
      { cover: "typed", cv: "{}" },
      null,
    );
    expect(out).toEqual({ take: ["cv"], conflicts: [] });
  });

  it("keeps what was typed while a run's auto-fill landed, and reports it", () => {
    const out = reseedDrafts(
      { cover: "", cv: "{}" },
      { cover: "run letter", cv: '{"jobs":[]}' },
      { cover: "typed during the wait", cv: "{}" },
      null,
    );
    expect(out).toEqual({ take: ["cv"], conflicts: ["cover"] });
  });

  it("keeps keystrokes made between a Save and its refetch, without a false alarm", () => {
    const saved = { cover: "saved", cv: "{}" };
    const out = reseedDrafts(
      server,
      saved,
      { cover: "saved and more", cv: "{}" },
      saved,
    );
    expect(out).toEqual({ take: [], conflicts: [] });
  });

  it("is quiet about the echo of a Save with no typing since", () => {
    const saved = { cover: "saved", cv: "{}" };
    expect(reseedDrafts(server, saved, saved, saved)).toEqual({
      take: [],
      conflicts: [],
    });
  });

  it("follows a server that normalised the echo, so Save can clear dirty", () => {
    const sent = { cover: "saved", cv: '{"b":1,"a":2}' };
    const out = reseedDrafts(
      server,
      { cover: "saved", cv: '{"a":2,"b":1}' },
      sent,
      sent,
    );
    expect(out).toEqual({ take: ["cv"], conflicts: [] });
  });

  it("does not revert a draft mid-save because another field moved", () => {
    // Save in flight (draft = sent, server still old) while the sender fill moves `meta`.
    const out = reseedDrafts(
      { cover: "old", meta: "unfilled" },
      { cover: "old", meta: "filled" },
      { cover: "sending", meta: "unfilled" },
      { cover: "sending", meta: "unfilled" },
    );
    expect(out.take).toEqual(["meta"]);
  });

  it("treats a stale `sent` as no protection once the draft moved on", () => {
    const out = reseedDrafts(
      { cover: "saved" },
      { cover: "applied run" },
      { cover: "typed since" },
      { cover: "saved" },
    );
    expect(out).toEqual({ take: [], conflicts: ["cover"] });
  });
});

describe("storedText", () => {
  it("is the form the server keeps, so end whitespace isn't an edit", () => {
    expect(storedText("Dear team,\n\nHello.\n")).toBe(
      storedText("Dear team,\n\nHello."),
    );
    expect(storedText("  a\nb  ")).toBe("a\nb");
  });
});
