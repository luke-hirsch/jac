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
 * unkerned run — both forms show up depending on the text. Bytes decode 1:1 to chars,
 * which is exact for ASCII and for WinAnsi ≥ 0xA0 (it agrees with latin1 there), but
 * NOT for 0x80–0x9F: that is where WinAnsi keeps the typographic punctuation this CV
 * actually prints — the bullet, the en dash in the date column, the em dash in a
 * heading. Undo that one divergence so assertions can be written in real characters.
 */
const WINANSI_HIGH: Record<number, string> = {
  0x80: "€", 0x82: "‚", 0x83: "ƒ", 0x84: "„", 0x85: "…", 0x86: "†", 0x87: "‡",
  0x88: "ˆ", 0x89: "‰", 0x8a: "Š", 0x8b: "‹", 0x8c: "Œ", 0x8e: "Ž", 0x91: "‘",
  0x92: "’", 0x93: "“", 0x94: "”", 0x95: "•", 0x96: "–", 0x97: "—", 0x98: "˜",
  0x99: "™", 0x9a: "š", 0x9b: "›", 0x9c: "œ", 0x9e: "ž", 0x9f: "Ÿ",
};

const fromWinAnsi = (s: string) =>
  s.replace(/[\u0080-\u009f]/g, (c) => WINANSI_HIGH[c.charCodeAt(0)] ?? c);

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
  const text = literals
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
  return fromWinAnsi(text);
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
