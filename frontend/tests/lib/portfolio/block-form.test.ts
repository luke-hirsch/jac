import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import {
  BLOCK_KINDS,
  BLOCK_LIMITS,
  blockData,
  blockProblems,
  blockRequestBody,
  blockSummary,
  formFromBlock,
  kindLabel,
  mediaKind,
  removeAt,
  saveErrorMessage,
  takesFile,
  unseenFiles,
  updateAt,
  type BlockForm,
} from "@/lib/portfolio/block-form";
import type { BlockKind, PortfolioBlockRow } from "@/lib/queries/portfolio";

/**
 * Guide 26: the block editor's pure half. A block row as the form's state, the form as the
 * request body per kind (JSON, or multipart with `data` as JSON text when a file goes along),
 * and the server's limits (spa/blocks.py) checked before a long upload goes out. The dialog
 * itself is click-through verified ([[frontend-test-layout]]).
 */

function row(over: Partial<PortfolioBlockRow> = {}): PortfolioBlockRow {
  return {
    id: 7,
    kind: "text",
    title: "",
    body: "",
    image: null,
    media: null,
    data: {},
    alt_text: "",
    domains: [],
    links: [],
    favourite: false,
    order: 0,
    is_active: true,
    updated_at: "2026-11-24T00:00:00Z",
    ...over,
  };
}

function form(kind: BlockKind, over: Partial<BlockForm> = {}): BlockForm {
  return { ...formFromBlock(null), kind, ...over };
}

function file(name: string, bytes = 10): File {
  return new File([new Uint8Array(bytes)], name);
}

/** A File that reports `size` without allocating it. */
function bigFile(name: string, size: number): File {
  const f = file(name);
  Object.defineProperty(f, "size", { value: size });
  return f;
}

const MB = 1024 * 1024;

describe("BLOCK_KINDS", () => {
  it("offers the seven kinds in the editor's order, with labels", () => {
    expect(BLOCK_KINDS.map((k) => k.value)).toEqual([
      "text",
      "image",
      "quote",
      "figures",
      "cta",
      "linklist",
      "media",
    ]);
    expect(kindLabel("cta")).toBe("Call to action");
    expect(kindLabel("media")).toBe("Audio or video");
  });
});

describe("takesFile", () => {
  it("gives an image block its image and a media block its file and poster", () => {
    expect(takesFile("image", "image")).toBe(true);
    expect(takesFile("image", "media")).toBe(false);
    expect(takesFile("media", "media")).toBe(true);
    expect(takesFile("media", "image")).toBe(true);
    for (const kind of [
      "text",
      "quote",
      "figures",
      "cta",
      "linklist",
    ] as const) {
      expect(takesFile(kind, "image")).toBe(false);
      expect(takesFile(kind, "media")).toBe(false);
    }
  });
});

describe("unseenFiles", () => {
  it("names the stored files a kind won't show, which the server keeps", () => {
    const media = row({
      kind: "media",
      media: "/media/portfolio/media/1/a.mp4",
      image: "/media/portfolio/blocks/1/cover.png",
    });
    expect(unseenFiles(media, "media")).toEqual([]);
    expect(unseenFiles(media, "image")).toEqual(["media"]);
    expect(unseenFiles(media, "text")).toEqual(["image", "media"]);
    expect(unseenFiles(row({ kind: "text" }), "quote")).toEqual([]);
    expect(unseenFiles(null, "text")).toEqual([]);
  });
});

describe("formFromBlock", () => {
  it("starts a new block as text, with two figure rows and one link row to fill", () => {
    const f = formFromBlock(null);
    expect(f.kind).toBe("text");
    expect(f.is_active).toBe(true);
    expect(f.figures).toEqual([
      { value: "", label: "" },
      { value: "", label: "" },
    ]);
    expect(f.items).toEqual([{ title: "", url: "", note: "" }]);
  });

  it("reads a quote's by and role", () => {
    const f = formFromBlock(
      row({ kind: "quote", body: "Hi", data: { by: "Ada", role: "CTO" } }),
    );
    expect([f.kind, f.body, f.by, f.role]).toEqual([
      "quote",
      "Hi",
      "Ada",
      "CTO",
    ]);
  });

  it("keeps stored figures and pads none", () => {
    const figures = [
      { value: "40%", label: "faster" },
      { value: "3", label: "teams" },
      { value: "1", label: "launch" },
    ];
    expect(
      formFromBlock(row({ kind: "figures", data: { figures } })).figures,
    ).toEqual(figures);
  });

  it("gives every stored link a note, blank when it has none", () => {
    const f = formFromBlock(
      row({
        kind: "linklist",
        data: { items: [{ title: "Talk", url: "https://example.com" }] },
      }),
    );
    expect(f.items).toEqual([
      { title: "Talk", url: "https://example.com", note: "" },
    ]);
  });

  it("reads a call to action's label and link", () => {
    const f = formFromBlock(
      row({
        kind: "cta",
        data: { label: "Mail me", url: "mailto:a@example.com" },
      }),
    );
    expect([f.label, f.url]).toEqual(["Mail me", "mailto:a@example.com"]);
  });
});

