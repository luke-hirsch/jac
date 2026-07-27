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

/** Move the id at `index` by `delta` within an ordered id list. Returns the SAME array
 *  reference (no-op) at the boundaries or out of range, so callers can skip a needless save.
 *  The application's `attachments` field is exactly this ordered id list. */
export function moveId(ids: number[], index: number, delta: -1 | 1): number[] {
  const target = index + delta;
  if (index < 0 || index >= ids.length || target < 0 || target >= ids.length) {
    return ids;
  }
  const next = [...ids];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}
