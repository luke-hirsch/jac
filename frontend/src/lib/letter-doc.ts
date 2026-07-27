import type { CoverLetterResult } from "@/lib/queries/generations";

export const LETTER_STUB =
  "⚠️⚠️ THE MODEL COULD NOT WRITE THIS LETTER — regenerate before sending ⚠️⚠️";

export const COMPANY_STUB =
  "⟨ add one line on why THIS company — omitted from exports until you do ⟩";

export type LetterMeta = {
  language: string;
  subject: string;
  salutation: string;
  date: string; // ISO yyyy-mm-dd
  closing: string;
  sender: Record<string, string>;
  recipient: Record<string, string>;
};

export function emptyLetterMeta(language = "en"): LetterMeta {
  return {
    language,
    subject: "",
    salutation: "",
    date: new Date().toISOString().slice(0, 10),
    closing: "",
    sender: {},
    recipient: {},
  };
}

/** Stored letter_meta may be `{}` (pre-guide-1 rows, manual mode) or partial — fill gaps. */
export function normalizeLetterMeta(raw: unknown, language = "en"): LetterMeta {
  const r = (raw ?? {}) as Partial<LetterMeta>;
  return {
    ...emptyLetterMeta(language),
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

/** Mirror of backend jac/cover_letter.py editable_body(): the personal paragraph
 *  (real or stub) OPENS the letter, then the body. */
export function editableBody(letter: CoverLetterResult): string {
  return letter.body;
}

export function hasStub(text: string): boolean {
  return text.includes(LETTER_STUB);
}

export function stripSoftStub(text: string): string {
  return text
    .split("\n\n")
    .filter((block) => !block.includes(COMPANY_STUB))
    .join("\n\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
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
