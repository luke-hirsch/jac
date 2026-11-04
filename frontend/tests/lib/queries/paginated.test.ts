import { beforeEach, describe, expect, it, vi } from "vitest";

// `fetchAll` walks pages via `api`; mock that boundary so no real fetch runs.
vi.mock("@/lib/api", () => ({ api: vi.fn() }));

import { api } from "@/lib/api";
import { buildQuery, fetchAll } from "@/lib/queries/paginated";

describe("buildQuery", () => {
  it("returns an empty string when nothing is set", () => {
    expect(buildQuery({})).toBe("");
  });

  it("omits page 1 but includes later pages", () => {
    expect(buildQuery({ page: 1 })).toBe("");
    expect(buildQuery({ page: 3 })).toBe("?page=3");
  });

  it("serialises search/ordering/filters and skips blank or undefined filters", () => {
    expect(
      buildQuery({
        search: "go",
        ordering: "-name",
        filters: { domain: 2, status: "", tag: undefined },
      }),
    ).toBe("?search=go&ordering=-name&domain=2");
  });

  it("always leads with a single '?'", () => {
    expect(buildQuery({ search: "x" }).startsWith("?")).toBe(true);
  });
});

describe("fetchAll", () => {
  beforeEach(() => vi.mocked(api).mockReset());

  it("walks every page until `next` is null and flattens the results", async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({ count: 3, next: "x", previous: null, results: [1, 2] })
      .mockResolvedValueOnce({ count: 3, next: null, previous: "y", results: [3] });

    expect(await fetchAll<number>("/api/jac/skills/")).toEqual([1, 2, 3]);
    expect(vi.mocked(api)).toHaveBeenCalledTimes(2);
    // it owns the page cursor: page 1 has no param, then ?page=2
    expect(vi.mocked(api).mock.calls[0][0]).toBe("/api/jac/skills/");
    expect(vi.mocked(api).mock.calls[1][0]).toBe("/api/jac/skills/?page=2");
  });

  it("returns immediately on a single un-paginated page", async () => {
    vi.mocked(api).mockResolvedValueOnce({ count: 0, next: null, previous: null, results: [] });
    expect(await fetchAll("/api/jac/skills/")).toEqual([]);
    expect(vi.mocked(api)).toHaveBeenCalledTimes(1);
  });
});