describe("blockData", () => {
  it("sends only the chosen kind's fields, trimmed", () => {
    const f = form("quote", { by: " Ada ", role: " CTO ", label: "stray" });
    expect(blockData(f)).toEqual({ by: "Ada", role: "CTO" });
    expect(blockData({ ...f, kind: "text" })).toEqual({});
    expect(blockData({ ...f, kind: "image" })).toEqual({});
    expect(blockData({ ...f, kind: "media" })).toEqual({});
  });

  it("drops the blank rows the editor keeps for typing", () => {
    const f = form("figures", {
      figures: [
        { value: " 40% ", label: " faster " },
        { value: "", label: "" },
        { value: "3", label: "teams" },
      ],
    });
    expect(blockData(f)).toEqual({
      figures: [
        { value: "40%", label: "faster" },
        { value: "3", label: "teams" },
      ],
    });
    const l = form("linklist", {
      items: [
        { title: "", url: "", note: "" },
        { title: "Talk", url: " https://example.com ", note: "" },
      ],
    });
    expect(blockData(l)).toEqual({
      items: [{ title: "Talk", url: "https://example.com", note: "" }],
    });
  });

  it("gives a call to action its label and link", () => {
    expect(
      blockData(form("cta", { label: " Go ", url: " https://x.de " })),
    ).toEqual({
      label: "Go",
      url: "https://x.de",
    });
  });
});

