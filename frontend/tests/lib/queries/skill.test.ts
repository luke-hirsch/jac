import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  changeRows,
  connectorHeader,
  connectorUrl,
  createAccessToken,
  errorLines,
  expandedBatch,
  fetchSkillBundle,
  keepCareerChange,
  setTokenPortfolio,
  staleLines,
  validationLines,
  type CareerChange,
} from "@/lib/queries/skill";

/**
 * The connector's SPA side (Account → LLM, under Connectors): its URL and header, DRF errors
 * as readable lines, a career change as display rows, the 409's stale entries by name, which
 * batch the log starts expanded, keeping a batch, a token's portfolio switch, and the bundle's
 * raw fetch (`fetch` + `document.cookie` stubbed per test, node env).
 */

afterEach(() => vi.unstubAllGlobals());

describe("connectorUrl", () => {
  it("is the API's MCP endpoint, with a trailing slash", () => {
    expect(connectorUrl("https://jac.example.org")).toBe(
      "https://jac.example.org/api/jac/mcp/",
    );
  });

  it("carries the key as its last segment when given one", () => {
    expect(connectorUrl("https://jac.example.org", "jac_AbC-d_1")).toBe(
      "https://jac.example.org/api/jac/mcp/jac_AbC-d_1/",
    );
  });
});

