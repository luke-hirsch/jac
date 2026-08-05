import { describe, it, expect } from "vitest";
import {
  SECTION_ORDER,
  activeContent,
  addEntry,
  dateRange,
  entryId,
  formatMonthYear,
  fromCareerDb,
  joinEntry,
  labelFor,
  mergePinned,
  missingEntries,
  moveEntry,
  parseEntryId,
  pinnedIds,
  removeEntry,
  toggleDeselect,
  togglePin,
  toggleSection,
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
    expect(labelFor("jobs", job)).toBe("Senior Dev at ACME (Jan 2021 – present)");
  });

  it("labels an education with degree + field head", () => {
    expect(labelFor("educations", education)).toBe(
      "BSc CS @ TU (Sep 2015 – Aug 2018)",
    );
  });

  it("dateRange falls back to '?' and 'present'", () => {
    expect(dateRange(null, null)).toBe("? – present");
  });

  it("dateRange formats ISO endpoints as 'Mon yyyy' with a spaced dash", () => {
    expect(dateRange("2020-01-15", "2023-06-30")).toBe("Jan 2020 – Jun 2023");
  });
});

describe("formatMonthYear", () => {
  it("renders 'Mon yyyy' from a full ISO date", () => {
    expect(formatMonthYear("2020-03-09")).toBe("Mar 2020");
  });

  it("renders a bare year when there is no month", () => {
    expect(formatMonthYear("2020")).toBe("2020");
  });

  it("returns '' for null/empty and passes unknown shapes through", () => {
    expect(formatMonthYear(null)).toBe("");
    expect(formatMonthYear("")).toBe("");
    expect(formatMonthYear("someday")).toBe("someday");
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
        label: "Senior Dev at ACME (Jan 2021 – present)",
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

describe("togglePin", () => {
  it("round-trips the flag without touching neighbours", () => {
    const once = togglePin(content(), "jobs", 0);
    expect(once.jobs[0].pinned).toBe(true);
    expect(once.jobs[1].pinned).toBeUndefined();

    const twice = togglePin(once, "jobs", 0);
    expect(twice.jobs[0].pinned).toBe(false);
  });

  it("ignores out-of-range indices", () => {
    const c = content();
    expect(togglePin(c, "jobs", 5)).toBe(c);
  });
});

describe("mergePinned", () => {
  it("re-flags pinned entries the new run also selected (run's rank/score wins)", () => {
    const current = togglePin(content(), "jobs", 0); // pin job:12
    const next: CvContent = {
      jobs: [{ id: "job:12", label: "fresh label", relevance_score: 0.7 }],
    };
    const merged = mergePinned(current, next);
    expect(merged.jobs).toEqual([
      { id: "job:12", label: "fresh label", relevance_score: 0.7, pinned: true },
    ]);
  });

  it("re-appends pinned entries the new run dropped, at the section tail", () => {
    const current = togglePin(content(), "jobs", 1); // pin job:99
    const next: CvContent = {
      jobs: [{ id: "job:12", label: "kept", relevance_score: 0.9 }],
    };
    const merged = mergePinned(current, next);
    expect(merged.jobs.map((e) => e.id)).toEqual(["job:12", "job:99"]);
    expect(merged.jobs[1].pinned).toBe(true);
    // unpinned entries from the old content do NOT come along
    expect(merged.skills ?? []).toEqual([]);
  });

  it("keeps pins from sections the run returned nothing for", () => {
    const current = togglePin(content(), "skills", 0);
    const merged = mergePinned(current, { jobs: [] });
    expect(merged.skills.map((e) => e.id)).toEqual(["skill:1"]);
    expect(merged.skills[0].pinned).toBe(true);
  });

  it("passes the run result through untouched when nothing is pinned", () => {
    const next: CvContent = {
      jobs: [{ id: "job:1", label: "x", relevance_score: null }],
    };
    expect(mergePinned(content(), next)).toEqual(next);
  });

  it("strips a stale warning from a tail-appended pin", () => {
    const current: CvContent = {
      jobs: [
        {
          id: "job:99",
          label: "old",
          relevance_score: null,
          pinned: true,
          warning: "pinned by you — an old run would have dropped this",
        },
      ],
    };
    const merged = mergePinned(current, { jobs: [] });
    expect(merged.jobs[0].pinned).toBe(true);
    expect(merged.jobs[0].warning).toBeUndefined();
  });

  it("keeps the incoming run's fresh warning on a re-selected pin", () => {
    const current: CvContent = {
      jobs: [{ id: "job:1", label: "x", relevance_score: null, pinned: true }],
    };
    const next: CvContent = {
      jobs: [
        {
          id: "job:1",
          label: "x",
          relevance_score: 0.1,
          pinned: true,
          warning: "pinned by you — the high-mode selection would have dropped this entry",
        },
      ],
    };
    const merged = mergePinned(current, next);
    expect(merged.jobs[0].warning).toContain("high-mode");
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
  it("appends at the tail with a built label, no score, pinned by hand", () => {
    const c = removeEntry(content(), "jobs", 0); // drop job:12, then re-add it
    const added = addEntry(c, "jobs", job);
    expect(added.jobs.map((e) => e.id)).toEqual(["job:99", "job:12"]);
    // A hand-added entry is pinned so the next run's selection can't silently drop it.
    expect(added.jobs[1]).toEqual({
      id: "job:12",
      label: "Senior Dev at ACME (Jan 2021 – present)",
      relevance_score: null,
      pinned: true,
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

/** The pin set the apply-run path (runToApplicationPatch) sends to the server as
 *  pinned_entries: collected across sections, de-duped, in document order, and a
 *  deselected-but-pinned entry still counts (the pin promises survival). */
describe("pinnedIds", () => {
  it("collects pinned ids across sections, de-duped, in document order", () => {
    const c: CvContent = {
      jobs: [
        { id: "job:1", label: "a", relevance_score: 0.9, pinned: true },
        { id: "job:2", label: "b", relevance_score: 0.5 },
      ],
      skills: [
        { id: "skill:1", label: "c", relevance_score: null, pinned: true },
        { id: "job:1", label: "dupe", relevance_score: null, pinned: true },
      ],
    };
    expect(pinnedIds(c)).toEqual(["job:1", "skill:1"]);
  });

  it("a deselected-but-pinned entry still counts (the pin promises survival)", () => {
    const c: CvContent = {
      jobs: [
        {
          id: "job:1",
          label: "a",
          relevance_score: null,
          pinned: true,
          deselected: true,
        },
      ],
    };
    expect(pinnedIds(c)).toEqual(["job:1"]);
  });

  it("an unpinned document yields []", () => {
    expect(pinnedIds(content())).toEqual([]);
    expect(pinnedIds({})).toEqual([]);
  });
});

/**
 * `[fullstack]-education-degree`. SKIP-MARKED — not the active guide.
 * **Step 0: delete the `.skip` below.**
 *
 * An unfinished study period must read as one honest line of coursework. Today the CV
 * says "Drop Out Education Physics" because that is what the free-text degree field
 * holds; with a `completed` flag the renderer composes the phrasing instead.
 */
describe.skip("labelFor — unfinished education", () => {
  const dropout = {
    id: 4,
    institution: "FU Berlin",
    degree: "",
    field_of_study: "Physics",
    started: "2016-10-01",
    ended: "2020-09-30",
    completed: false,
  } as unknown as EducationRow;

  it("marks it, rather than trusting whatever is in the degree field", () => {
    expect(labelFor("educations", dropout)).toBe(
      "Physics @ FU Berlin (Oct 2016 – Sep 2020) — no degree",
    );
  });

  it("leaves a completed degree untouched", () => {
    expect(labelFor("educations", { ...education, completed: true })).toBe(
      "BSc CS @ TU (Sep 2015 – Aug 2018)",
    );
  });
});

/**
 * `[fullstack]-cv-section-toggles`. SKIP-MARKED — not the active guide.
 * **Step 0: delete the `.skip` below.**
 *
 * A switched-off section is gone from the CV *on purpose* — different from a deselected
 * entry, and different again from an entry the page fit had to cut. It must not reach the
 * export at all, invisible-ink layer included.
 */
describe.skip("activeContent / toggleSection — whole sections", () => {
  const content: CvContent = {
    jobs: [
      { id: "job:1", label: "kept", relevance_score: null },
      { id: "job:2", label: "deselected", relevance_score: null, deselected: true },
    ],
    certifications: [{ id: "certification:7", label: "Udemy", relevance_score: null }],
  };

  it("keeps stripping deselected entries when nothing is switched off", () => {
    const out = activeContent(content, []);
    expect(out.jobs.map((e) => e.id)).toEqual(["job:1"]);
    expect(out.certifications).toHaveLength(1);
  });

  it("defaults to the old single-argument behaviour", () => {
    expect(Object.keys(activeContent(content))).toEqual(["jobs", "certifications"]);
  });

  it("drops a switched-off section whole, key and all", () => {
    const out = activeContent(content, ["certifications"]);
    expect(out).not.toHaveProperty("certifications");
    expect(out.jobs.map((e) => e.id)).toEqual(["job:1"]);
  });

  it("toggleSection adds and removes without mutating", () => {
    const off: string[] = [];
    const on1 = toggleSection(off, "languages");
    expect(on1).toEqual(["languages"]);
    expect(off).toEqual([]);
    expect(toggleSection(on1, "languages")).toEqual([]);
  });
});
