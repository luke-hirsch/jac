/**
 * One entry → {heading, meta, body} — the shared shape the PDF templates and the markdown
 * exporter both render. Joins against the career DB; a missing row falls back to the stored
 * label (mirrors the editor's behaviour, guide 2).
 */
import {
  dateRange,
  entryId,
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
  meta: string;
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

export function entryParts(
  db: CvEntriesResponse | undefined,
  section: SectionKey,
  entry: CvEntry,
): EntryParts {
  const row = db ? joinEntry(db, section, entry) : null;
  if (!row)
    return { heading: entry.label, meta: "", body: "", favourite: false };
  const favourite = "favourite" in row ? Boolean(row.favourite) : false;
  switch (section) {
    case "jobs": {
      const j = row as JobRow;
      return {
        heading: `${j.title} — ${j.company}`,
        meta: [dateRange(j.started, j.ended), skillNames(db, j.skills)]
          .filter(Boolean)
          .join(" · "),
        body: j.description,
        favourite,
      };
    }
    case "educations": {
      const e = row as EducationRow;
      const head = `${e.degree ?? ""} ${e.field_of_study ?? ""}`.trim();
      return {
        heading: head ? `${head} @ ${e.institution}` : e.institution,
        meta: [
          dateRange(e.started, e.ended),
          e.grade ? `Grade: ${e.grade}` : "",
        ]
          .filter(Boolean)
          .join(" · "),
        body: e.description,
        favourite,
      };
    }
    case "projects": {
      const p = row as ProjectRow;
      return {
        heading: p.name,
        meta: [dateRange(p.started, p.ended), skillNames(db, p.skills), p.url]
          .filter(Boolean)
          .join(" · "),
        body: p.description,
        favourite,
      };
    }
    case "skills": {
      const s = row as SkillRow;
      return {
        heading: s.name,
        meta: `${s.proficiency} (${s.category})`,
        body: "",
        favourite,
      };
    }
    case "certifications": {
      const c = row as CertificationRow;
      return {
        heading: `${c.name} — ${c.issuer}`,
        meta: c.issued_on ? `Issued: ${c.issued_on}` : "",
        body: c.description,
        favourite,
      };
    }
    case "languages": {
      const l = row as LanguageRow;
      return { heading: l.name, meta: l.fluency, body: "", favourite };
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
