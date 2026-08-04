import { describe, expect, it } from "vitest";
import {
  candidates,
  filterCandidates,
  moveFeatured,
  pruneFeatured,
  resolveFeatured,
  toggleFeatured,
  toggleName,
} from "@/lib/portfolio/link-form";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import type { PortfolioBlockRow } from "@/lib/queries/portfolio";

/**
 * Guide [frontend]-portfolio-manage: the featured-picker list algebra — the whole
 * testable surface of the manual-link editor (dialogs/routes are click-through). Mixed
 * ids ("job:12", "block:7"), immutable ops, cv-doc grammar + labels. No React, no HTTP.
 */

const db = {
  skills: [
    {
      id: 5,
      name: "TypeScript",
      proficiency: "expert",
      category: "lang",
      domains: [2],
    },
  ],
  jobs: [
    {
      id: 12,
      title: "Senior Dev",
      company: "ACME",
      started: "2021-01-01",
      ended: null,
      domains: [1, 2],
    },
  ],
  educations: [],
  certifications: [],
  projects: [],
  languages: [],
} as unknown as CvEntriesResponse;

function block(over: Partial<PortfolioBlockRow>): PortfolioBlockRow {
  return {
    id: 7,
    kind: "text",
    title: "Award",
    body: "",
    image: null,
    alt_text: "",
    domains: [],
    favourite: false,
    order: 0,
    is_active: true,
    updated_at: "2026-07-27T00:00:00Z",
    ...over,
  };
}

const blocks = [
  block({ id: 7, title: "Award", kind: "text", domains: [1] }),
  block({ id: 8, title: "", kind: "image", domains: [2] }),
];

describe("candidates", () => {
  it("lists career entries in SECTION_ORDER, then blocks", () => {
    const ids = candidates(db, blocks).map((c) => c.id);
    // jobs before skills (SECTION_ORDER), blocks last.
    expect(ids).toEqual(["job:12", "skill:5", "block:7", "block:8"]);
  });

  it("labels career entries with cv-doc labelFor and tags their type", () => {
    const c = candidates(db, []);
    expect(c[0]).toMatchObject({ id: "job:12", type: "job" });
    expect(c[0].label).toContain("Senior Dev at ACME");
    expect(c[1]).toMatchObject({ id: "skill:5", type: "skill" });
  });

  it("falls back to '<kind> block' for an untitled block", () => {
    const [award, untitled] = candidates(undefined, blocks);
    expect(award).toMatchObject({ id: "block:7", type: "block", label: "Award" });
    expect(untitled.label).toBe("image block");
  });

  it("undefined db yields blocks only; empty everything yields []", () => {
    expect(candidates(undefined, blocks).map((c) => c.id)).toEqual([
      "block:7",
      "block:8",
    ]);
    expect(candidates(undefined, [])).toEqual([]);
  });

  it("attaches domainIds from the row/block tags", () => {
    const byId = new Map(candidates(db, blocks).map((c) => [c.id, c.domainIds]));
    expect(byId.get("job:12")).toEqual([1, 2]);
    expect(byId.get("skill:5")).toEqual([2]);
    expect(byId.get("block:7")).toEqual([1]);
    expect(byId.get("block:8")).toEqual([2]);
  });

  it("languages carry no domainIds (no domains M2M)", () => {
    const langDb = {
      skills: [],
      jobs: [],
      educations: [],
      certifications: [],
      projects: [],
      languages: [{ id: 3, name: "German", fluency: "native" }],
    } as unknown as CvEntriesResponse;
    const [lang] = candidates(langDb, []);
    expect(lang).toMatchObject({ id: "language:3", type: "language" });
    expect(lang.domainIds).toEqual([]);
  });
});

