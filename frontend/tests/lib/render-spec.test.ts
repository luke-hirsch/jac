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

  it("the fallback itself speaks plural", () => {
    expect(FALLBACK_SPEC.cv.sections).toContain("educations");
    expect(FALLBACK_SPEC.cv.sections).not.toContain("education");
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