describe("blockProblems", () => {
  it("passes a complete block of every kind", () => {
    const good: [BlockForm, Parameters<typeof blockProblems>[1]][] = [
      [form("text", { body: "Hi" }), {}],
      [form("image"), { image: file("a.png") }],
      [form("quote", { body: "Hi", by: "Ada" }), {}],
      [
        form("figures", {
          figures: [
            { value: "40%", label: "faster" },
            { value: "3", label: "teams" },
          ],
        }),
        {},
      ],
      [form("cta", { label: "Go", url: "https://example.com" }), {}],
      [
        form("cta", { label: "Go", url: "mailto:jane@example.com?subject=Hi" }),
        {},
      ],
      [
        form("linklist", {
          items: [
            { title: "Paper", url: "http://example.org/p.pdf", note: "" },
          ],
        }),
        {},
      ],
      [form("media"), { media: file("talk.mp4") }],
    ];
    for (const [f, files] of good)
      expect(blockProblems(f, files), f.kind).toEqual([]);
  });

  it("asks for each kind's required field", () => {
    expect(blockProblems(form("text"), {})).toHaveLength(1);
    expect(blockProblems(form("image"), {})).toHaveLength(1);
    expect(blockProblems(form("quote", { body: "Hi" }), {})).toHaveLength(1);
    expect(blockProblems(form("quote", { by: "Ada" }), {})).toHaveLength(1);
    expect(blockProblems(form("cta", { label: "Go" }), {})).toHaveLength(1);
    expect(blockProblems(form("media"), {})).toHaveLength(1);
  });

  it("doesn't ask for a file the block already has", () => {
    expect(
      blockProblems(form("image"), {}, { image: true, media: false }),
    ).toEqual([]);
    expect(
      blockProblems(form("media"), {}, { image: false, media: true }),
    ).toEqual([]);
  });

  it("holds key figures to two to four complete rows", () => {
    const one = [{ value: "3", label: "teams" }];
    expect(blockProblems(form("figures", { figures: one }), {})).not.toEqual(
      [],
    );
    const five = Array.from({ length: 5 }, () => ({
      value: "3",
      label: "teams",
    }));
    expect(blockProblems(form("figures", { figures: five }), {})).not.toEqual(
      [],
    );
    const half = [...one, { value: "40%", label: "" }];
    expect(blockProblems(form("figures", { figures: half }), {})).not.toEqual(
      [],
    );
  });

  it("refuses links a visitor shouldn't be sent to", () => {
    for (const url of [
      "javascript:alert(1)",
      "http://example.com",
      "example.com",
      "mailto:nobody",
    ]) {
      expect(
        blockProblems(form("cta", { label: "Go", url }), {}),
        url,
      ).not.toEqual([]);
    }
    for (const url of [
      "javascript:alert(1)",
      "mailto:jane@example.com",
      "ftp://x.de",
    ]) {
      const items = [{ title: "T", url, note: "" }];
      expect(blockProblems(form("linklist", { items }), {}), url).not.toEqual(
        [],
      );
    }
  });

  it("holds a link list to one to twelve titled links", () => {
    const empty = [{ title: "", url: "", note: "" }];
    expect(blockProblems(form("linklist", { items: empty }), {})).not.toEqual(
      [],
    );
    const untitled = [{ title: "", url: "https://x.de", note: "" }];
    expect(
      blockProblems(form("linklist", { items: untitled }), {}),
    ).not.toEqual([]);
    const many = Array.from({ length: 13 }, () => ({
      title: "T",
      url: "https://x.de",
      note: "",
    }));
    expect(blockProblems(form("linklist", { items: many }), {})).not.toEqual(
      [],
    );
  });

  it("mirrors the server's text limits", () => {
    const L = BLOCK_LIMITS;
    const long = (n: number) => "x".repeat(n + 1);
    expect(
      blockProblems(form("quote", { body: "Hi", by: long(L.by) }), {}),
    ).not.toEqual([]);
    expect(
      blockProblems(
        form("quote", { body: "Hi", by: "Ada", role: long(L.role) }),
        {},
      ),
    ).not.toEqual([]);
    expect(
      blockProblems(
        form("cta", { label: long(L.ctaLabel), url: "https://x.de" }),
        {},
      ),
    ).not.toEqual([]);
    const items = [{ title: "T", url: "https://x.de", note: long(L.itemNote) }];
    expect(blockProblems(form("linklist", { items }), {})).not.toEqual([]);
  });

  it("stops a file over the cap before it's uploaded", () => {
    expect(
      blockProblems(form("media"), { media: bigFile("talk.mp4", 50 * MB + 1) }),
    ).not.toEqual([]);
    expect(
      blockProblems(form("media"), {
        media: file("talk.mp4"),
        image: bigFile("poster.png", 5 * MB + 1),
      }),
    ).not.toEqual([]);
    expect(
      blockProblems(form("media"), { media: bigFile("talk.mp4", 50 * MB) }),
    ).toEqual([]);
  });

  it("stops a file the server won't take, by its name", () => {
    expect(
      blockProblems(form("media"), { media: file("clip.mov") }),
    ).not.toEqual([]);
    for (const name of [
      "a.mp4",
      "a.webm",
      "a.mp3",
      "a.m4a",
      "a.ogg",
      "A.MP4",
    ]) {
      expect(blockProblems(form("media"), { media: file(name) }), name).toEqual(
        [],
      );
    }
  });

  it("ignores a file the kind doesn't take", () => {
    expect(
      blockProblems(form("text", { body: "Hi" }), {
        media: bigFile("x.mov", 99 * MB),
      }),
    ).toEqual([]);
  });
});

describe("blockRequestBody", () => {
  it("is JSON with the kind's data when no file goes along", () => {
    const body = blockRequestBody(
      form("quote", { body: "Hi", by: "Ada", domains: [3] }),
    );
    expect(typeof body).toBe("string");
    const json = JSON.parse(body as string);
    expect(json).toMatchObject({
      kind: "quote",
      body: "Hi",
      domains: [3],
      data: { by: "Ada", role: "" },
    });
    expect(json).not.toHaveProperty("by");
    expect(json).not.toHaveProperty("figures");
  });

  it("goes multipart with a file, and data as JSON text", () => {
    const media = file("talk.mp4");
    const image = file("poster.png");
    const body = blockRequestBody(
      form("media", { title: "Talk", domains: [1, 2], links: ["job:3"] }),
      { media, image },
    );
    expect(body).toBeInstanceOf(FormData);
    const fd = body as FormData;
    expect(fd.get("kind")).toBe("media");
    expect(fd.get("title")).toBe("Talk");
    expect(JSON.parse(fd.get("data") as string)).toEqual({});
    expect(fd.getAll("domains")).toEqual(["1", "2"]);
    expect(fd.getAll("links")).toEqual(["job:3"]);
    expect(fd.get("is_active")).toBe("true");
    expect((fd.get("media") as File).name).toBe("talk.mp4");
    expect((fd.get("image") as File).name).toBe("poster.png");
  });

  it("never sends a file the kind doesn't take", () => {
    const body = blockRequestBody(form("text", { body: "Hi" }), {
      media: file("talk.mp4"),
      image: file("a.png"),
    });
    expect(typeof body).toBe("string");
    const fd = blockRequestBody(form("image"), {
      media: file("talk.mp4"),
      image: file("a.png"),
    }) as FormData;
    expect(fd.get("media")).toBeNull();
    expect(fd.get("image")).not.toBeNull();
  });

  it("keeps a link list JSON, data and all, when a stray file is still picked", () => {
    const items = [
      { title: "Talk", url: "https://example.com", note: "20 min" },
    ];
    const body = blockRequestBody(form("linklist", { items }), {
      image: file("a.png"),
    });
    expect(typeof body).toBe("string");
    expect(JSON.parse(body as string).data).toEqual({ items });
  });
});

