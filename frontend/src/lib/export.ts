/**
 * Markdown/JSON exports. The markdown mirrors the backend renderers (jac/render.py
 * CvRender.export_md and jac/cover_letter.py render_markdown) so every export format tells
 * the same story.
 */
import {
  joinEntry,
  SECTION_ORDER,
  SECTION_TITLES,
  type CvContent,
} from "@/lib/cv-doc";
import { hasStub, stripSoftStub, type LetterMeta } from "@/lib/letter-doc";
import type { ApplicationRow } from "@/lib/queries/applications";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import type { ProfileRow } from "@/lib/queries/profile";
import { entryParts } from "@/lib/render/parts";

export function cvToMarkdown(
  name: string,
  content: CvContent,
  db: CvEntriesResponse | undefined,
): string {
  const lines: string[] = [`# ${name}`, ""];
  for (const section of SECTION_ORDER) {
    const entries = content[section] ?? [];
    if (entries.length === 0) continue;
    lines.push(`## ${SECTION_TITLES[section]}`, "");
    for (const e of entries) {
      const p = entryParts(db, section, e);
      lines.push(`### ${p.favourite ? "★ " : ""}${p.heading}`);
      const metaLine = [p.date, p.meta, p.skills].filter(Boolean).join(" · ");
      if (metaLine) lines.push(metaLine);
      // The three detail signals are not equal. `entry.detail` is editorial — "this job
      // is a footnote" — and holds in any format. The other two (rank against the
      // layout budget, and a fit demotion) exist because the PAGE ran out of room, and
      // markdown has no pages. So markdown reads the override off the entry and ignores
      // the rest; it needs no `detailed` map and keeps its three-argument signature.
      if (p.body && e.detail !== "compact") lines.push(p.body);
      lines.push("");
    }
  }
  return lines.join("\n").replace(/\n+$/, "") + "\n";
}

/** Mirrors CoverLetter.render_markdown: sender block, recipient block, date, subject, …, name. */
export function letterToMarkdown(meta: LetterMeta, body: string): string {
  body = stripSoftStub(body);
  const snd = meta.sender;
  const rcp = meta.recipient;
  const out: string[] = [];
  const push = (line: string | undefined) => {
    if (line) out.push(line);
  };

  push(snd.name);
  push(snd.street);
  push(snd.address_line2);
  push([snd.zip, snd.city].filter(Boolean).join(" "));
  push(snd.country);
  push([snd.email, snd.phone].filter(Boolean).join(" · "));
  out.push("");

  push(rcp.company);
  push(rcp.contact_name);
  push(rcp.street);
  push(rcp.address_line2);
  push([rcp.zip, rcp.city].filter(Boolean).join(" "));
  push(rcp.country);
  out.push("");

  out.push(
    meta.date,
    "",
    `**${meta.subject}**`,
    "",
    meta.salutation,
    "",
    body,
    "",
  );
  out.push(meta.closing, "", snd.name ?? "");
  return out.join("\n").replace(/\n+$/, "") + "\n";
}

export type ExportScope = "complete" | "cv" | "letter";
export type ExportFormat = "pdf" | "md" | "json";

/* ---------- file names ---------- */

const SCOPE_WORD: Record<ExportScope, string> = {
  complete: "application",
  cv: "cv",
  letter: "letter",
};

// The gender marker German titles carry, "(m/w/d)", "(w/m/d)", "(m/f/x)", "(all genders)", "(gn)".
const GENDER_MARKER =
  /\(\s*(?:(?:[mwfdxi]|div)(?:\s*[/|,]\s*(?:[mwfdxi]|div))+|all\s+genders?|gn\*?)\s*\)/gi;
const SPELLED_OUT: Record<string, string> = {
  ä: "ae",
  ö: "oe",
  ü: "ue",
  Ä: "Ae",
  Ö: "Oe",
  Ü: "Ue",
  ß: "ss",
};
const PART_MAX = 60;

