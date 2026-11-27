import { describe, it, expect } from "vitest";
import { mailDraft, mailtoHref } from "@/lib/mail";
import type { ApplicationRow } from "@/lib/queries/applications";

describe("mailtoHref", () => {
  it("fills recipient, subject and body", () => {
    expect(
      mailtoHref({
        to: "jobs@example.com",
        subject: "Bewerbung als Entwickler",
        body: "Hallo",
      }),
    ).toBe(
      "mailto:jobs@example.com?subject=Bewerbung%20als%20Entwickler&body=Hallo",
    );
  });

  it("writes spaces as %20, never +, which mail apps would show", () => {
    const href = mailtoHref({ to: "", subject: "a b", body: "c d" });
    expect(href).not.toContain("+");
    expect(href).toBe("mailto:?subject=a%20b&body=c%20d");
  });

  it("sends line breaks as CRLF (RFC 6068), however they were typed", () => {
    const href = mailtoHref({
      to: "",
      subject: "",
      body: "eins\nzwei\r\ndrei",
    });
    expect(href).toBe("mailto:?body=eins%0D%0Azwei%0D%0Adrei");
  });

  it("encodes umlauts and the characters that would end a field", () => {
    const href = mailtoHref({
      to: "",
      subject: "Grüße & Co? #1",
      body: "100% sicher",
    });
    expect(href).toBe(
      "mailto:?subject=Gr%C3%BC%C3%9Fe%20%26%20Co%3F%20%231&body=100%25%20sicher",
    );
  });

  it("keeps the @ of the address readable but encodes what would break it", () => {
    expect(mailtoHref({ to: "a+b@example.com", subject: "", body: "" })).toBe(
      "mailto:a%2Bb@example.com",
    );
  });

  it("leaves out what's empty", () => {
    expect(mailtoHref({ to: "", subject: "", body: "" })).toBe("mailto:");
    expect(mailtoHref({ to: "x@example.com", subject: "", body: "Hi" })).toBe(
      "mailto:x@example.com?body=Hi",
    );
  });

  it("trims the address", () => {
    expect(mailtoHref({ to: "  x@example.com ", subject: "", body: "" })).toBe(
      "mailto:x@example.com",
    );
  });
});

function row(over: Partial<ApplicationRow> = {}): ApplicationRow {
  return {
    posting_detail: {
      address: { email: "hr@beispiel.de" },
    },
    letter_meta: { subject: "Bewerbung als Entwickler" },
    email_body: "Sehr geehrte Frau Muster,",
    ...over,
  } as unknown as ApplicationRow;
}

describe("mailDraft", () => {
  it("takes the posting's address, the letter's subject and the email", () => {
    expect(mailDraft(row())).toEqual({
      to: "hr@beispiel.de",
      subject: "Bewerbung als Entwickler",
      body: "Sehr geehrte Frau Muster,",
    });
  });

  it("is empty where the application has nothing", () => {
    const draft = mailDraft(
      row({
        posting_detail: {
          address: null,
        } as unknown as ApplicationRow["posting_detail"],
        letter_meta: {},
        email_body: "",
      }),
    );
    expect(draft).toEqual({ to: "", subject: "", body: "" });
  });
});
