import { describe, expect, it } from "vitest";
import {
  applicationIdOf,
  errorText,
  fileStem,
  previewNotices,
  previewPayload,
  refusalOf,
  resultJson,
  toBase64,
  wasmAllowed,
  within,
  type ToolResult,
} from "@/connector/payload";
import type { BuiltPdf } from "@/lib/render/preview";

/**
 * The connector preview's reading of the host's tool results (src/connector/payload.ts). jac's
 * connector answers every tool with one text block of JSON (backend/jac/mcp.py `_tools_call`).
 */

const text = (data: unknown, isError = false): ToolResult => ({
  content: [{ type: "text", text: JSON.stringify(data) }],
  isError,
});

const built = (over: Partial<BuiltPdf> = {}): BuiltPdf => ({
  blob: new Blob([]),
  fit: {
    content: {},
    pages: 1,
    fits: true,
    droppedIds: [],
    demotedIds: [],
    addedIds: [],
    cutIds: [],
  } as unknown as BuiltPdf["fit"],
  letterPages: 1,
  ...over,
});

describe("the application a tool result is about", () => {
  it("reads the id save_application and show_application answer with", () => {
    expect(applicationIdOf(text({ id: 12, url: "x", warnings: [] }))).toBe(12);
  });

  it("is none for a refusal, a non-number id or text that isn't JSON", () => {
    expect(applicationIdOf(text({ id: 12 }, true))).toBeNull();
    expect(applicationIdOf(text({ id: "12" }))).toBeNull();
    expect(
      applicationIdOf({ content: [{ type: "text", text: "{nope" }] }),
    ).toBeNull();
    expect(applicationIdOf({ content: [] })).toBeNull();
    expect(resultJson({})).toBeNull();
  });
});

describe("a refusal in jac's words", () => {
  it("is the detail when jac gives one", () => {
    expect(
      refusalOf(text({ detail: "There is no application 9." }, true)),
    ).toBe("There is no application 9.");
  });

  it("names the refused fields otherwise", () => {
    expect(
      refusalOf(text({ errors: { entries: ["x"], letter: ["y"] } }, true)),
    ).toBe("jac refused: entries, letter.");
    expect(refusalOf({ isError: true })).toBe("jac refused the request.");
  });
});

describe("the preview payload", () => {
  const payload = {
    application: { id: 3 },
    layout: null,
    career: { jobs: [] },
    profile: { name: "Ada" },
    attachments: 0,
    signature: false,
    url: "https://jac.example/applications/3",
  };

  it("passes jac's answer through", () => {
    expect(previewPayload(text(payload))).toEqual(payload);
  });

  it("throws jac's refusal, so the page can show it", () => {
    expect(() =>
      previewPayload(text({ detail: "There is no application 3." }, true)),
    ).toThrow("There is no application 3.");
  });

  it("throws on an answer without the parts it renders from", () => {
    expect(() => previewPayload(text({ application: { id: 3 } }))).toThrow(
      "no application",
    );
  });
});

describe("the notes under the pages", () => {
  const none = { attachments: 0, signature: false };

  it("are the export card's fit notices", () => {
    const notes = previewNotices(
      built({ letterPages: 2 }),
      1,
      "complete",
      none,
    );
    expect(notes.map((n) => n.text)).toEqual([
      "The cover letter exceeds one page — shorten the body.",
    ]);
  });

  it("say how many attachments only the export in jac adds", () => {
    expect(
      previewNotices(built(), 1, "complete", { ...none, attachments: 1 }).at(-1)
        ?.text,
    ).toBe("1 attachment is added when you export in jac.");
    expect(
      previewNotices(built(), 1, "cv", { ...none, attachments: 3 }).at(-1)
        ?.text,
    ).toBe("3 attachments are added when you export in jac.");
  });

  it("leave attachments out of a letter on its own, as the export does", () => {
    expect(
      previewNotices(built(), 1, "letter", { ...none, attachments: 2 }),
    ).toEqual([]);
  });

  it("say the export signs the letter, where there is a letter", () => {
    const signed = { ...none, signature: true };
    expect(
      previewNotices(built(), 1, "letter", signed).map((n) => n.text),
    ).toEqual(["Your signature is added when you export in jac."]);
    expect(previewNotices(built(), 1, "cv", signed)).toEqual([]);
  });
});

describe("the download", () => {
  it("is named like the export card's file", () => {
    expect(fileStem(7, "complete")).toBe("application-7-complete");
  });

  it("hands over standard base64, also past one chunk", () => {
    const small = new TextEncoder().encode("%PDF-1.7");
    expect(toBase64(small)).toBe(Buffer.from(small).toString("base64"));
    const big = new Uint8Array(100_000).map((_, i) => i % 251);
    expect(toBase64(big)).toBe(Buffer.from(big).toString("base64"));
  });
});

describe("an error on the page", () => {
  it("names the error and its message", () => {
    expect(errorText(new TypeError("boom"))).toBe("TypeError: boom");
    expect(errorText("plain")).toBe("plain");
  });
});

describe("what the renderer needs from the sandbox", () => {
  it("finds WebAssembly where it may run", () => {
    expect(wasmAllowed()).toBe(true);
  });

  it("finds none where compiling it throws", () => {
    const real = WebAssembly.Module;
    WebAssembly.Module = function () {
      throw new WebAssembly.CompileError(
        "violates the Content Security Policy",
      );
    } as unknown as typeof WebAssembly.Module;
    try {
      expect(wasmAllowed()).toBe(false);
    } finally {
      WebAssembly.Module = real;
    }
  });
});

describe("a render that never settles", () => {
  it("ends in words after the limit", async () => {
    await expect(within(new Promise(() => {}), 10, "too slow")).rejects.toThrow(
      "too slow",
    );
  });

  it("passes a render that settles in time through", async () => {
    await expect(within(Promise.resolve(3), 1000, "too slow")).resolves.toBe(3);
  });
});
