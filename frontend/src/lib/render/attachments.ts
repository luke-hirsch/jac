import { PDFDocument } from "pdf-lib";

/** Merge the react-pdf output with the attachment PDFs (fetched same-origin), in the given order.
 *  An unreachable attachment is skipped rather than failing the whole export. */
export async function mergePdfs(
  main: Blob,
  attachmentUrls: string[],
): Promise<Blob> {
  const out = await PDFDocument.load(await main.arrayBuffer());
  for (const url of attachmentUrls) {
    const res = await fetch(url, { credentials: "same-origin" });
    if (!res.ok) continue;
    const src = await PDFDocument.load(await res.arrayBuffer());
    const pages = await out.copyPages(src, src.getPageIndices());
    for (const p of pages) out.addPage(p);
  }
  // Copy into a fresh ArrayBuffer: pdf-lib's save() returns Uint8Array<ArrayBufferLike>,
  // which isn't a valid BlobPart under the DOM lib types.
  const bytes = await out.save();
  const buf = new ArrayBuffer(bytes.byteLength);
  new Uint8Array(buf).set(bytes);
  return new Blob([buf], { type: "application/pdf" });
}

export type AttachmentLike = { id: number; position: number };

/** Re-number a list to contiguous 0-based positions (mirrors cv-doc moveEntry). */
export function withPositions<T extends AttachmentLike>(items: T[]): T[] {
  return items.map((a, i) => ({ ...a, position: i }));
}

export function moveAttachment<T extends AttachmentLike>(
  items: T[],
  index: number,
  delta: -1 | 1,
): T[] {
  const target = index + delta;
  if (
    index < 0 ||
    index >= items.length ||
    target < 0 ||
    target >= items.length
  ) {
    return items;
  }
  const next = [...items];
  [next[index], next[target]] = [next[target], next[index]];
  return withPositions(next);
}
