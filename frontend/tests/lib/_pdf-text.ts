/**
 * Shared PDF text extraction for the render tests (not a test module — vitest only
 * collects `*.test.ts`). Pulled out of render-hidden-pdf.test.ts when the density/header
 * tests needed the same extractor.
 */
import { inflateSync } from "node:zlib";

/**
 * Every Flate stream inflated (raw fallback for uncompressed ones), then all string
 * literals concatenated: kerning may split one text run into many literals with
 * positioning numbers between them, so only the concatenation is comparable — and
 * word spacing may swallow blanks entirely, hence `flat` comparisons in the tests.
 *
 * react-pdf/pdfkit renders a kerned run as a `TJ` array of *hex* strings (`<...>`)
 * interleaved with position adjustments, not the plain `(...)` literal `Tj` uses for an
 * unkerned run — both forms show up depending on the text. WinAnsi hex bytes decode
 * 1:1 to chars via `fromCharCode`, which is exact for the ASCII these suites render.
 */
export function pdfTextRuns(buf: Buffer): string {
  const latin1 = buf.toString("latin1");
  const chunks: string[] = [];
  const re = /stream\r?\n/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(latin1))) {
    const start = m.index + m[0].length;
    const end = latin1.indexOf("endstream", start);
    if (end < 0) break;
    let data = buf.subarray(start, end);
    while (
      data.length &&
      (data[data.length - 1] === 0x0a || data[data.length - 1] === 0x0d)
    ) {
      data = data.subarray(0, data.length - 1);
    }
    try {
      chunks.push(inflateSync(data).toString("latin1"));
    } catch {
      chunks.push(latin1.slice(start, end)); // not Flate — take it raw
    }
    re.lastIndex = end;
  }
  const literals =
    chunks.join("\n").match(/\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]*>/g) ?? [];
  return literals
    .map((s) => {
      if (s[0] === "(") return s.slice(1, -1).replace(/\\(.)/g, "$1");
      const hex = s.slice(1, -1).replace(/\s+/g, "");
      let out = "";
      for (let i = 0; i < hex.length; i += 2) {
        out += String.fromCharCode(parseInt(hex.slice(i, i + 2), 16));
      }
      return out;
    })
    .join("");
}

/**
 * The Info dict's string values: pdfkit stores each as either an inline `(literal)`
 * right after the key, or (seen once enough metadata fields are set) an indirect
 * `/Key N 0 R` pointing at a standalone `N 0 obj (literal) endobj` — both are the info
 * dictionary, just PDF's normal indirection, so dereference rather than assume inline.
 */
export function infoField(latin1: string, key: string): string | undefined {
  const inline = latin1.match(new RegExp(`/${key} \\(((?:\\\\.|[^\\\\)])*)\\)`));
  if (inline) return inline[1].replace(/\\(.)/g, "$1");
  const ref = latin1.match(new RegExp(`/${key} (\\d+) 0 R`));
  if (!ref) return undefined;
  const obj = latin1.match(
    new RegExp(`\\n${ref[1]} 0 obj\\s*\\(((?:\\\\.|[^\\\\)])*)\\)\\s*endobj`),
  );
  return obj ? obj[1].replace(/\\(.)/g, "$1") : undefined;
}

export const flat = (s: string) => s.replace(/\s+/g, "");
