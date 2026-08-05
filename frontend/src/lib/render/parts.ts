/**
 * One entry → {heading, meta, body} — the shared shape the PDF templates and the markdown
 * exporter both render. Joins against the career DB; a missing row falls back to the stored
 * label (mirrors the editor's behaviour, guide 2).
 */
import {
  dateRange,
  entryId,
  formatMonthYear,
  joinEntry,
  SECTION_ORDER,
  type SectionKey,
} from "@/lib/cv-doc";
import type { CvEntry } from "@/lib/queries/generations";
import type {
  CertificationRow,
  CvEntriesResponse,
  EducationRow,
  JobRow,
  LanguageRow,
  ProjectRow,
  SkillRow,
} from "@/lib/queries/jac";

export type EntryParts = {
  heading: string;
  /** Joined range — markdown export only. The PDF uses dateFrom/dateTo. */
  date: string;
  /** Left column, line 1 (NBSP-joined, never wraps). */
  dateFrom: string;
  /** Left column, line 2 — "– present" / "– Jan 2023"; "" for a point in time. */
  dateTo: string;
  /** Secondary line: grade, url. NEVER skills — they belong to the machine layer. */
  meta: string;
  /** Resolved skill names. Hidden layer + markdown only; the page never shows them. */
  skills: string;
  body: string;
  favourite: boolean;
};

const nb = (s: string) => s.replace(/ /g, "\u00A0");

/**
 * The date column as two atomic units. Each side is NBSP-joined **throughout** — the
 * dash included: a plain space after it is a break opportunity, and when the range
 * overflows the column react-pdf takes it, orphaning "– " onto a line of its own and
 * making every row three lines tall (the whitespace bug in this guide's Results).
 *
 * The template joins the two with the one plain space that is left, so the range sets on
 * a single line when the column fits it and degrades to the deliberate two-line split
 * ("Mar 2023" / "– Apr 2025") when a narrower custom layout doesn't.
 */
export function dateParts(
  started: string | null,
  ended: string | null,
): { from: string; to: string } {
  return {
    from: nb(formatMonthYear(started) || "?"),
    to: nb(`– ${ended ? formatMonthYear(ended) : "present"}`),
  };
}
export function datePoint(iso: string | null): { from: string; to: string } {
  return { from: nb(formatMonthYear(iso)), to: "" };
}
export function skillNames(
  db: CvEntriesResponse | undefined,
  ids: number[],
): string {
  if (!db || ids.length === 0) return "";
  return ids
    .map((id) => db.skills.find((s) => s.id === id)?.name)
    .filter(Boolean)
    .join(", ");
}

const PROFICIENCY_IN = new Set(["technical", "domain"]);
const SKILL_CATEGORY_LABELS: Record<string, string> = {
  technical: "Technical",
  soft: "Soft",
  domain: "Domain",
  other: "Other",
};
const SKILL_CATEGORY_ORDER = ["technical", "soft", "domain", "other"];

export function skillGroups(
  db: CvEntriesResponse | undefined,
  entries: CvEntry[],
): { label: string; names: string }[] {
  const groups: Record<string, string[]> = {};
  for (const e of entries) {
    const row = db ? (joinEntry(db, "skills", e) as SkillRow | null) : null;
    const cat = row?.category ?? "other";
    const name = row?.name ?? e.label;
    (groups[cat] ??= []).push(
      PROFICIENCY_IN.has(cat) && row?.proficiency
        ? `${name} (${row.proficiency})`
        : name,
    );
  }
  return SKILL_CATEGORY_ORDER.filter((c) => groups[c]?.length).map((c) => ({
    label: SKILL_CATEGORY_LABELS[c] ?? c,
    names: groups[c].join(", "),
  }));
}
export function entryParts(
  db: CvEntriesResponse | undefined,
  section: SectionKey,
  entry: CvEntry,
): EntryParts {
  const row = db ? joinEntry(db, section, entry) : null;
  const blank = { date: "", dateFrom: "", dateTo: "", meta: "", skills: "" };
  if (!row)
    return { heading: entry.label, ...blank, body: "", favourite: false };
  const favourite = "favourite" in row ? Boolean(row.favourite) : false;
  switch (section) {
    case "jobs": {
      const j = row as JobRow;
      const d = dateParts(j.started, j.ended);
      return {
        heading: `${j.title} — ${j.company}`,
        date: dateRange(j.started, j.ended),
        dateFrom: d.from,
        dateTo: d.to,
        meta: "", // was the skill cloud — now machine-layer only
        skills: skillNames(db, j.skills),
        body: j.description,
        favourite,
      };
    }
    case "educations": {
      const e = row as EducationRow;
      const head = `${e.degree ?? ""} ${e.field_of_study ?? ""}`.trim();
      const d = dateParts(e.started, e.ended);
      return {
        heading: head ? `${head} @ ${e.institution}` : e.institution,
        date: dateRange(e.started, e.ended),
        dateFrom: d.from,
        dateTo: d.to,
        meta: e.grade ? `Grade: ${e.grade}` : "",
        skills: "",
        body: e.description,
        favourite,
      };
    }
    case "projects": {
      const p = row as ProjectRow;
      const d = dateParts(p.started, p.ended);
      return {
        heading: p.name,
        date: dateRange(p.started, p.ended),
        dateFrom: d.from,
        dateTo: d.to,
        meta: p.url, // the link stays visible; the skill cloud does not
        skills: skillNames(db, p.skills),
        body: p.description,
        favourite,
      };
    }
    case "skills": {
      const s = row as SkillRow;
      return {
        heading: s.name,
        ...blank,
        meta: `${s.proficiency} · ${s.category}`,
        body: "",
        favourite,
      };
    }
    case "certifications": {
      const c = row as CertificationRow;
      const d = datePoint(c.issued_on);
      return {
        heading: `${c.name} — ${c.issuer}`,
        date: formatMonthYear(c.issued_on),
        dateFrom: d.from,
        dateTo: d.to,
        meta: "",
        skills: skillNames(db, c.skills),
        body: c.description,
        favourite,
      };
    }
    case "languages": {
      const l = row as LanguageRow;
      return {
        heading: l.name,
        ...blank,
        meta: l.fluency,
        body: "",
        favourite,
      };
    }
  }
}

/** id → favourite?, across all sections — feeds fitCv so favourites drop last. */
export function isFavouriteLookup(
  db: CvEntriesResponse | undefined,
): (id: string) => boolean {
  if (!db) return () => false;
  const favs = new Set<string>();
  for (const section of SECTION_ORDER) {
    for (const row of db[section]) {
      if (row.favourite) favs.add(entryId(section, row.id));
    }
  }
  return (id) => favs.has(id);
}

export const MIN_DETAILED = 1;

export function entryDetail(
  entry: CvEntry,
  index: number,
  section: string,
  detailed: Record<string, number>,
  demoted: Set<string> = new Set(),
): "full" | "compact" {
  if (entry.detail) return entry.detail;
  if (demoted.has(entry.id)) return "compact";
  return index < (detailed[section] ?? 0) ? "full" : "compact";
}
