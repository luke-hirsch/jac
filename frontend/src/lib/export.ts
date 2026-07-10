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
import { hasStub, type LetterMeta } from "@/lib/letter-doc";
import type { CvEntriesResponse } from "@/lib/queries/jac";
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
      if (p.meta) lines.push(p.meta);
      if (p.body) lines.push(p.body);
      lines.push("");
    }
  }
  return lines.join("\n").replace(/\n+$/, "") + "\n";
}

/** Mirrors CoverLetter.render_markdown: sender block, recipient block, date, subject, …, name. */
export function letterToMarkdown(meta: LetterMeta, body: string): string {
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
    return (
      "The letter body still contains the personal-paragraph stub — " +
      "replace it before exporting."
    );
  }
  return null;
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
  const cv = Object.fromEntries(
    SECTION_ORDER.map((section) => [
      section,
      (args.content[section] ?? []).map((e) => ({
        ...e,
        entry: joinEntry(args.db, section, e),
      })),
    ]),
  );
  const letter = { meta: args.meta, body: args.body };
  const payload =
    scope === "cv" ? { cv } : scope === "letter" ? { letter } : { cv, letter };
  return JSON.stringify(payload, null, 2);
}

/* ---------- downloads (browser-only, not unit-tested) ---------- */

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function downloadText(
  text: string,
  filename: string,
  mime = "text/plain",
) {
  downloadBlob(new Blob([text], { type: mime }), filename);
}
