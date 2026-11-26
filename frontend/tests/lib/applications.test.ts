import { describe, it, expect } from "vitest";
import {
  APPLICATION_STATUSES,
  applicationsSearch,
  filterByStatus,
  parseStatusFilter,
  parseSort,
  portfolioLinkNote,
  runToApplicationPatch,
  searchApplications,
  sortApplications,
  statusCounts,
  toApplicationPayload,
  type ApplicationStatus,
} from "@/lib/queries/applications";
import type { TailoredResult } from "@/lib/queries/generations";

/**
 * Pure helpers behind the applications pages: pasted-posting→payload and the
 * "apply this run" PATCH body (cv JSON + body-only letter + letter_meta furniture,
 * nothing else — the user's status/layout choices must survive an apply).
 */

describe("toApplicationPayload", () => {
  it("trims the pasted posting text", () => {
    expect(toApplicationPayload("  we need a dev  ")).toEqual({
      posting_text: "we need a dev",
    });
  });
});

describe("runToApplicationPatch", () => {
  it("maps the run result onto cv_content + body-only letter + letter_meta + pins", () => {
    const result = {
      meta: { mode: "standard", provider: "ollama", model: "" },
      cv: {
        skills: [{ id: "skill:1", label: "Python", relevance_score: 0.9 }],
      },
      cover_letter: {
        language: "en",
        subject: "Application for Dev",
        salutation: "Dear team,",
        body: "I build things.",
        sender: { name: "Ada" },
        recipient: { company: "ACME" },
        date: "2026-07-09",
        closing: "Kind regards,",
        text: "…full furnished text (not what gets stored)…",
      },
    } as unknown as TailoredResult;

    const patch = runToApplicationPatch(result);
    expect(patch).toEqual({
      cv_content: result.cv,
      // The editable body is the letter body verbatim — never the furnished `text`.
      // Any personal paragraph is already woven into `body` by the backend.
      cover_letter: "I build things.",
      letter_meta: {
        language: "en",
        subject: "Application for Dev",
        salutation: "Dear team,",
        date: "2026-07-09",
        closing: "Kind regards,",
        sender: { name: "Ada" },
        recipient: { company: "ACME" },
        address_form: "",
      },
      // No entry carried a pin, so nothing is force-kept for the next run.
      pinned_entries: [],
    });
  });

  it("carries pinned entries from the current cv_content into the new result", () => {
    const result = {
      meta: { mode: "standard", provider: "ollama", model: "" },
      cv: { jobs: [{ id: "job:1", label: "kept", relevance_score: 0.8 }] },
      cover_letter: {
        language: "en",
        subject: "s",
        salutation: "Dear,",
        body: "b",
        sender: {},
        recipient: {},
        date: "2026-07-13",
        closing: "Regards,",
        personal_paragraph: "",
        text: "…",
        ai_share: 0,
      },
    } as unknown as TailoredResult;
    const current = {
      jobs: [
        { id: "job:1", label: "old", relevance_score: 0.5, pinned: true },
        { id: "job:2", label: "unpinned, dropped", relevance_score: 0.4 },
      ],
      skills: [
        { id: "skill:9", label: "Rust", relevance_score: null, pinned: true },
      ],
    };

    const patch = runToApplicationPatch(result, current);
    expect(patch.cv_content).toEqual({
      jobs: [
        { id: "job:1", label: "kept", relevance_score: 0.8, pinned: true },
      ],
      skills: [
        { id: "skill:9", label: "Rust", relevance_score: null, pinned: true },
      ],
    });
  });

  // The empty-pin case is asserted above (pinned_entries: []); here the non-empty
  // pin set derived from the merged doc must travel, or the next run force-keeps
  // nothing server-side.
  it("derives pinned_entries from the merged doc (non-empty pin set travels)", () => {
    const result = {
      meta: { mode: "standard", provider: "ollama", model: "" },
      cv: { jobs: [{ id: "job:1", label: "kept", relevance_score: 0.9 }] },
      cover_letter: {
        language: "en",
        subject: "s",
        salutation: "Dear,",
        body: "b",
        sender: {},
        recipient: {},
        date: "2026-07-18",
        closing: "Best,",
        personal_paragraph: "",
        text: "…",
        ai_share: 0,
      },
    } as unknown as TailoredResult;
    const current = {
      jobs: [
        { id: "job:99", label: "mine", relevance_score: null, pinned: true },
      ],
    };
    expect(runToApplicationPatch(result, current).pinned_entries).toEqual([
      "job:99",
    ]);
  });
});

