/**
 * The PDF on screen, page by page, drawn with pdf.js onto a canvas. Not an <iframe> of the blob as
 * the export card's preview does: the sandboxed frame Claude runs this in gets no PDF viewer, and
 * the phone app's WebView paints a blank box (`supportsInlinePdf` knows that for iOS).
 *
 * The legacy build: it brings its polyfills, and the phone's WebView is whatever the OS ships.
 * No worker thread: the sandbox may refuse one, and an application is a few pages. pdf.js runs
 * its worker code on this thread when it finds `globalThis.pdfjsWorker`.
 */
import * as pdfjs from "pdfjs-dist/legacy/build/pdf.mjs";
import * as pdfjsWorker from "pdfjs-dist/legacy/build/pdf.worker.mjs";

(globalThis as { pdfjsWorker?: unknown }).pdfjsWorker = pdfjsWorker;

export type PdfDoc = pdfjs.PDFDocumentProxy;
export type RenderTask = pdfjs.RenderTask;

export async function openPdf(blob: Blob): Promise<PdfDoc> {
  const data = new Uint8Array(await blob.arrayBuffer());
  // System fonts for the standard 14 (Helvetica), which react-pdf doesn't embed and whose font
  // files the sandbox couldn't fetch; no WebAssembly decoders, which would be fetched too.
  return pdfjs.getDocument({ data, useSystemFonts: true, useWasm: false })
    .promise;
}

/** Draws page `n` (1-based) `cssWidth` wide, sharp at the screen's pixel ratio. */
export async function drawPage(
  doc: PdfDoc,
  n: number,
  canvas: HTMLCanvasElement,
  cssWidth: number,
  started: (task: RenderTask) => void,
): Promise<void> {
  const page = await doc.getPage(n);
  const base = page.getViewport({ scale: 1 });
  const ratio = window.devicePixelRatio || 1;
  const viewport = page.getViewport({ scale: (cssWidth / base.width) * ratio });
  canvas.width = Math.floor(viewport.width);
  canvas.height = Math.floor(viewport.height);
  canvas.style.width = `${cssWidth}px`;
  canvas.style.height = `${(cssWidth * base.height) / base.width}px`;
  const task = page.render({ canvas, viewport });
  started(task);
  await task.promise;
}
