import { describe, it, expect } from "vitest";
import {
  PERSONAL_STUB,
  appendParagraph,
  editableBody,
  emptyLetterMeta,
  hasStub,
  letterMetaFromResult,
  normalizeLetterMeta,
  replaceStub,
} from "@/lib/letter-doc";
import type { CoverLetterResult } from "@/lib/queries/generations";

/**
 * Pure letter logic (guide [frontend]-letter-editor). The letter lives in two application
 * fields: `cover_letter` = editable body (woven snippets + personal paragraph or stub),
 * `letter_meta` = furniture (subject/salutation/date/closing/sender/recipient). The stub
 * marker must match backend jac/cover_letter.py PERSONAL_STUB byte for byte.
 */

const letter = {
  language: "de",
  subject: "Bewerbung als Dev",
  salutation: "Sehr geehrte Frau Doe,",
  body: "Ich baue Dinge.",
  sender: { name: "Ada Lovelace", city: "Berlin" },
  recipient: { company: "ACME", contact_name: "Jane Doe" },
  date: "2026-07-09",
  closing: "Mit freundlichen Grüßen,",
  personal_paragraph: "",
  personal_paragraph_is_stub: false,
} as CoverLetterResult;

describe("letterMetaFromResult", () => {
  it("takes exactly the furniture slice", () => {
    expect(letterMetaFromResult(letter)).toEqual({
      language: "de",
      subject: "Bewerbung als Dev",
      salutation: "Sehr geehrte Frau Doe,",
      date: "2026-07-09",
      closing: "Mit freundlichen Grüßen,",
      sender: { name: "Ada Lovelace", city: "Berlin" },
      recipient: { company: "ACME", contact_name: "Jane Doe" },
    });
  });
});

describe("normalizeLetterMeta", () => {
  it("fills a legacy empty object with defaults", () => {
    const meta = normalizeLetterMeta({});
    expect(meta.language).toBe("en");
    expect(meta.sender).toEqual({});
    expect(meta.recipient).toEqual({});
    expect(meta.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("keeps provided fields and fills the gaps", () => {
    const meta = normalizeLetterMeta({ subject: "Hi", recipient: { company: "X" } });
    expect(meta.subject).toBe("Hi");
    expect(meta.recipient).toEqual({ company: "X" });
    expect(meta.closing).toBe("");
  });

  it("tolerates null/undefined", () => {
    expect(normalizeLetterMeta(null).subject).toBe("");
    expect(normalizeLetterMeta(undefined)).toEqual(
      expect.objectContaining(emptyLetterMeta()),
    );
  });
});

describe("editableBody", () => {
  it("is the body alone when there is no personal paragraph", () => {
    expect(editableBody(letter)).toBe("Ich baue Dinge.");
  });

  it("appends the personal paragraph as its own block", () => {
    expect(
      editableBody({ ...letter, personal_paragraph: "Ich bewundere ACME." }),
    ).toBe("Ich baue Dinge.\n\nIch bewundere ACME.");
  });

  it("keeps a stub paragraph loud", () => {
    expect(
      editableBody({ ...letter, personal_paragraph: PERSONAL_STUB }),
    ).toContain(PERSONAL_STUB);
  });
});

describe("hasStub / replaceStub", () => {
  const withStub = `Intro.\n\n${PERSONAL_STUB}\n\nOutro.`;

  it("detects the marker", () => {
    expect(hasStub(withStub)).toBe(true);
    expect(hasStub("clean letter")).toBe(false);
  });

  it("swaps the marker for the user's paragraph", () => {
    expect(replaceStub(withStub, "My own words.")).toBe(
      "Intro.\n\nMy own words.\n\nOutro.",
    );
  });

  it("replaces every occurrence", () => {
    const twice = `${PERSONAL_STUB}\n\n${PERSONAL_STUB}`;
    const out = replaceStub(twice, "Once.");
    expect(out).not.toContain(PERSONAL_STUB);
    expect(out.match(/Once\./g)).toHaveLength(2);
  });

  it("an empty paragraph removes the stub and collapses its padding", () => {
    expect(replaceStub(withStub, "  ")).toBe("Intro.\n\nOutro.");
  });
});

describe("appendParagraph", () => {
  it("appends as a new block, trimming trailing whitespace", () => {
    expect(appendParagraph("Body.\n\n", "Snippet text.")).toBe(
      "Body.\n\nSnippet text.",
    );
  });

  it("starts the text when empty", () => {
    expect(appendParagraph("", "Snippet text.")).toBe("Snippet text.");
  });

  it("ignores blank paragraphs", () => {
    expect(appendParagraph("Body.", "   ")).toBe("Body.");
  });
});