/**
 * The list's status filter: `?status=` names one lifecycle status, anything else
 * means no filter. The options are the statuses the list holds, in lifecycle order.
 */
describe("parseStatusFilter", () => {
  it("keeps a known status", () => {
    for (const s of APPLICATION_STATUSES) expect(parseStatusFilter(s)).toBe(s);
  });

  it("drops anything else to no filter", () => {
    for (const raw of [undefined, null, "", "Sent", "bogus", 3, ["sent"]])
      expect(parseStatusFilter(raw)).toBeUndefined();
  });

  it("doesn't take an object's inherited keys for a status", () => {
    expect(parseStatusFilter("toString")).toBeUndefined();
    expect(parseStatusFilter("constructor")).toBeUndefined();
  });
});

describe("applicationsSearch", () => {
  it("reads the status out of the URL's search", () => {
    expect(applicationsSearch({ status: "sent" })).toEqual({ status: "sent" });
    expect(applicationsSearch({ status: "nope" }).status).toBeUndefined();
    expect(applicationsSearch({}).status).toBeUndefined();
  });

  it("reads the search text and the sort, dropping what it doesn't know", () => {
    expect(applicationsSearch({ q: "berlin ", sort: "title" })).toEqual({
      status: undefined,
      q: "berlin ", // kept as typed: trimming here would eat the space being typed
      sort: "title",
    });
    expect(applicationsSearch({ q: "", sort: "nope" })).toEqual({
      status: undefined,
      q: undefined,
      sort: undefined,
    });
  });

  it("takes a search the router parsed as a number back as text", () => {
    expect(applicationsSearch({ q: 2026 }).q).toBe("2026");
  });
});

describe("parseSort", () => {
  it("knows the four orders and nothing else", () => {
    expect(parseSort("oldest")).toBe("oldest");
    expect(parseSort("deadline")).toBe("deadline");
    expect(parseSort("newest")).toBeUndefined(); // the default: kept out of the URL
    expect(parseSort("toString")).toBeUndefined();
  });
});

type Searchable = Parameters<typeof searchApplications>[0][number];

const app = (
  id: number,
  over: {
    title?: string;
    text?: string;
    notes?: string;
    created?: string;
    deadline?: string | null;
  } = {},
): Searchable & { id: number } => ({
  id,
  posting_detail: { title: over.title ?? "", posting_text: over.text ?? "" },
  notes: over.notes ?? "",
  created_at: over.created ?? "2026-11-01T10:00:00Z",
  deadline: over.deadline ?? null,
});

describe("searchApplications", () => {
  const list = [
    app(1, { title: "Backend Engineer", text: "Python in Berlin" }),
    app(2, { title: "Frontend Entwickler", text: "React in München" }),
    app(3, { title: "Data Analyst", notes: "Called Anna on Monday" }),
  ];
  const ids = (q: string | undefined) =>
    searchApplications(list, q).map((r) => r.id);

  it("returns the whole list for an empty or blank search", () => {
    expect(searchApplications(list, undefined)).toBe(list);
    expect(searchApplications(list, "   ")).toBe(list);
  });

  it("matches the title, the posting and the notes, ignoring case", () => {
    expect(ids("engineer")).toEqual([1]);
    expect(ids("BERLIN")).toEqual([1]);
    expect(ids("anna")).toEqual([3]);
  });

  it("needs every word, in any order", () => {
    expect(ids("berlin python")).toEqual([1]);
    expect(ids("berlin react")).toEqual([]);
  });

  it("ignores accents and spells ß both ways", () => {
    expect(ids("munchen")).toEqual([2]);
    expect(
      searchApplications([app(4, { text: "Straße" })], "strasse"),
    ).toHaveLength(1);
    expect(
      searchApplications([app(5, { text: "Strasse" })], "straße"),
    ).toHaveLength(1);
  });
});

