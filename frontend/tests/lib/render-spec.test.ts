import { describe, it, expect } from "vitest";
import { FALLBACK_SPEC, parseLayoutSpec, templatePath } from "@/lib/render/spec";

/**
 * LayoutSpec parsing (guide [frontend]-render-export): defensive against partial or
 * legacy specs (the shipped default once said "education" — cv_content speaks plural),
 * and template URLs are rewritten to same-origin paths for the /media proxy.
 */

describe("parseLayoutSpec", () => {
  it("falls back wholesale on empty input", () => {
    expect(parseLayoutSpec(undefined)).toEqual(FALLBACK_SPEC);
    expect(parseLayoutSpec(null)).toEqual(FALLBACK_SPEC);
  });

  it("keeps provided values and fills the gaps", () => {
    const spec = parseLayoutSpec({
      cv: { pages: 2 },
      font: { base_pt: 12 },
      colors: { accent: "#000000" },
    });
    expect(spec.cv.pages).toBe(2);
    expect(spec.cv.sections).toEqual(FALLBACK_SPEC.cv.sections);
    expect(spec.font.base_pt).toBe(12);
    expect(spec.font.family).toBe(FALLBACK_SPEC.font.family);
    expect(spec.colors.accent).toBe("#000000");
    expect(spec.colors.muted).toBe(FALLBACK_SPEC.colors.muted);
  });

  it("normalizes the legacy singular 'education' section name", () => {
    const spec = parseLayoutSpec({
      cv: { sections: ["jobs", "education"], sidebar: ["skills"] },
    });
    expect(spec.cv.sections).toEqual(["jobs", "educations"]);
    expect(spec.cv.sidebar).toEqual(["skills"]);
  });

  /**
   * `[frontend]-fit-preflight` Results round 1: a nested array in `sidebar` is the
   * layout saying "these render side by side". Kept in `sidebar` rather than a second
   * field so one list still reads top-to-bottom as the page does.
   */
  it("reads a nested sidebar group as one row of columns", () => {
    const spec = parseLayoutSpec({
      cv: { sidebar: ["skills", ["certifications", "languages"]] },
    });
    expect(spec.cv.sidebar).toEqual([
      "skills",
      ["certifications", "languages"],
    ]);
  });

  it("normalizes legacy names inside a group too", () => {
    const spec = parseLayoutSpec({ cv: { sidebar: [["education", "languages"]] } });
    expect(spec.cv.sidebar).toEqual([["educations", "languages"]]);
  });

  it("the fallback pairs certifications with languages", () => {
    expect(FALLBACK_SPEC.cv.sidebar).toEqual([
      "skills",
      ["certifications", "languages"],
    ]);
  });

  it("the fallback itself speaks plural", () => {
    expect(FALLBACK_SPEC.cv.sections).toContain("educations");
    expect(FALLBACK_SPEC.cv.sections).not.toContain("education");
  });

  it("falls back to the default entry budget when max_entries is missing", () => {
    const spec = parseLayoutSpec({ cv: { pages: 2 } });
    expect(spec.cv.max_entries).toEqual(FALLBACK_SPEC.cv.max_entries);
    expect(FALLBACK_SPEC.cv.max_entries.skills).toBeGreaterThan(0);
  });

  it("keeps a provided entry budget, mapping legacy names and dropping junk", () => {
    const spec = parseLayoutSpec({
      cv: {
        max_entries: {
          skills: 5,
          education: 1, // legacy singular
          jobs: 0, // non-positive → no cap
          projects: "lots" as unknown as number, // junk → no cap
        },
      },
    });
    expect(spec.cv.max_entries).toEqual({ skills: 5, educations: 1 });
  });
});

describe("templatePath", () => {
  it("rewrites an absolute media URL to a same-origin path", () => {
    expect(
      templatePath(
        "http://localhost:8000/media/application_layouts/default_layout.json",
        "http://localhost:5173",
      ),
    ).toBe("/media/application_layouts/default_layout.json");
  });

  it("passes relative paths through", () => {
    expect(templatePath("/media/x.json", "http://localhost:5173")).toBe(
      "/media/x.json",
    );
  });
});