describe("resolveFeatured", () => {
  const pool = candidates(db, blocks);

  it("returns candidates in FEATURED order, not pool order", () => {
    expect(resolveFeatured(["block:7", "job:12"], pool).map((c) => c.id)).toEqual([
      "block:7",
      "job:12",
    ]);
  });

  it("drops ids no longer in the pool", () => {
    expect(resolveFeatured(["job:99", "skill:5"], pool).map((c) => c.id)).toEqual([
      "skill:5",
    ]);
  });

  it("empty featured resolves to []", () => {
    expect(resolveFeatured([], pool)).toEqual([]);
  });
});

describe("toggleFeatured", () => {
  it("appends an absent id at the tail", () => {
    expect(toggleFeatured(["a", "b"], "c")).toEqual(["a", "b", "c"]);
  });

  it("removes a present id, leaving others' order", () => {
    expect(toggleFeatured(["a", "b", "c"], "b")).toEqual(["a", "c"]);
  });
});

describe("moveFeatured", () => {
  it("swaps with the next neighbour", () => {
    expect(moveFeatured(["a", "b", "c"], 0, 1)).toEqual(["b", "a", "c"]);
  });

  it("no-ops at the boundaries and out of range", () => {
    expect(moveFeatured(["a", "b"], 0, -1)).toEqual(["a", "b"]);
    expect(moveFeatured(["a", "b"], 1, 1)).toEqual(["a", "b"]);
    expect(moveFeatured(["a", "b"], 5, 1)).toEqual(["a", "b"]);
  });

  it("returns a new array (immutability)", () => {
    const src = ["a", "b"];
    expect(moveFeatured(src, 0, 1)).not.toBe(src);
    expect(src).toEqual(["a", "b"]);
  });
});

describe("pruneFeatured", () => {
  it("drops ghosts and preserves surviving order", () => {
    const pool = candidates(db, blocks);
    expect(pruneFeatured(["job:12", "block:99", "skill:5"], pool)).toEqual([
      "job:12",
      "skill:5",
    ]);
  });
});

describe("toggleName", () => {
  it("adds an absent name and removes a present one", () => {
    expect(toggleName(["ai"], "music")).toEqual(["ai", "music"]);
    expect(toggleName(["ai", "music"], "ai")).toEqual(["music"]);
  });
});

describe("filterCandidates", () => {
  // pool: job:12 [1,2], skill:5 [2], block:7 [1], block:8 [2]
  const pool = candidates(db, blocks);
  const ids = (cs: ReturnType<typeof filterCandidates>) => cs.map((c) => c.id);

  it("no filters / empty filters is the identity", () => {
    expect(ids(filterCandidates(pool))).toEqual(ids(pool));
    expect(ids(filterCandidates(pool, { search: "", types: [], domainIds: [] }))).toEqual(
      ids(pool),
    );
  });

  it("search matches the label case-insensitively", () => {
    expect(ids(filterCandidates(pool, { search: "senior" }))).toEqual(["job:12"]);
    expect(ids(filterCandidates(pool, { search: "AWARD" }))).toEqual(["block:7"]);
    expect(filterCandidates(pool, { search: "nope" })).toEqual([]);
  });

  it("type facet keeps only the listed types", () => {
    expect(ids(filterCandidates(pool, { types: ["block"] }))).toEqual([
      "block:7",
      "block:8",
    ]);
    expect(ids(filterCandidates(pool, { types: ["job", "skill"] }))).toEqual([
      "job:12",
      "skill:5",
    ]);
  });

  it("domain facet is OR within, keeping any candidate carrying a selected domain", () => {
    expect(ids(filterCandidates(pool, { domainIds: [1] }))).toEqual([
      "job:12",
      "block:7",
    ]);
    expect(ids(filterCandidates(pool, { domainIds: [1, 2] }))).toEqual(ids(pool));
  });

  it("ANDs the three filters together", () => {
    // job type AND domain 2 → job:12 only (skill:5 excluded by type)
    expect(ids(filterCandidates(pool, { types: ["job"], domainIds: [2] }))).toEqual([
      "job:12",
    ]);
    // skill type AND domain 1 → skill has only domain 2 → empty
    expect(filterCandidates(pool, { types: ["skill"], domainIds: [1] })).toEqual([]);
  });
});