describe("connectorHeader", () => {
  it("includes the scheme: Claude sends the value as it is", () => {
    expect(connectorHeader("jac_AbC")).toBe("Bearer jac_AbC");
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

describe("changeRows", () => {
  const change = (
    before: CareerChange["before"],
    after: CareerChange["after"],
  ): CareerChange => ({
    id: "job:3",
    label: "Dev at Acme",
    why: "",
    before,
    after,
  });

  it("gives a value before and after, multi-line text as it is", () => {
    expect(
      changeRows(
        change(
          { description: "Old.", current: false, hours: 20 },
          { description: "New.\nTwo lines.", current: true, hours: 40 },
        ),
        {},
      ),
    ).toEqual([
      {
        kind: "value",
        field: "description",
        before: "Old.",
        after: "New.\nTwo lines.",
      },
      { kind: "value", field: "current", before: "no", after: "yes" },
      { kind: "value", field: "hours", before: "20", after: "40" },
    ]);
  });

  it("reads an empty value as null", () => {
    expect(
      changeRows(
        change({ summary: "", end: "2024" }, { summary: "Text", end: null }),
        {},
      ),
    ).toEqual([
      { kind: "value", field: "summary", before: null, after: "Text" },
      { kind: "value", field: "end", before: "2024", after: null },
    ]);
  });

  it("gives a relation list's additions and removals, ids by label", () => {
    const labels = { "skill:12": "Docker", "skill:5": "Python" };
    expect(
      changeRows(
        change(
          { skills: ["skill:12", "skill:5"], domains: ["Backend"] },
          { skills: ["skill:5", "skill:44"], domains: ["Backend", "Cloud"] },
        ),
        labels,
      ),
    ).toEqual([
      // skill:44 has no label: its id stands in.
      {
        kind: "list",
        field: "skills",
        added: ["skill:44"],
        removed: ["Docker"],
      },
      { kind: "list", field: "domains", added: ["Cloud"], removed: [] },
    ]);
  });

  it("shows a new entry's fields as added, without the empty ones", () => {
    expect(
      changeRows(
        change(null, {
          name: "Docker",
          description: "",
          domains: ["DevOps"],
          builds_on: [],
        }),
        {},
      ),
    ).toEqual([
      { kind: "value", field: "name", before: null, after: "Docker" },
      { kind: "list", field: "domains", added: ["DevOps"], removed: [] },
    ]);
  });

  it("shows a removed entry's fields as they were", () => {
    expect(changeRows(change({ name: "Docker" }, null), {})).toEqual([
      { kind: "value", field: "name", before: "Docker", after: null },
    ]);
  });

  it("leaves out fields that read the same, and spells out field names", () => {
    expect(
      changeRows(
        change(
          { skills: ["skill:5", "skill:12"], title: "Dev", builds_on: null },
          {
            skills: ["skill:12", "skill:5"],
            title: "Dev",
            builds_on: "skill:12",
          },
        ),
        { "skill:12": "Docker" },
      ),
    ).toEqual([
      { kind: "value", field: "builds on", before: null, after: "Docker" },
    ]);
  });
});

describe("staleLines", () => {
  const stale = new ApiError(409, {
    detail: "Edited in jac since.",
    stale: ["job:3", "skill:12", "project:9"],
  });

  it("names the stale entries by the batch, else by id", () => {
    const batch = {
      changes: [
        { id: "job:3", label: "Dev at Acme", why: "", before: {}, after: {} },
      ],
      labels: { "skill:12": "Docker" },
    };
    expect(staleLines(stale, "fallback", batch)).toEqual([
      "Edited in jac since.",
      "Dev at Acme",
      "Docker",
      "project:9",
    ]);
  });

  it("gives the ids as they are without a batch or labels", () => {
    expect(staleLines(stale, "fallback")).toEqual([
      "Edited in jac since.",
      "job:3",
      "skill:12",
      "project:9",
    ]);
  });

  it("names them by the 409's own labels on an import", () => {
    const answer = new ApiError(409, {
      detail: "Changed in jac since Claude read them.",
      stale: ["job:3", "job:4"],
      labels: { "job:3": "Dev at Acme" },
    });
    expect(staleLines(answer, "fallback")).toEqual([
      "Changed in jac since Claude read them.",
      "Dev at Acme",
      "job:4",
    ]);
  });

  it("reads any other failure as errorLines", () => {
    expect(
      staleLines(new ApiError(409, { detail: "Already undone." }), "fallback"),
    ).toEqual(["Already undone."]);
    expect(
      staleLines(
        new ApiError(400, { changes: { 2: { description: ["Too long."] } } }),
        "fallback",
      ),
    ).toEqual(["changes.2.description: Too long."]);
    expect(staleLines(new TypeError("Failed to fetch"), "fallback")).toEqual([
      "fallback",
    ]);
  });
});

describe("expandedBatch", () => {
  it("is the newest batch that isn't undone", () => {
    expect(
      expandedBatch([
        { id: 9, undone_at: "2026-11-23T10:00:00+00:00" },
        { id: 8, undone_at: null },
        { id: 7, undone_at: null },
      ]),
    ).toBe(8);
  });

  it("is none when every batch is undone, or there are none", () => {
    expect(
      expandedBatch([{ id: 9, undone_at: "2026-11-23T10:00:00+00:00" }]),
    ).toBeNull();
    expect(expandedBatch([])).toBeNull();
  });
});

describe("keepCareerChange", () => {
  function answer(status: number, body: unknown = "") {
    const isJson = typeof body !== "string";
    return {
      ok: status >= 200 && status < 300,
      status,
      headers: {
        get: (h: string) =>
          h === "content-type" && isJson ? "application/json" : null,
      },
      json: async () => body,
      text: async () => body,
    } as unknown as Response;
  }

  it("DELETEs the batch, with the CSRF token", async () => {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) =>
      answer(204),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("document", { cookie: "csrftoken=tok123" });

    await expect(keepCareerChange(7)).resolves.toBeUndefined();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/jac/skill/career/changes/7/");
    expect(init.method).toBe("DELETE");
    expect(new Headers(init.headers).get("X-CSRFToken")).toBe("tok123");
  });

  it("takes a 404 as kept: another tab was first", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => answer(404, { detail: "Not found." })),
    );
    vi.stubGlobal("document", { cookie: "" });

    await expect(keepCareerChange(7)).resolves.toBeUndefined();
  });

  it("throws any other failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => answer(429, { detail: "Request was throttled." })),
    );
    vi.stubGlobal("document", { cookie: "" });

    const error = await keepCareerChange(7).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(429);
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

  it("GETs the zip: the skill alone, so nothing is minted and nothing to guard", async () => {
    const zip = new Blob(["PK"], { type: "application/zip" });
    const fetchMock = vi.fn(async () =>
      fakeResponse(200, "application/zip", zip),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("document", { cookie: "csrftoken=tok123" });

    await expect(fetchSkillBundle()).resolves.toBe(zip);
    expect(fetchMock).toHaveBeenCalledWith("/api/jac/skill/bundle/", {
      credentials: "same-origin",
    });
  });

  it("throws the server's JSON", async () => {
    const detail = { detail: "Authentication credentials were not provided." };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => fakeResponse(403, "application/json", detail)),
    );
    vi.stubGlobal("document", { cookie: "" });

    const error = await fetchSkillBundle().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(403);
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

describe("a token's portfolio switch", () => {
  function answer(status: number, body: unknown) {
    return {
      ok: status >= 200 && status < 300,
      status,
      headers: {
        get: (h: string) => (h === "content-type" ? "application/json" : null),
      },
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as unknown as Response;
  }

  function stub(status: number, body: unknown = {}) {
    const fetchMock = vi.fn(async (_url: string, _init: RequestInit) =>
      answer(status, body),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("document", { cookie: "csrftoken=tok123" });
    return fetchMock;
  }

  it("goes with a new token: ticked or not, the server hears it", async () => {
    const fetchMock = stub(201, { id: 3, key: "jac_x" });
    for (const mayEditPortfolio of [false, true]) {
      await createAccessToken({
        name: "Claude",
        transferConsent: true,
        mayEditPortfolio,
      });
    }
    const bodies = fetchMock.mock.calls.map(([url, init]) => {
      expect(url).toBe("/api/jac/skill/tokens/");
      expect(init.method).toBe("POST");
      return JSON.parse(init.body as string);
    });
    expect(bodies).toEqual([
      { name: "Claude", transfer_consent: true, may_edit_portfolio: false },
      { name: "Claude", transfer_consent: true, may_edit_portfolio: true },
    ]);
  });

  it("PATCHes one token's switch, with the CSRF token and nothing else", async () => {
    const fetchMock = stub(200, { id: 7, may_edit_portfolio: true });

    await expect(setTokenPortfolio(7, true)).resolves.toEqual({
      id: 7,
      may_edit_portfolio: true,
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/jac/skill/tokens/7/");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({
      may_edit_portfolio: true,
    });
    expect(new Headers(init.headers).get("X-CSRFToken")).toBe("tok123");
  });

  it("throws what the server refused", async () => {
    stub(404, { detail: "No AccessToken matches the given query." });

    const error = await setTokenPortfolio(7, false).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
  });
});
