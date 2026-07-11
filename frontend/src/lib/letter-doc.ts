import type { CoverLetterResult } from "@/lib/queries/generations";

/** Must match backend jac/cover_letter.py PERSONAL_STUB byte for byte. */
export const PERSONAL_STUB =
  "⚠️⚠️ WRITE A PERSONAL PARAGRAPH YOU LAZY PIECE OF SHIT ⚠️⚠️";

export type LetterMeta = {
  language: string;
  subject: string;
  salutation: string;
  date: string; // ISO yyyy-mm-dd
  closing: string;
  sender: Record<string, string>;
  recipient: Record<string, string>;
};

export function emptyLetterMeta(): LetterMeta {
  return {
    language: "en",
    subject: "",
    salutation: "",
    date: new Date().toISOString().slice(0, 10),
    closing: "",
    sender: {},
    recipient: {},
  };
}

/** Stored letter_meta may be `{}` (pre-guide-1 rows, manual mode) or partial — fill gaps. */
export function normalizeLetterMeta(raw: unknown): LetterMeta {
  const r = (raw ?? {}) as Partial<LetterMeta>;
  return {
    ...emptyLetterMeta(),
    ...r,
    sender: { ...(r.sender ?? {}) },
    recipient: { ...(r.recipient ?? {}) },
  };
}

/** Fill only the blank/missing keys of `target` — explicit values always win. */
export function fillBlanks(
  target: Record<string, string>,
  defaults: Record<string, string>,
): Record<string, string> {
  const out = { ...target };
  for (const [key, value] of Object.entries(defaults)) {
    if (!(out[key] ?? "").trim() && value) out[key] = value;
  }
  return out;
}

/** The sender block a user profile implies — mirrors backend CoverLetter._sender(). */
export function senderFromProfile(p: {
  name: string;
  email: string;
  phone: string;
  street: string;
  address_line2: string;
  zip: string;
  city: string;
  country: string;
  website: string;
  linkedin_url: string;
  github_url: string;
}): Record<string, string> {
  return {
    name: p.name,
    email: p.email,
    phone: p.phone,
    street: p.street,
    address_line2: p.address_line2,
    zip: p.zip,
    city: p.city,
    country: p.country,
    website: p.website,
    linkedin: p.linkedin_url,
    github: p.github_url,
  };
}

export function contactLine(
  sender: Record<string, string>,
  opts: { socials: boolean },
): string {
  const parts = [sender.email, sender.phone];
  if (opts.socials) parts.push(sender.website, sender.linkedin, sender.github);
  return parts.filter(Boolean).join(" · ");
}

export function letterMetaFromResult(letter: CoverLetterResult): LetterMeta {
  return {
    language: letter.language,
    subject: letter.subject,
    salutation: letter.salutation,
    date: letter.date,
    closing: letter.closing,
    sender: letter.sender,
    recipient: letter.recipient,
  };
}

/** Mirror of backend jac/cover_letter.py editable_body(): body + personal paragraph/stub. */
export function editableBody(letter: CoverLetterResult): string {
  const parts = [letter.body];
  if (letter.personal_paragraph) parts.push(letter.personal_paragraph);
  return parts.filter(Boolean).join("\n\n");
}

export function hasStub(text: string): boolean {
  return text.includes(PERSONAL_STUB);
}

/**
 * Swap every stub marker for the user's paragraph. An empty paragraph removes the stub
 * instead, collapsing the blank-line padding it sat between.
 */
export function replaceStub(text: string, paragraph: string): string {
  const p = paragraph.trim();
  if (p) return text.split(PERSONAL_STUB).join(p);
  return text
    .split("\n\n")
    .map((block) => block.split(PERSONAL_STUB).join("").trim())
    .filter((block) => block !== "")
    .join("\n\n");
}

/** Append a paragraph (e.g. a snippet's content) as its own block. */
export function appendParagraph(text: string, paragraph: string): string {
  const p = paragraph.trim();
  if (!p) return text;
  const base = text.replace(/\s+$/, "");
  return base ? `${base}\n\n${p}` : p;
}

/** Splice `replacement` over [start, end) — how an AI-rewritten selection lands back. */
export function replaceRange(
  text: string,
  start: number,
  end: number,
  replacement: string,
): string {
  const from = Math.max(0, Math.min(start, text.length));
  const to = Math.max(from, Math.min(end, text.length));
  return text.slice(0, from) + replacement + text.slice(to);
}
