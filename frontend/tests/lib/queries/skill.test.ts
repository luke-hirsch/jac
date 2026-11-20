import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  envLines,
  errorLines,
  fetchSkillBundle,
  parseImportFile,
  validationLines,
} from "@/lib/queries/skill";

/**
 * The Claude skill's SPA side (account → Claude, applications → Import from Claude): the
 * env lines for Claude Code, DRF errors as readable lines, the import file check, and the
 * bundle's raw fetch (`fetch` + `document.cookie` stubbed per test, node env).
 */

afterEach(() => vi.unstubAllGlobals());

describe("envLines", () => {
  it("gives the address and the key as two export lines", () => {
    expect(envLines("https://jac.example.org", "jac_AbCdEfGh123")).toBe(
      "export JAC_URL=https://jac.example.org\nexport JAC_TOKEN=jac_AbCdEfGh123",
    );
  });
});

describe("validationLines", () => {
  it("names the nested field a message belongs to", () => {
    expect(
      validationLines({
        posting: { text: ["This field is required."] },
        entries: ["Not yours: job:99"],
      }),
    ).toEqual([
      "posting.text: This field is required.",
      "entries: Not yours: job:99",
    ]);
  });

  it("reads detail and non_field_errors on their own", () => {
    expect(validationLines({ detail: "At most 10 tokens." })).toEqual([
      "At most 10 tokens.",
    ]);
    expect(
      validationLines({ non_field_errors: ["Expected a dictionary."] }),
    ).toEqual(["Expected a dictionary."]);
  });

  it("numbers the items of a list, and skips the ones without errors", () => {
    expect(validationLines({ entries: { 2: ["Too long."] } })).toEqual([
      "entries.2: Too long.",
    ]);
    expect(validationLines({ items: [{}, { name: ["Required."] }] })).toEqual([
      "items.1.name: Required.",
    ]);
  });

  it("gives every message of a field its own line", () => {
    expect(
      validationLines({ letter: { body: ["Required.", "Blank."] } }),
    ).toEqual(["letter.body: Required.", "letter.body: Blank."]);
  });

  it("finds nothing in what isn't an error body", () => {
    expect(validationLines({})).toEqual([]);
    expect(validationLines(null)).toEqual([]);
    expect(validationLines({ count: 3 })).toEqual([]);
  });
});

describe("errorLines", () => {
  it("reads a DRF body", () => {
    const e = new ApiError(400, { posting: { text: ["Required."] } });
    expect(errorLines(e, "fallback")).toEqual(["posting.text: Required."]);
  });

  it("falls back when the body says nothing readable", () => {
    expect(errorLines(new ApiError(500, "<html>…</html>"), "fallback")).toEqual(
      ["fallback"],
    );
    expect(errorLines(new ApiError(400, {}), "fallback")).toEqual(["fallback"]);
    expect(errorLines(new TypeError("Failed to fetch"), "fallback")).toEqual([
      "fallback",
    ]);
  });
});

describe("parseImportFile", () => {
  it("returns the object as it is", () => {
    const file = { posting: { text: "Ad" }, entries: ["job:3"], letter: {} };
    expect(parseImportFile(JSON.stringify(file))).toEqual(file);
  });

  it("says so when the file isn't JSON", () => {
    expect(() => parseImportFile("{ not json")).toThrow(/isn't valid JSON/);
  });

  it("says so when the JSON isn't an object", () => {
    for (const text of ["[]", "null", "42", '"text"'])
      expect(() => parseImportFile(text)).toThrow(/isn't an application/);
  });
});

describe("fetchSkillBundle", () => {
  function fakeResponse(status: number, contentType: string, body: unknown) {
    return {
      ok: status >= 200 && status < 300,
      status,
      headers: {
        get: (h: string) => (h === "content-type" ? contentType : null),
      },
      json: async () => body,
      blob: async () => body,
    } as unknown as Response;
  }

  it("POSTs with the CSRF header and returns the zip", async () => {
    const zip = new Blob(["PK"], { type: "application/zip" });
    const fetchMock = vi.fn(async () =>
      fakeResponse(200, "application/zip", zip),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("document", { cookie: "csrftoken=tok123" });

    await expect(fetchSkillBundle()).resolves.toBe(zip);
    expect(fetchMock).toHaveBeenCalledWith("/api/jac/skill/bundle/", {
      method: "POST",
      headers: { "X-CSRFToken": "tok123" },
      credentials: "same-origin",
    });
  });

  it("throws the server's JSON at the token limit", async () => {
    const detail = { detail: "At most 10 tokens: revoke one first." };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => fakeResponse(400, "application/json", detail)),
    );
    vi.stubGlobal("document", { cookie: "" });

    const error = await fetchSkillBundle().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(400);
    expect((error as ApiError).data).toEqual(detail);
  });

  it("throws without a body when the error isn't JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => fakeResponse(502, "text/html", "<html>")),
    );
    vi.stubGlobal("document", { cookie: "" });

    const error = await fetchSkillBundle().catch((e: unknown) => e);
    expect((error as ApiError).status).toBe(502);
    expect((error as ApiError).data).toBeNull();
  });
});
