import { describe, expect, it, vi } from "vitest";
import type { QueryClient } from "@tanstack/react-query";
import {
  hasSampleEntries,
  invalidateResource,
  isCareerEmpty,
  type CvEntriesResponse,
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
