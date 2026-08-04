import { describe, it, expect } from "vitest";
import {
  COMPANY_STUB,
  LETTER_STUB,
  contactLine,
  editableBody,
  emptyLetterMeta,
  fillBlanks,
  hasStub,
  letterMetaFromResult,
  normalizeLetterMeta,
  replaceRange,
  senderFromProfile,
  stripSoftStub,
} from "@/lib/letter-doc";
import type { CoverLetterResult } from "@/lib/queries/generations";

/**
 * Pure letter logic (guide [frontend]-letter-editor). The letter lives in two application
 * fields: `cover_letter` = the editable body (the woven letter text — the backend folds any
 * personal paragraph straight into it), `letter_meta` = furniture (subject/salutation/date/
 * closing/sender/recipient). Two markers must match the backend (jac/cover_letter.py) byte for
 * byte: LETTER_STUB (the writer produced nothing → blocks export) and COMPANY_STUB (the soft
 * "why this company" placeholder → stripped from exports until filled).
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
  it("is the letter body verbatim — the backend already wove any personal paragraph in", () => {
    expect(editableBody(letter)).toBe("Ich baue Dinge.");
  });

  it("passes a failure stub straight through so the editor shows it loud", () => {
    expect(editableBody({ ...letter, body: LETTER_STUB })).toBe(LETTER_STUB);
  });
});

describe("hasStub (export gate on the failure marker)", () => {
  it("detects the failure stub and ignores a clean body", () => {
    expect(hasStub(`Intro.\n\n${LETTER_STUB}\n\nOutro.`)).toBe(true);
    expect(hasStub("clean letter")).toBe(false);
  });
});

describe("stripSoftStub (drops the 'why this company' placeholder from exports)", () => {
  it("removes the stub block and collapses its padding", () => {
    expect(stripSoftStub(`Intro.\n\n${COMPANY_STUB}\n\nOutro.`)).toBe(
      "Intro.\n\nOutro.",
    );
  });

  it("removes every stub block", () => {
    const twice = `${COMPANY_STUB}\n\nMiddle.\n\n${COMPANY_STUB}`;
    const out = stripSoftStub(twice);
    expect(out).not.toContain(COMPANY_STUB);
    expect(out).toBe("Middle.");
  });

  it("leaves a clean body untouched (bar trimming)", () => {
    expect(stripSoftStub("A finished letter.")).toBe("A finished letter.");
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

  // Guide [fullstack]-portfolio-cv-qr: the portfolio URL rides in the text layer as a
  // belt for the QR image (ATS parsers / image-stripping viewers keep the link).
  it("appends the portfolio URL after socials", () => {
    const line = contactLine(sender, {
      socials: true,
      portfolioUrl: "https://lukehirsch.com/portfolio/acme-x7f3",
    });
    expect(line.endsWith("https://lukehirsch.com/portfolio/acme-x7f3")).toBe(true);
  });

  it("omits the portfolio URL when absent or blank", () => {
    expect(contactLine(sender, { socials: false })).toBe("ada@x.com · +49 123");
    expect(contactLine(sender, { socials: false, portfolioUrl: "" })).toBe(
      "ada@x.com · +49 123",
    );
  });

  it("includes the portfolio URL even with socials off", () => {
    expect(
      contactLine(sender, { socials: false, portfolioUrl: "https://x.dev/p/y" }),
    ).toBe("ada@x.com · +49 123 · https://x.dev/p/y");
  });
});

/**
 * Manual mode: a hand-curated application has no run to bring furniture, so
 * emptyLetterMeta / normalizeLetterMeta take the POSTING language as the fallback
 * (not a blanket "en"); a stored language still wins over it.
 */
describe("letter-meta posting-language fallback", () => {
  it("emptyLetterMeta takes the posting language", () => {
    expect(emptyLetterMeta("de").language).toBe("de");
    expect(emptyLetterMeta().language).toBe("en");
  });

  it("normalizeLetterMeta falls back to the posting language", () => {
    expect(normalizeLetterMeta({}, "de").language).toBe("de");
    expect(normalizeLetterMeta(undefined, "de").language).toBe("de");
  });

  it("a stored language always wins over the fallback", () => {
    expect(normalizeLetterMeta({ language: "en" }, "de").language).toBe("en");
  });
});
