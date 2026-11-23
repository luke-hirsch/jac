import { describe, expect, it, vi } from "vitest";
import type { QueryClient } from "@tanstack/react-query";
import {
  hasSampleEntries,
  invalidateAfterDomainWrite,
  invalidateResource,
  isCareerEmpty,
  type CvEntriesResponse,
  type DomainWrite,
  type JobRow,
  type ResourceKey,
} from "@/lib/queries/jac";

describe("invalidateResource", () => {
  it.each<ResourceKey>(["jobs", "skills", "education"])(
    "a write to %s reaches its own lists and the cv-entries dump",
    async (resource) => {
      const invalidateQueries = vi.fn().mockResolvedValue(undefined);
      await invalidateResource(
        { invalidateQueries } as unknown as QueryClient,
        resource,
      );
      const keys = invalidateQueries.mock.calls.map(([f]) => f.queryKey);
      expect(keys).toContainEqual(["jac", resource]);
      expect(keys).toContainEqual(["jac", "cv-entries"]);
    },
  );

  it.each<[ResourceKey, ResourceKey]>([
    ["skills", "certifications"],
    ["certifications", "skills"],
  ])(
    "a write to %s reaches %s too: both list the one link between them",
    async (resource, other) => {
      const invalidateQueries = vi.fn().mockResolvedValue(undefined);
      await invalidateResource(
        { invalidateQueries } as unknown as QueryClient,
        resource,
      );
      const keys = invalidateQueries.mock.calls.map(([f]) => f.queryKey);
      expect(keys).toContainEqual(["jac", other]);
    },
  );

  it("a write to jobs leaves the skills lists alone", async () => {
    const invalidateQueries = vi.fn().mockResolvedValue(undefined);
    await invalidateResource(
      { invalidateQueries } as unknown as QueryClient,
      "jobs",
    );
    const keys = invalidateQueries.mock.calls.map(([f]) => f.queryKey);
    expect(keys).not.toContainEqual(["jac", "skills"]);
  });
});

describe("invalidateAfterDomainWrite", () => {
  async function invalidated(write: DomainWrite) {
    const invalidateQueries = vi.fn().mockResolvedValue(undefined);
    await invalidateAfterDomainWrite(
      { invalidateQueries } as unknown as QueryClient,
      write,
    );
    return invalidateQueries.mock.calls.map(([f]) => f.queryKey);
  }
  const TAGGED = [
    ["jac", "jobs"],
    ["jac", "projects"],
    ["jac", "education"],
    ["jac", "certifications"],
    ["jac", "skills"],
    ["jac", "cv-entries"],
    ["portfolio", "blocks"],
  ];

  it.each<DomainWrite>(["merge", "delete"])(
    "a %s reaches every row that holds domain ids",
    async (write) => {
      const keys = await invalidated(write);
      expect(keys).toContainEqual(["jac", "domains"]);
      for (const key of TAGGED) expect(keys).toContainEqual(key);
    },
  );

  it("a rename leaves the entries alone: they hold ids, not names", async () => {
    const keys = await invalidated("rename");
    expect(keys).toContainEqual(["jac", "domains"]);
    for (const key of TAGGED) expect(keys).not.toContainEqual(key);
  });

  it("a rename or a merge reaches the portfolio links, which scope by name", async () => {
    const links = ["portfolio", "links"];
    expect(await invalidated("rename")).toContainEqual(links);
    expect(await invalidated("merge")).toContainEqual(links);
    expect(await invalidated("delete")).not.toContainEqual(links);
  });
});

const EMPTY: CvEntriesResponse = {
  skills: [],
  jobs: [],
  educations: [],
  certifications: [],
  projects: [],
  languages: [],
};

const job = (company: string) => ({ company }) as JobRow;

describe("isCareerEmpty", () => {
  it("is empty only with nothing in any section", () => {
    expect(isCareerEmpty(EMPTY)).toBe(true);
    expect(isCareerEmpty({ ...EMPTY, jobs: [job("Acme")] })).toBe(false);
  });

  it("counts a career of only languages as a career: the backend's 409 does too", () => {
    const languages = [{ name: "French" }] as CvEntriesResponse["languages"];
    expect(isCareerEmpty({ ...EMPTY, languages })).toBe(false);
  });
});

describe("hasSampleEntries", () => {
  it("finds the marker on a job, a project or an education", () => {
    expect(
      hasSampleEntries({ ...EMPTY, jobs: [job("Muster GmbH (sample)")] }),
    ).toBe(true);
    expect(
      hasSampleEntries({
        ...EMPTY,
        projects: [{ name: "Dashboard (sample)" }],
      } as CvEntriesResponse),
    ).toBe(true);
    expect(
      hasSampleEntries({
        ...EMPTY,
        educations: [{ institution: "Hochschule (sample)" }],
      } as CvEntriesResponse),
    ).toBe(true);
  });

  it("is false for a real career", () => {
    expect(hasSampleEntries({ ...EMPTY, jobs: [job("Acme")] })).toBe(false);
    expect(hasSampleEntries(EMPTY)).toBe(false);
  });
});
