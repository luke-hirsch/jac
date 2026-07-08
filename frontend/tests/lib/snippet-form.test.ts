import { describe, expect, it } from "vitest";
import {
  SNIPPET_KINDS,
  snippetSchema,
  emptySnippetInput,
  snippetToInput,
  toSnippetPayload,
  type SnippetInput,
} from "@/lib/snippet-form";
import type { ResumeSnippetRow } from "@/lib/queries/jac";

/**
 * Red-first unit tests for the pure form<->payload helpers in `snippet-form.ts`
 * (the cover-letter snippets CRUD tab). No DOM, no network — just the
 * kind list, blank/seed state, payload assembly, and schema contract that the
 * `/cv/snippets` route relies on. See guide `[frontend]-cv-snippets.md`.
 */

const KIND_VALUES = [
  "intro",
  "achievement",
  "value_statement",
  "closing",
  "other",
] as const;

function makeRow(over: Partial<ResumeSnippetRow> = {}): ResumeSnippetRow {
  return {
    id: 7,
    title: "Opened a new market",
    content: "Grew the DACH pipeline from zero.",
    kind: "achievement",
    domains: [1, 2],
    skills: [3, 4],
    job: 5,
    project: 6,
    language: "de",
    is_active: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    ...over,
  };
}

describe("SNIPPET_KINDS", () => {
  it("covers exactly the five backend ResumeSnippet.Kind choices", () => {
    expect(SNIPPET_KINDS.map((k) => k.value).sort()).toEqual(
      [...KIND_VALUES].sort(),
    );
  });

  it("gives every kind a human label", () => {
    for (const k of SNIPPET_KINDS) {
      expect(typeof k.label).toBe("string");
      expect(k.label.length).toBeGreaterThan(0);
    }
  });
});

describe("emptySnippetInput", () => {
  it("returns blank defaults (other/en/active, no links)", () => {
    expect(emptySnippetInput()).toEqual({
      title: "",
      content: "",
      kind: "other",
      language: "en",
      domains: [],
      skills: [],
      job: null,
      project: null,
      is_active: true,
    });
  });

  it("returns fresh arrays each call (no shared references)", () => {
    const a = emptySnippetInput();
    const b = emptySnippetInput();
    a.domains.push(99);
    a.skills.push(99);
    expect(b.domains).toEqual([]);
    expect(b.skills).toEqual([]);
  });
});

describe("snippetToInput", () => {
  it("mirrors emptySnippetInput when the row is null", () => {
    expect(snippetToInput(null)).toEqual(emptySnippetInput());
  });

  it("copies every field from an existing row", () => {
    const row = makeRow();
    expect(snippetToInput(row)).toEqual({
      title: "Opened a new market",
      content: "Grew the DACH pipeline from zero.",
      kind: "achievement",
      domains: [1, 2],
      skills: [3, 4],
      job: 5,
      project: 6,
      language: "de",
      is_active: false,
    });
  });

  it("copies array links rather than aliasing the row's arrays", () => {
    const row = makeRow();
    const input = snippetToInput(row);
    input.domains.push(99);
    input.skills.push(99);
    expect(row.domains).toEqual([1, 2]);
    expect(row.skills).toEqual([3, 4]);
  });

  it("passes null job/project links through", () => {
    const input = snippetToInput(makeRow({ job: null, project: null }));
    expect(input.job).toBeNull();
    expect(input.project).toBeNull();
  });
});

describe("toSnippetPayload", () => {
  function baseInput(over: Partial<SnippetInput> = {}): SnippetInput {
    return { ...emptySnippetInput(), ...over };
  }

  it("trims the title", () => {
    expect(toSnippetPayload(baseInput({ title: "  Hello  " })).title).toBe(
      "Hello",
    );
  });

  it("defaults blank/whitespace language to en, lower-cased", () => {
    expect(toSnippetPayload(baseInput({ language: "" })).language).toBe("en");
    expect(toSnippetPayload(baseInput({ language: "   " })).language).toBe("en");
    expect(toSnippetPayload(baseInput({ language: "DE" })).language).toBe("de");
  });

  it("passes kind, links and is_active through unchanged", () => {
    const input = baseInput({
      kind: "closing",
      domains: [1, 2],
      skills: [3],
      job: 4,
      project: 5,
      is_active: false,
    });
    const payload = toSnippetPayload(input);
    expect(payload.kind).toBe("closing");
    expect(payload.domains).toEqual([1, 2]);
    expect(payload.skills).toEqual([3]);
    expect(payload.job).toBe(4);
    expect(payload.project).toBe(5);
    expect(payload.is_active).toBe(false);
  });

  it("keeps content verbatim (no trimming — markdown may be intentional)", () => {
    const content = "  line one\n\n  line two  ";
    expect(toSnippetPayload(baseInput({ content })).content).toBe(content);
  });
});

describe("snippetSchema", () => {
  const valid: SnippetInput = {
    title: "Intro",
    content: "Hi there.",
    kind: "intro",
    language: "en",
    domains: [],
    skills: [],
    job: null,
    project: null,
    is_active: true,
  };

  it("accepts a valid input", () => {
    expect(snippetSchema.safeParse(valid).success).toBe(true);
  });

  it("rejects an empty title", () => {
    expect(snippetSchema.safeParse({ ...valid, title: "" }).success).toBe(false);
  });

  it("rejects empty content", () => {
    expect(snippetSchema.safeParse({ ...valid, content: "" }).success).toBe(
      false,
    );
  });

  it("rejects an unknown kind", () => {
    expect(
      snippetSchema.safeParse({ ...valid, kind: "banana" }).success,
    ).toBe(false);
  });

  it("accepts nullable job/project links", () => {
    expect(
      snippetSchema.safeParse({ ...valid, job: 3, project: 4 }).success,
    ).toBe(true);
  });
});
