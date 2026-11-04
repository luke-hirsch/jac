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
  /** German letters: the form of "you" ("sie" | "ihr" | "du"); "" = none / not
   *  German. Set from the run, editable; rewrite / shorten / chat keep it, and the
   *  editor checks the body against it (lib/register.ts). */
  address_form: string;
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
    address_form: "",
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
  opts: { socials: boolean; portfolioUrl?: string },
): string {
  const parts = [sender.email, sender.phone];
  if (opts.socials) parts.push(sender.website, sender.linkedin, sender.github);
  // Text-layer belt for the QR: ATS parsers and image-stripping viewers keep the link.
  if (opts.portfolioUrl) parts.push(opts.portfolioUrl);
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
    // The form, not the run's leaks: the editor re-checks the body it holds.
    address_form: letter.register?.form ?? "",
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

/** Month names for the letter date. Only the two languages the letter furniture supports
 *  (cover_letter.py _SALUTATION / _CLOSING); anything else falls back to ISO, which is
 *  unambiguous everywhere and never the US month-first form. */
const MONTHS: Record<string, string[]> = {
  de: [
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
  ],
  en: [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
  ],
};

/** ISO date → the long business-letter form: "27. Juli 2026" / "27 July 2026". */
export function formatLetterDate(iso: string, language: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec((iso ?? "").trim());
  if (!m) return iso ?? "";
  const [, y, mo, d] = m;
  const names = MONTHS[(language ?? "en").slice(0, 2).toLowerCase()];
  if (!names) return iso;
  const month = names[Number(mo) - 1];
  const day = Number(d);
  return language.slice(0, 2).toLowerCase() === "de"
    ? `${day}. ${month} ${y}`
    : `${day} ${month} ${y}`;
}

/** Words, the way the shorten budget counts them. */
export function countWords(text: string): number {
  return (text ?? "").trim() ? text.trim().split(/\s+/).length : 0;
}
