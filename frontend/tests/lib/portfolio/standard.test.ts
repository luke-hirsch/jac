import { describe, expect, it } from "vitest";
import {
  isStandardEmpty,
  standardBody,
  standardChanged,
  standardDraft,
} from "@/lib/portfolio/standard-page";
import type { StandardPageRow } from "@/lib/queries/portfolio";

/**
 * The standard-page card's form ↔ the PATCH body of /api/spa/portfolio/manage/standard/. The card
 * itself is click-through verified ([[frontend-test-layout]]).
 */

const row = (over: Partial<StandardPageRow> = {}): StandardPageRow => ({
  title: "",
  intro: "",
  content: { featured: [] },
  url: "https://portfolio.example.com/jane/?focus=balanced&tone=neutral",
  updated_at: null,
  ...over,
});

describe("standardDraft", () => {
  it("reads the saved page into the form", () => {
    expect(
      standardDraft(
        row({
          title: "Hi",
          intro: "I build things.",
          content: { featured: ["job:1", "block:2"] },
        }),
      ),
    ).toEqual({
      title: "Hi",
      intro: "I build things.",
      featured: ["job:1", "block:2"],
    });
  });

  it("reads the page the server answers before the first save as empty", () => {
    expect(standardDraft(row())).toEqual({
      title: "",
      intro: "",
      featured: [],
    });
  });
});

describe("standardBody", () => {
  it("is the whole page, trimmed, and nothing a link has", () => {
    expect(
      standardBody({
        title: "  Hi ",
        intro: " I build things.\n",
        featured: ["block:2", "job:1"],
      }),
    ).toEqual({
      title: "Hi",
      intro: "I build things.",
      content: { featured: ["block:2", "job:1"] },
    });
  });
});

describe("isStandardEmpty", () => {
  const empty = { title: "", intro: "", featured: [] };

  it("is true with nothing of the owner's own, blanks included", () => {
    expect(isStandardEmpty(empty)).toBe(true);
    expect(isStandardEmpty({ ...empty, title: "   ", intro: "\n" })).toBe(true);
  });

  it("is false once any part is set", () => {
    expect(isStandardEmpty({ ...empty, title: "Hi" })).toBe(false);
    expect(isStandardEmpty({ ...empty, intro: "Hello" })).toBe(false);
    expect(isStandardEmpty({ ...empty, featured: ["job:1"] })).toBe(false);
  });
});

describe("standardChanged", () => {
  const saved = row({
    title: "Hi",
    intro: "Hello",
    content: { featured: ["job:1", "job:2"] },
  });

  it("is false for the form as it was loaded", () => {
    expect(standardChanged(saved, standardDraft(saved))).toBe(false);
  });

  it("ignores what the server trims anyway", () => {
    const draft = { ...standardDraft(saved), title: " Hi  " };
    expect(standardChanged(saved, draft)).toBe(false);
  });

  it("sees an edit to any part", () => {
    const draft = standardDraft(saved);
    expect(standardChanged(saved, { ...draft, title: "Hey" })).toBe(true);
    expect(standardChanged(saved, { ...draft, intro: "" })).toBe(true);
    expect(standardChanged(saved, { ...draft, featured: ["job:1"] })).toBe(
      true,
    );
  });

  it("sees a new order: it's the order visitors see", () => {
    const draft = { ...standardDraft(saved), featured: ["job:2", "job:1"] };
    expect(standardChanged(saved, draft)).toBe(true);
  });
});
