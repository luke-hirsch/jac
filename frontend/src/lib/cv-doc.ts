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

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** ISO `yyyy`, `yyyy-mm`, or `yyyy-mm-dd` → "Mon yyyy" (or bare "yyyy"). Unknown
 *  shapes pass through untouched; empty/null → "". Keeps CV dates short enough to
 *  wrap inside the fixed hints column instead of overflowing the content column. */
export function formatMonthYear(iso: string | null): string {
  if (!iso) return "";
  const m = /^(\d{4})(?:-(\d{2}))?/.exec(iso);
  if (!m) return iso;
  const mi = m[2] ? Number(m[2]) : 0;
  const month = mi >= 1 && mi <= 12 ? MONTHS[mi - 1] : null;
  return month ? `${month} ${m[1]}` : m[1];
}

export function dateRange(
  started: string | null,
  ended: string | null,
): string {
  const from = formatMonthYear(started) || "?";
  const to = ended ? formatMonthYear(ended) : "present";
  return `${from} – ${to}`;
}

/** One ordered field, one meaning: anything above `none` is a formal qualification. */
export const isDegree = (e: EducationRow) => e.degree_level > 0;

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
      const base = head
        ? `${head} @ ${e.institution} (${w})`
        : `${e.institution} (${w})`;
      return isDegree(e) ? base : `${base} — no degree`;
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

export function togglePin(
  content: CvContent,
  section: string,
  index: number,
): CvContent {
  const list = content[section] ?? [];
  if (index < 0 || index >= list.length) return content;
  const next = list.map((e, i) =>
    i === index ? { ...e, pinned: !e.pinned } : e,
  );
  return { ...content, [section]: next };
}

/** The user's per-entry detail override. `entryDetail` reads this before the fit's
 *  demotion set, so a stored value beats whatever the page fit would have preferred —
 *  which is exactly why the fit never writes this field itself. */
export function setDetail(
  content: CvContent,
  section: string,
  index: number,
  detail: "full" | "compact",
): CvContent {
  const list = content[section] ?? [];
  if (index < 0 || index >= list.length) return content;
  const next = list.map((e, i) => (i === index ? { ...e, detail } : e));
  return { ...content, [section]: next };
}

export function pinnedIds(content: CvContent): string[] {
  const out: string[] = [];
  for (const list of Object.values(content)) {
    for (const e of list) {
      if (e.pinned && !out.includes(e.id)) out.push(e.id);
    }
  }
  return out;
}

export function mergePinned(current: CvContent, next: CvContent): CvContent {
  const out: CvContent = {};
  for (const [section, list] of Object.entries(next)) out[section] = [...list];
  for (const [section, list] of Object.entries(current)) {
    const pinned = list.filter((e) => e.pinned);
    if (pinned.length === 0) continue;
    const target = out[section] ? [...out[section]] : [];
    for (const pin of pinned) {
      const i = target.findIndex((e) => e.id === pin.id);
      if (i >= 0) target[i] = { ...target[i], pinned: true };
      else {
        const { warning: _stale, ...keep } = pin;
        target.push({ ...keep });
      }
    }
    out[section] = target;
  }
  return out;
}

/** Deselected entries stripped, and switched-off sections dropped whole — the shape the
 *  render/export pipeline consumes. Both are "not on the CV", but for different reasons:
 *  see JobApplication.sections_off (a section that is off was cut on purpose, so it is
 *  absent from the machine layer too, rather than filed under cut_for_space). */
export function activeContent(
  content: CvContent,
  sectionsOff: string[] = [],
): CvContent {
  const off = new Set(sectionsOff);
  const out: CvContent = {};
  for (const [section, list] of Object.entries(content)) {
    if (off.has(section)) continue;
    out[section] = list.filter((e) => !e.deselected);
  }
  return out;
}

/** Immutable toggle for the editor's section switch. */
export function toggleSection(
  sectionsOff: string[],
  section: string,
): string[] {
  return sectionsOff.includes(section)
    ? sectionsOff.filter((s) => s !== section)
    : [...sectionsOff, section];
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
      {
        id,
        label: labelFor(section, row),
        relevance_score: null,
        pinned: true,
      },
    ],
  };
}
