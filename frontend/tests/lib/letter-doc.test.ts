import { describe, it, expect } from "vitest";
import {
  PERSONAL_STUB,
  appendParagraph,
  contactLine,
  editableBody,
  emptyLetterMeta,
  fillBlanks,
  hasStub,
  letterMetaFromResult,
  normalizeLetterMeta,
  replaceRange,
  replaceStub,
  senderFromProfile,
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
} as unknown as CoverLetterResult;

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

  it("opens with the personal paragraph (letter-quality: paragraph-as-opener)", () => {
    expect(
      editableBody({ ...letter, personal_paragraph: "Ich bewundere ACME." }),
    ).toBe("Ich bewundere ACME.\n\nIch baue Dinge.");
  });

  it("keeps a stub paragraph loud, right at the top", () => {
    expect(
      editableBody({ ...letter, personal_paragraph: PERSONAL_STUB }).startsWith(
        PERSONAL_STUB,
      ),
    ).toBe(true);
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

describe("replaceRange", () => {
  it("splices the replacement over the selection", () => {
    // "aaa bbb ccc": textarea selection of "bbb" is [4, 7)
    expect(replaceRange("aaa bbb ccc", 4, 7, "XXX")).toBe("aaa XXX ccc");
  });

  it("inserts at a collapsed selection", () => {
    expect(replaceRange("ab", 1, 1, "-")).toBe("a-b");
  });

  it("clamps out-of-range indices instead of throwing", () => {
    expect(replaceRange("abc", -5, 99, "X")).toBe("X");
    expect(replaceRange("abc", 2, 1, "X")).toBe("abXc"); // end < start → collapsed at start
  });
});

describe("fillBlanks / senderFromProfile (profile → sender defaults)", () => {
  const profile = {
    name: "Ada Lovelace",
    email: "ada@x.com",
    phone: "+49 123",
    street: "Musterstr. 1",
    address_line2: "",
    zip: "10115",
    city: "Berlin",
    country: "Germany",
    website: "https://ada.dev",
    linkedin_url: "https://linkedin.com/in/ada",
    github_url: "https://github.com/ada",
  };

  it("fills only blank fields — explicit values always win", () => {
    const merged = fillBlanks(
      { name: "A. Byron", city: "  ", email: "" },
      senderFromProfile(profile),
    );
    expect(merged.name).toBe("A. Byron"); // explicit wins
    expect(merged.city).toBe("Berlin"); // whitespace counts as blank
    expect(merged.email).toBe("ada@x.com");
    expect(merged.street).toBe("Musterstr. 1"); // missing key filled
  });

  it("never writes empty defaults", () => {
    const merged = fillBlanks({}, senderFromProfile(profile));
    expect(merged).not.toHaveProperty("address_line2"); // blank in the profile
  });

  it("maps the whole sender block the templates consume", () => {
    const sender = senderFromProfile(profile);
    for (const key of [
      "name",
      "email",
      "phone",
      "street",
      "zip",
      "city",
      "country",
      "website",
      "linkedin",
      "github",
    ]) {
      expect(sender[key]).toBeTruthy();
    }
  });
});

describe("contactLine (CV contact header)", () => {
  const sender = senderFromProfile({
    name: "Ada",
    email: "ada@x.com",
    phone: "+49 123",
    street: "",
    address_line2: "",
    zip: "",
    city: "",
    country: "",
    website: "https://ada.dev",
    linkedin_url: "https://linkedin.com/in/ada",
    github_url: "https://github.com/ada",
  });

  it("shows only email + phone when socials are off", () => {
    expect(contactLine(sender, { socials: false })).toBe("ada@x.com · +49 123");
  });

  it("adds website + socials when on", () => {
    const line = contactLine(sender, { socials: true });
    expect(line).toContain("https://ada.dev");
    expect(line).toContain("https://linkedin.com/in/ada");
    expect(line).toContain("https://github.com/ada");
  });

  it("drops blank fields", () => {
    expect(contactLine({ email: "a@b.c" }, { socials: true })).toBe("a@b.c");
  });
});
