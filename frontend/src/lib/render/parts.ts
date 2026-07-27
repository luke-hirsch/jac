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
  date: string; // the moderncv left-column date/period; "" for skills/languages
  meta: string; // secondary line: skills, grade, url — NO date
  body: string;
  favourite: boolean;
};
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

export function skillGroups(
  db: CvEntriesResponse | undefined,
  entries: CvEntry[],
): { label: string; names: string }[] {
  const groups: Record<string, string[]> = {};
  for (const e of entries) {
    const row = db ? (joinEntry(db, "skills", e) as SkillRow | null) : null;
    const cat = row?.category ?? "other";
    (groups[cat] ??= []).push(row?.name ?? e.label);
  }
  return SKILL_CATEGORY_ORDER.filter((c) => groups[c]?.length).map((c) => ({
    label: SKILL_CATEGORY_LABELS[c] ?? c,
    names: groups[c].join(", "),
  }));
}
const SKILL_CATEGORY_LABELS: Record<string, string> = {
  technical: "Technical",
  soft: "Soft",
  domain: "Domain",
  other: "Other",
};
const SKILL_CATEGORY_ORDER = ["technical", "soft", "domain", "other"];

export function entryParts(
  db: CvEntriesResponse | undefined,
  section: SectionKey,
  entry: CvEntry,
): EntryParts {
  const row = db ? joinEntry(db, section, entry) : null;
  if (!row)
    return {
      heading: entry.label,
      date: "",
      meta: "",
      body: "",
      favourite: false,
    };
  const favourite = "favourite" in row ? Boolean(row.favourite) : false;
  switch (section) {
    case "jobs": {
      const j = row as JobRow;
      return {
        heading: `${j.title} — ${j.company}`,
        date: dateRange(j.started, j.ended),
        meta: skillNames(db, j.skills),
        body: j.description,
        favourite,
      };
    }
    case "educations": {
      const e = row as EducationRow;
      const head = `${e.degree ?? ""} ${e.field_of_study ?? ""}`.trim();
      return {
        heading: head ? `${head} @ ${e.institution}` : e.institution,
        date: dateRange(e.started, e.ended),
        meta: e.grade ? `Grade: ${e.grade}` : "",
        body: e.description,
        favourite,
      };
    }
    case "projects": {
      const p = row as ProjectRow;
      return {
        heading: p.name,
        date: dateRange(p.started, p.ended),
        meta: [skillNames(db, p.skills), p.url].filter(Boolean).join(" · "),
        body: p.description,
        favourite,
      };
    }
    case "skills": {
      const s = row as SkillRow;
      return {
        heading: s.name,
        date: "",
        meta: `${s.proficiency} · ${s.category}`,
        body: "",
        favourite,
      };
    }
    case "certifications": {
      const c = row as CertificationRow;
      return {
        heading: `${c.name} — ${c.issuer}`,
        date: formatMonthYear(c.issued_on),
        meta: "",
        body: c.description,
        favourite,
      };
    }
    case "languages": {
      const l = row as LanguageRow;
      return {
        heading: l.name,
        date: "",
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