/** One part of a file name: ASCII letters, digits and `+`, words joined by hyphens, since the
 *  underscore separates the parts. Umlauts spelled out (upload portals choke on them), other
 *  accents dropped, the gender marker left out; cut at a word boundary past 60 characters. */
export function fileNamePart(text: string): string {
  const word = text
    .replace(GENDER_MARKER, " ")
    .replace(/[äöüÄÖÜß]/g, (c) => SPELLED_OUT[c])
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^A-Za-z0-9+]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (word.length <= PART_MAX) return word;
  const cut = word.slice(0, PART_MAX + 1);
  const end = cut.lastIndexOf("-");
  return (end > 0 ? cut.slice(0, end) : word.slice(0, PART_MAX)).replace(
    /-+$/,
    "",
  );
}

/** What every export of an application is called, without the extension:
 *  `cv_<posting title>_<your name>`, `letter_…`, `application_…` for the complete one. The name
 *  is the full name from the profile, else the name jac signs with (display name or username).
 *  A posting without a title falls back to the company, then the application's number. */
export function exportStem(
  scope: ExportScope,
  row: Pick<ApplicationRow, "id" | "posting_detail" | "letter_meta">,
  profile: Pick<ProfileRow, "first_name" | "last_name" | "name"> | undefined,
): string {
  const full = [profile?.first_name, profile?.last_name]
    .map((s) => (s ?? "").trim())
    .filter(Boolean)
    .join(" ");
  const person = fileNamePart(
    full || profile?.name || row.letter_meta?.sender?.name || "",
  );
  const subject =
    fileNamePart(row.posting_detail?.title ?? "") ||
    fileNamePart(row.letter_meta?.recipient?.company ?? "") ||
    String(row.id);
  return [SCOPE_WORD[scope], subject, person].filter(Boolean).join("_");
}

/**
 * Send-time stub safeguard: a letter-bearing pdf/md export with the PERSONAL_STUB still in
 * the body is refused — that document must never reach an employer. Returns the reason to
 * show, or null when the export may proceed. JSON is exempt (a data dump, not a sendable
 * artefact); so is a cv-only export (no letter in it).
 */
export function exportBlocker(
  scope: ExportScope,
  format: ExportFormat,
  body: string,
): string | null {
  if (scope === "cv" || format === "json") return null;
  if (hasStub(body)) {
    return "The letter could not be generated (placeholder still in the body) — regenerate before exporting.";
  }
  return null;
}

/** content joined with the career-DB rows behind it — the shared export/hidden-layer shape.
 *  `skill_names` is the resolved form of the row's `skills` id array: since the CV page
 *  stopped printing the per-entry skill cloud, this is where a parser reads it. */
export function joinedContent(
  content: CvContent,
  db: CvEntriesResponse | undefined,
) {
  return Object.fromEntries(
    SECTION_ORDER.map((section) => [
      section,
      (content[section] ?? []).map((e) => {
        const skills = entryParts(db, section, e).skills;
        return {
          ...e,
          entry: joinEntry(db, section, e),
          ...(skills ? { skill_names: skills } : {}),
        };
      }),
    ]),
  );
}

/** JSON export: the selection joined with the career-DB rows behind it (frozen snapshot). */
export function exportJson(
  scope: ExportScope,
  args: {
    content: CvContent; // active (deselected stripped); pass the fitted one if you want WYSIWYG
    meta: LetterMeta;
    body: string;
    db: CvEntriesResponse | undefined;
  },
): string {
  const cv = joinedContent(args.content, args.db);
  const letter = { meta: args.meta, body: args.body };
  const payload =
    scope === "cv" ? { cv } : scope === "letter" ? { letter } : { cv, letter };
  return JSON.stringify(payload, null, 2);
}
/* ---------- downloads (browser-only) ---------- */

/** Safari starts the download after `click()` has returned: revoking right away fails it. */
const REVOKE_AFTER_MS = 40_000;

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_AFTER_MS);
}

export function downloadText(
  text: string,
  filename: string,
  mime = "text/plain",
) {
  downloadBlob(new Blob([text], { type: mime }), filename);
}
