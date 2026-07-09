/**
 * Pure editing logic for the application's tailored-CV JSON (`cv_content`).
 *
 * Shape: `{section: [{id, label, relevance_score, deselected?}]}` — plural section keys
 * ("jobs"), ids "<singular>:<pk>" ("job:12"), entries in ranked order (the order IS the rank;
 * backend jac/generation_result.py). Display joins the ids against the live career DB
 * (`/api/jac/cv/entries/`); the stored label is the fallback for rows since deleted there.
 * All operations are immutable — they return a new CvContent.
 */
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

export type CvContent = Record<string, CvEntry[]>;

/** Render order — mirrors backend CvRender.SECTION_ORDER (jac/render.py). */
export const SECTION_ORDER = [
  "jobs",
  "educations",
  "projects",
  "skills",
  "certifications",
  "languages",
] as const;
export type SectionKey = (typeof SECTION_ORDER)[number];

export const SECTION_TITLES: Record<SectionKey, string> = {
  jobs: "Experience",
  educations: "Education",
  projects: "Projects",
  skills: "Skills",
  certifications: "Certifications",
  languages: "Languages",
};

const SINGULAR: Record<SectionKey, string> = {
  jobs: "job",
  educations: "education",
  projects: "project",
  skills: "skill",
  certifications: "certification",
  languages: "language",
};

/** "job:12"-style id for a career-DB row. */
export function entryId(section: SectionKey, pk: number): string {
  return `${SINGULAR[section]}:${pk}`;
}

export type AnyRow =
  | SkillRow
  | JobRow
  | EducationRow
  | CertificationRow
  | ProjectRow
  | LanguageRow;

export function parseEntryId(id: string): { type: string; pk: number } | null {
  const m = /^([a-z_]+):(\d+)$/.exec(id);
  return m ? { type: m[1], pk: Number(m[2]) } : null;
}

/** The career-DB row behind a cv_content entry, or null (db not loaded / row deleted). */
export function joinEntry(
  db: CvEntriesResponse | undefined,
  section: SectionKey,
  entry: CvEntry,
): AnyRow | null {
  if (!db) return null;
  const parsed = parseEntryId(entry.id);
  if (!parsed) return null;
  return (db[section] as AnyRow[]).find((r) => r.id === parsed.pk) ?? null;
}

export function dateRange(
  started: string | null,
  ended: string | null,
): string {
  return `${started ?? "?"}–${ended ?? "present"}`;
}

/**
 * One-line label per entry — mirrors the backend labelers (jac/generation_result.py) so a
 * manually built CV reads like a generated one. Divergence: the skill label omits the
 * "| domains: …" suffix (the API row carries domain pks, not names — not worth a lookup here).
 */
export function labelFor(section: SectionKey, row: AnyRow): string {
  switch (section) {
    case "skills": {
      const s = row as SkillRow;
      return `${s.name} (${s.proficiency}, ${s.category})`;
    }
    case "jobs": {
      const j = row as JobRow;
      return `${j.title} at ${j.company} (${dateRange(j.started, j.ended)})`;
    }
    case "educations": {
      const e = row as EducationRow;
      const head = `${e.degree ?? ""} ${e.field_of_study ?? ""}`.trim();
      const w = dateRange(e.started, e.ended);
      return head
        ? `${head} @ ${e.institution} (${w})`
        : `${e.institution} (${w})`;
    }
    case "certifications": {
      const c = row as CertificationRow;
      return `${c.name} — ${c.issuer}${c.issued_on ? ` (${c.issued_on})` : ""}`;
    }
    case "projects": {
      const p = row as ProjectRow;
      return `${p.name} (${dateRange(p.started, p.ended)})`;
    }
    case "languages": {
      const l = row as LanguageRow;
      return `${l.name} (${l.fluency})`;
    }
  }
}

/** Manual mode: a cv_content with every career-DB entry, API order, no scores. */
export function fromCareerDb(db: CvEntriesResponse): CvContent {
  const content: CvContent = {};
  for (const section of SECTION_ORDER) {
    content[section] = (db[section] as AnyRow[]).map((row) => ({
      id: `${SINGULAR[section]}:${row.id}`,
      label: labelFor(section, row),
      relevance_score: null,
    }));
  }
  return content;
}

export function moveEntry(
  content: CvContent,
  section: string,
  index: number,
  delta: -1 | 1,
): CvContent {
  const list = content[section] ?? [];
  const target = index + delta;
  if (
    index < 0 ||
    index >= list.length ||
    target < 0 ||
    target >= list.length
  ) {
    return content;
  }
  const next = [...list];
  [next[index], next[target]] = [next[target], next[index]];
  return { ...content, [section]: next };
}

export function removeEntry(
  content: CvContent,
  section: string,
  index: number,
): CvContent {
  const list = content[section] ?? [];
  if (index < 0 || index >= list.length) return content;
  return { ...content, [section]: list.filter((_, i) => i !== index) };
}

export function toggleDeselect(
  content: CvContent,
  section: string,
  index: number,
): CvContent {
  const list = content[section] ?? [];
  if (index < 0 || index >= list.length) return content;
  const next = list.map((e, i) =>
    i === index ? { ...e, deselected: !e.deselected } : e,
  );
  return { ...content, [section]: next };
}

/** Deselected entries stripped — the shape the render/export pipeline (guide 4) consumes. */
export function activeContent(content: CvContent): CvContent {
  const out: CvContent = {};
  for (const [section, list] of Object.entries(content)) {
    out[section] = list.filter((e) => !e.deselected);
  }
  return out;
}

/** Career-DB rows of a section not (or no longer) in the content — the add-picker options. */
export function missingEntries(
  db: CvEntriesResponse | undefined,
  section: SectionKey,
  entries: CvEntry[],
): AnyRow[] {
  if (!db) return [];
  const present = new Set(entries.map((e) => e.id));
  return (db[section] as AnyRow[]).filter(
    (r) => !present.has(entryId(section, r.id)),
  );
}

/** Append a career-DB row to a section — end of list (= lowest rank), no score. */
export function addEntry(
  content: CvContent,
  section: SectionKey,
  row: AnyRow,
): CvContent {
  const list = content[section] ?? [];
  const id = entryId(section, row.id);
  if (list.some((e) => e.id === id)) return content;
  return {
    ...content,
    [section]: [
      ...list,
      { id, label: labelFor(section, row), relevance_score: null },
    ],
  };
}
