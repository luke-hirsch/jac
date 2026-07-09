import { describe, it, expect } from "vitest";
import {
  SECTION_ORDER,
  activeContent,
  addEntry,
  dateRange,
  entryId,
  fromCareerDb,
  joinEntry,
  labelFor,
  missingEntries,
  moveEntry,
  parseEntryId,
  removeEntry,
  toggleDeselect,
  type CvContent,
} from "@/lib/cv-doc";
import type { CvEntriesResponse, EducationRow, JobRow } from "@/lib/queries/jac";

/**
 * Pure cv_content editing logic (guide [frontend]-cv-editor): "<singular>:<pk>" ids join
 * against the career DB, labels mirror the backend labelers (jac/generation_result.py),
 * and every edit operation is immutable. Entry order is the rank — guide 4's fit relies
 * on it, so move/remove must never reorder anything else.
 */

const job = {
  id: 12,
  title: "Senior Dev",
  company: "ACME",
  started: "2021-01-01",
  ended: null,
} as JobRow;

const education = {
  id: 3,
  institution: "TU",
  degree: "BSc",
  field_of_study: "CS",
  started: "2015-09-01",
  ended: "2018-08-31",
} as EducationRow;

const db = {
  skills: [
    { id: 1, name: "Python", proficiency: "expert", category: "technical" },
  ],
  jobs: [job],
  educations: [education],
  certifications: [],
  projects: [],
  languages: [],
} as unknown as CvEntriesResponse;

function content(): CvContent {
  return {
    jobs: [
      { id: "job:12", label: "stored label", relevance_score: 0.9 },
      { id: "job:99", label: "a job deleted from the DB", relevance_score: 0.5 },
    ],
    skills: [
      { id: "skill:1", label: "Python (expert, technical)", relevance_score: null },
    ],
  };
}

describe("parseEntryId / entryId", () => {
  it("splits '<type>:<pk>'", () => {
    expect(parseEntryId("job:12")).toEqual({ type: "job", pk: 12 });
  });

  it("returns null for garbage", () => {
    expect(parseEntryId("nope")).toBeNull();
    expect(parseEntryId("job:")).toBeNull();
  });

  it("entryId uses the singular section prefix", () => {
    expect(entryId("educations", 3)).toBe("education:3");
    expect(entryId("jobs", 12)).toBe("job:12");
  });
});

describe("labelFor / dateRange", () => {
  it("labels a job like the backend labeler", () => {
    expect(labelFor("jobs", job)).toBe("Senior Dev at ACME (2021-01-01–present)");
  });

  it("labels an education with degree + field head", () => {
    expect(labelFor("educations", education)).toBe(
      "BSc CS @ TU (2015-09-01–2018-08-31)",
    );
  });

  it("dateRange falls back to '?' and 'present'", () => {
    expect(dateRange(null, null)).toBe("?–present");
  });
});

describe("joinEntry", () => {
  it("resolves an id to its career-DB row", () => {
    expect(joinEntry(db, "jobs", content().jobs[0])).toBe(job);
  });

  it("returns null for a row deleted from the DB", () => {
    expect(joinEntry(db, "jobs", content().jobs[1])).toBeNull();
  });

  it("returns null while the DB has not loaded", () => {
    expect(joinEntry(undefined, "jobs", content().jobs[0])).toBeNull();
  });
});

describe("fromCareerDb", () => {
  it("builds every section in SECTION_ORDER with null scores", () => {
    const built = fromCareerDb(db);
    expect(Object.keys(built)).toEqual([...SECTION_ORDER]);
    expect(built.jobs).toEqual([
      {
        id: "job:12",
        label: "Senior Dev at ACME (2021-01-01–present)",
        relevance_score: null,
      },
    ]);
    expect(built.projects).toEqual([]);
  });
});

describe("moveEntry", () => {
  it("swaps neighbours", () => {
    const moved = moveEntry(content(), "jobs", 0, 1);
    expect(moved.jobs.map((e) => e.id)).toEqual(["job:99", "job:12"]);
  });

  it("clamps at the edges (returns the same object)", () => {
    const c = content();
    expect(moveEntry(c, "jobs", 0, -1)).toBe(c);
    expect(moveEntry(c, "jobs", 1, 1)).toBe(c);
  });

  it("does not mutate the input", () => {
    const c = content();
    moveEntry(c, "jobs", 0, 1);
    expect(c.jobs.map((e) => e.id)).toEqual(["job:12", "job:99"]);
  });
});

describe("removeEntry", () => {
  it("drops exactly the indexed entry", () => {
    const removed = removeEntry(content(), "jobs", 0);
    expect(removed.jobs.map((e) => e.id)).toEqual(["job:99"]);
    expect(removed.skills).toHaveLength(1); // other sections untouched
  });

  it("ignores out-of-range indices", () => {
    const c = content();
    expect(removeEntry(c, "jobs", 5)).toBe(c);
  });
});

describe("toggleDeselect", () => {
  it("round-trips the flag without touching neighbours", () => {
    const once = toggleDeselect(content(), "jobs", 1);
    expect(once.jobs[1].deselected).toBe(true);
    expect(once.jobs[0].deselected).toBeUndefined();

    const twice = toggleDeselect(once, "jobs", 1);
    expect(twice.jobs[1].deselected).toBe(false);
  });
});

describe("activeContent", () => {
  it("strips deselected entries only", () => {
    const c = toggleDeselect(content(), "jobs", 0);
    const active = activeContent(c);
    expect(active.jobs.map((e) => e.id)).toEqual(["job:99"]);
    expect(active.skills).toHaveLength(1);
  });
});

describe("missingEntries", () => {
  it("lists exactly the career-DB rows the section does not reference", () => {
    // content() references skill:1, the only skill in the DB → nothing to add…
    expect(missingEntries(db, "skills", content().skills)).toEqual([]);
    // …but with an empty selection the row becomes addable.
    expect(missingEntries(db, "skills", []).map((r) => r.id)).toEqual([1]);
    // job:12 is referenced; job:99 is not a DB row, so it can't make anything "missing".
    expect(missingEntries(db, "jobs", content().jobs)).toEqual([]);
  });

  it("is empty while the DB has not loaded", () => {
    expect(missingEntries(undefined, "skills", [])).toEqual([]);
  });
});

describe("addEntry", () => {
  it("appends at the tail with a built label and no score", () => {
    const c = removeEntry(content(), "jobs", 0); // drop job:12, then re-add it
    const added = addEntry(c, "jobs", job);
    expect(added.jobs.map((e) => e.id)).toEqual(["job:99", "job:12"]);
    expect(added.jobs[1]).toEqual({
      id: "job:12",
      label: "Senior Dev at ACME (2021-01-01–present)",
      relevance_score: null,
    });
  });

  it("creates the section when absent", () => {
    const added = addEntry({}, "educations", education);
    expect(added.educations.map((e) => e.id)).toEqual(["education:3"]);
  });

  it("refuses duplicates (returns the same object)", () => {
    const c = content();
    expect(addEntry(c, "jobs", job)).toBe(c); // job:12 already referenced
  });
});
