import { describe, expect, it, vi } from "vitest";

// The endpoints are the boundary: mock `api`, keep the rest of the module real.
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  api: vi.fn(),
}));

import { api } from "@/lib/api";
import {
  exportFileName,
  fetchExport,
  importedSummary,
  postImport,
} from "@/lib/queries/transfer";

describe("exportFileName", () => {
  it("names the file by kind and day, zero-padded", () => {
    const day = new Date(2026, 0, 5, 12);
    expect(exportFileName("career", day)).toBe("career-2026-01-05.json");
    expect(exportFileName("applications", day)).toBe(
      "applications-2026-01-05.json",
    );
  });

  it("uses the local day: just after midnight in Berlin, UTC is still yesterday", () => {
    expect(exportFileName("career", new Date(2026, 10, 20, 0, 30))).toBe(
      "career-2026-11-20.json",
    );
  });
});

describe("importedSummary", () => {
  it("counts career entries, not the domains and locations they point at", () => {
    const created = {
      domains: 4,
      locations: 2,
      certifications: 0,
      skills: 8,
      jobs: 2,
      projects: 1,
      educations: 1,
      languages: 2,
    };
    expect(importedSummary("career", created)).toBe(
      "14 career entries imported",
    );
    expect(importedSummary("career", { jobs: 1 })).toBe(
      "1 career entry imported",
    );
  });

  it("counts applications, not their postings", () => {
    expect(
      importedSummary("applications", { postings: 1, applications: 2 }),
    ).toBe("2 applications imported");
    expect(importedSummary("applications", { applications: 1 })).toBe(
      "1 application imported",
    );
  });
});

describe("the endpoints", () => {
  it("exports each kind from its own place", async () => {
    vi.mocked(api).mockResolvedValue({});
    await fetchExport("career");
    await fetchExport("applications");
    expect(vi.mocked(api).mock.calls.map(([path]) => path)).toEqual([
      "/api/jac/career/export/",
      "/api/jac/applications/export/",
    ]);
  });

  it("uploads the file as multipart `file`, not as a JSON body", async () => {
    vi.mocked(api).mockReset().mockResolvedValue({ created: {}, warnings: [] });
    const file = new File(["{}"], "applications-2026-11-20.json", {
      type: "application/json",
    });
    await postImport("applications", file);
    const [path, init] = vi.mocked(api).mock.calls[0];
    expect(path).toBe("/api/jac/applications/import/");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeInstanceOf(FormData);
    expect((init?.body as FormData).get("file")).toBeInstanceOf(File);
  });
});