describe("sortApplications", () => {
  const ids = (rows: { id: number }[]) => rows.map((r) => r.id);

  it("puts the newest first by default, and the oldest first on request", () => {
    const list = [
      app(1, { created: "2026-10-01T00:00:00Z" }),
      app(2, { created: "2026-11-20T00:00:00Z" }),
      app(3, { created: "2026-11-01T00:00:00Z" }),
    ];
    expect(ids(sortApplications(list, undefined))).toEqual([2, 3, 1]);
    expect(ids(sortApplications(list, "oldest"))).toEqual([1, 3, 2]);
  });

  it("sorts by title alphabetically, numbers by value, untitled last", () => {
    const list = [
      app(1, { title: "" }),
      app(2, { title: "Übersetzer" }),
      app(3, { title: "analyst 10" }),
      app(4, { title: "Analyst 9" }),
      app(5, { title: "Zoologist" }),
    ];
    expect(ids(sortApplications(list, "title"))).toEqual([4, 3, 2, 5, 1]);
  });

  it("puts the next deadline first, then the passed ones, then those without", () => {
    const today = new Date(2026, 10, 26); // 26 Nov 2026, local
    const list = [
      app(1, { deadline: null }),
      app(2, { deadline: "2026-12-10" }),
      app(3, { deadline: "2026-11-20" }), // passed
      app(4, { deadline: "2026-11-26" }), // today still counts as coming
      app(5, { deadline: "2026-11-25" }), // passed, more recently
    ];
    expect(ids(sortApplications(list, "deadline", today))).toEqual([
      4, 2, 5, 3, 1,
    ]);
  });

  it("never reorders the list it was given", () => {
    const list = [app(1, { created: "2026-01-01T00:00:00Z" }), app(2)];
    const copy = [...list];
    sortApplications(list, undefined);
    expect(list).toEqual(copy);
  });
});

describe("APPLICATION_STATUSES", () => {
  it("lists every status once, in lifecycle order", () => {
    expect(APPLICATION_STATUSES).toEqual([
      "draft",
      "approved",
      "sent",
      "response",
      "follow_up",
      "inactive",
    ]);
  });
});

const rows = (...statuses: ApplicationStatus[]) =>
  statuses.map((status, id) => ({ id, status }));

describe("statusCounts", () => {
  it("counts each status the list holds, in lifecycle order", () => {
    expect(statusCounts(rows("sent", "draft", "sent", "inactive"))).toEqual([
      { status: "draft", count: 1 },
      { status: "sent", count: 2 },
      { status: "inactive", count: 1 },
    ]);
  });

  it("leaves out statuses nobody has", () => {
    expect(statusCounts(rows("draft", "draft"))).toEqual([
      { status: "draft", count: 2 },
    ]);
    expect(statusCounts([])).toEqual([]);
  });
});

describe("filterByStatus", () => {
  it("keeps only the rows with that status, in their order", () => {
    const list = rows("sent", "draft", "sent");
    expect(filterByStatus(list, "sent").map((r) => r.id)).toEqual([0, 2]);
  });

  it("returns the whole list without a filter", () => {
    const list = rows("sent", "draft");
    expect(filterByStatus(list, undefined)).toBe(list);
  });

  it("returns nothing when no row has the status", () => {
    expect(filterByStatus(rows("draft"), "response")).toEqual([]);
  });
});

describe("portfolioLinkNote", () => {
  it("says the application's own choice wins over the default", () => {
    expect(portfolioLinkNote(true, false)).toBe("Chosen for this application.");
    expect(portfolioLinkNote(false, true)).toBe("Chosen for this application.");
  });

  it("names the default while the application follows it", () => {
    expect(portfolioLinkNote(null, true)).toBe("On by your default.");
    expect(portfolioLinkNote(null, false)).toBe("Off by your default.");
    expect(portfolioLinkNote(null, undefined)).toBe("Off by your default.");
  });
});
