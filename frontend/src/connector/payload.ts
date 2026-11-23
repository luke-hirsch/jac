/**
 * The connector preview's pure parts: reading the host's tool results, the notes under the
 * pages, the file a download hands over. No DOM, no pdf.js, so the suite runs them in node.
 */
import type { ExportScope } from "@/lib/export";
import type { ApplicationRow } from "@/lib/queries/applications";
import type { CvEntriesResponse } from "@/lib/queries/jac";
import type { ProfileRow } from "@/lib/queries/profile";
import { fitNotices, type BuiltPdf, type Notice } from "@/lib/render/preview";

/** The parts of an MCP CallToolResult the preview reads. jac answers with one text block of
 *  JSON (jac/mcp.py `_tools_call`) and no structuredContent. */
export type ToolResult = {
  content?: { type: string; text?: string }[];
  isError?: boolean;
};

/** What `application_preview` answers (jac/mcp.py): the export card's inputs. */
export type PreviewPayload = {
  application: ApplicationRow;
  layout: unknown; // the layout's JSON spec as stored, null without a template file
  career: CvEntriesResponse;
  profile: ProfileRow; // `signature` is a data URL here, or ""
  attachments: number; // what the export in jac appends, which the preview can't
  url: string;
};

/** The JSON in a result's text block, or null when there is none or it doesn't parse. */
export function resultJson(result: ToolResult): unknown {
  const text = result.content?.find((c) => c.type === "text")?.text;
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/** The application a `save_application` or `show_application` result is about. */
export function applicationIdOf(result: ToolResult): number | null {
  if (result.isError) return null;
  const data = resultJson(result) as { id?: unknown } | null;
  return typeof data?.id === "number" ? data.id : null;
}

/** A refusal in jac's words: its `detail`, else the fields it named. */
export function refusalOf(result: ToolResult): string {
  const data = resultJson(result) as {
    detail?: unknown;
    errors?: Record<string, unknown>;
  } | null;
  if (typeof data?.detail === "string") return data.detail;
  if (data?.errors && typeof data.errors === "object") {
    return `jac refused: ${Object.keys(data.errors).join(", ")}.`;
  }
  return "jac refused the request.";
}

export function previewPayload(result: ToolResult): PreviewPayload {
  if (result.isError) throw new Error(refusalOf(result));
  const data = resultJson(result) as Partial<PreviewPayload> | null;
  if (!data?.application || !data.career || !data.profile) {
    throw new Error("jac's answer had no application to render.");
  }
  return data as PreviewPayload;
}

/** The notes under the pages: what the fit did (the export card's own wording), then what only
 *  the export in jac adds. */
export function previewNotices(
  built: BuiltPdf,
  pageBudget: number,
  scope: ExportScope,
  attachments: number,
): Notice[] {
  const out = fitNotices(built, pageBudget);
  if (scope !== "letter" && attachments > 0) {
    out.push({
      level: "info",
      text:
        `${attachments} attachment${attachments === 1 ? "" : "s"} ` +
        `${attachments === 1 ? "is" : "are"} added when you export in jac.`,
    });
  }
  return out;
}

/** The file name the export card gives the same PDF. */
export function fileStem(id: number, scope: ExportScope): string {
  return `application-${id}-${scope}`;
}

/** Standard base64, no data: prefix: what `ui/download-file` takes as a resource's `blob`.
 *  Chunked, since spreading a whole PDF into one call overflows the argument limit. */
export function toBase64(bytes: Uint8Array): string {
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

/** react-pdf lays the pages out with WebAssembly (yoga). A sandbox whose CSP refuses it can't
 *  render, and react-pdf's own error says so only in emscripten's words. */
export function wasmAllowed(): boolean {
  try {
    new WebAssembly.Module(
      new Uint8Array([0x00, 0x61, 0x73, 0x6d, 0x01, 0, 0, 0]),
    );
    return true;
  } catch {
    return false;
  }
}

export const NO_WASM =
  "This app doesn't allow WebAssembly in previews, and jac's PDF renderer needs it";

/** Rejects after `ms` with `what`, so a render that never settles still ends in words. */
export function within<T>(
  promise: Promise<T>,
  ms: number,
  what: string,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const late = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(what)), ms);
  });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

/** An error's message for the page, so a live failure says what broke. */
export function errorText(err: unknown): string {
  if (err instanceof Error) return `${err.name}: ${err.message}`;
  return String(err);
}