describe("saveErrorMessage", () => {
  it("passes on the server's own message", () => {
    expect(
      saveErrorMessage(
        new ApiError(400, {
          media: ["Only MP4 or WebM video and MP3, M4A or Ogg audio."],
        }),
      ),
    ).toBe("Only MP4 or WebM video and MP3, M4A or Ogg audio.");
    expect(
      saveErrorMessage(new ApiError(400, { data: ["data.by is required."] })),
    ).toBe("data.by is required.");
  });

  it("explains what nginx and the throttle answer", () => {
    expect(
      saveErrorMessage(new ApiError(413, "<html>Too large</html>")),
    ).toContain("50 MB");
    expect(
      saveErrorMessage(new ApiError(429, { detail: "Throttled." })),
    ).toContain("upload limit");
  });

  it("says something for anything else", () => {
    expect(saveErrorMessage(new Error("offline"))).toBe(
      "Couldn't save the block.",
    );
    expect(saveErrorMessage(new ApiError(500, "boom"))).toBe(
      "Couldn't save the block.",
    );
  });
});

describe("mediaKind", () => {
  it("tells video from audio by the stored extension", () => {
    expect(mediaKind("/media/portfolio/media/1/a.mp4")).toBe("video");
    expect(
      mediaKind("https://jac.example.com/media/portfolio/media/1/a.webm"),
    ).toBe("video");
    expect(mediaKind("/media/portfolio/media/1/a.m4a")).toBe("audio");
    expect(mediaKind("/media/portfolio/media/1/a.mp3")).toBe("audio");
    expect(mediaKind("/media/portfolio/media/1/a.ogg")).toBe("audio");
    expect(mediaKind("/media/portfolio/media/1/a.mov")).toBeNull();
    expect(mediaKind(null)).toBeNull();
  });
});

describe("blockSummary", () => {
  it("says what a block holds when it has no body to show", () => {
    expect(
      blockSummary(
        row({
          kind: "figures",
          data: {
            figures: [
              { value: "40%", label: "faster" },
              { value: "3", label: "teams" },
            ],
          },
        }),
      ),
    ).toBe("40% faster · 3 teams");
    expect(
      blockSummary(
        row({
          kind: "linklist",
          data: {
            items: [
              { title: "Talk", url: "https://x.de" },
              { title: "Paper", url: "https://y.de" },
            ],
          },
        }),
      ),
    ).toBe("2 links: Talk, Paper");
    expect(
      blockSummary(
        row({ kind: "media", media: "/media/portfolio/media/1/a.m4a" }),
      ),
    ).toBe("Audio (M4A)");
  });

  it("names a quote's speaker and a button's target", () => {
    expect(
      blockSummary(row({ kind: "quote", body: "Hi", data: { by: "Ada" } })),
    ).toBe("“Hi” (Ada)");
    expect(
      blockSummary(
        row({
          kind: "cta",
          data: { label: "Mail me", url: "mailto:a@example.com" },
        }),
      ),
    ).toBe("Mail me → mailto:a@example.com");
  });

  it("is the body for text", () => {
    expect(blockSummary(row({ kind: "text", body: "Hello" }))).toBe("Hello");
  });
});

describe("updateAt / removeAt", () => {
  it("change one row and leave the others alone", () => {
    const rows = [{ a: 1 }, { a: 2 }];
    expect(updateAt(rows, 1, { a: 3 })).toEqual([{ a: 1 }, { a: 3 }]);
    expect(removeAt(rows, 0)).toEqual([{ a: 2 }]);
    expect(rows).toEqual([{ a: 1 }, { a: 2 }]);
  });
});
