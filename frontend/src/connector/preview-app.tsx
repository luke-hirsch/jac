/**
 * The PDF preview in the Claude app: the application the tool call saved or named, built by
 * the export card's own renderer and shown page by page. Every state says what it waits for,
 * and every failure says what broke, in the page: it's tested live in the app, where there is
 * no console to read.
 */
import type { App } from "@modelcontextprotocol/ext-apps";
import {
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
  type RefObject,
} from "react";
import { exportBlocker, type ExportScope } from "@/lib/export";
import { applyAccent } from "@/lib/render/accent";
import { buildPdf, exportMeta } from "@/lib/render/build";
import { PAGE_ASPECT, type BuiltPdf } from "@/lib/render/preview";
import {
  FALLBACK_SPEC,
  parseLayoutSpec,
  type LayoutSpec,
} from "@/lib/render/spec";
import type { HostState, HostStore } from "./host";
import { drawPage, openPdf, type PdfDoc, type RenderTask } from "./pages";
import {
  applicationIdOf,
  errorText,
  fileStem,
  previewNotices,
  previewPayload,
  NO_WASM,
  refusalOf,
  toBase64,
  wasmAllowed,
  within,
  type PreviewPayload,
  type ToolResult,
} from "./payload";

const SCOPES: { value: ExportScope; label: string }[] = [
  { value: "complete", label: "Complete" },
  { value: "cv", label: "CV" },
  { value: "letter", label: "Letter" },
];

// The inline page stays readable without taking the whole chat; full screen fits the height.
const INLINE_MAX_WIDTH = 720;
const FULLSCREEN_CHROME = 140; // toolbar + notes + padding, px
// The page fit renders the CV several times; a phone takes seconds, never minutes.
const RENDER_LIMIT_MS = 90_000;

type Load = {
  id: number;
  payload: PreviewPayload | null;
  error: string | null;
};

/** The render data for one application, fetched once per id through the host. */
function usePayload(app: App, id: number | null): Load | null {
  const [load, setLoad] = useState<Load | null>(null);
  useEffect(() => {
    if (id == null) return;
    let live = true;
    app
      .callServerTool({ name: "application_preview", arguments: { id } })
      .then((r) => previewPayload(r as ToolResult))
      .then(
        (payload) => live && setLoad({ id, payload, error: null }),
        (err) => live && setLoad({ id, payload: null, error: errorText(err) }),
      );
    return () => {
      live = false;
    };
  }, [app, id]);
  return load && load.id === id ? load : null;
}

type Rendered = {
  key: string;
  spec: LayoutSpec | null;
  built: BuiltPdf | null;
  doc: PdfDoc | null;
  blocked: string | null;
  error: string | null;
};

function renderKey(payload: PreviewPayload | null, scope: ExportScope) {
  return payload
    ? `${payload.application.id}:${payload.application.updated_at}:${scope}`
    : "";
}

/** The built PDF for the payload and scope: the export card's build, then pdf.js. */
function useRendered(
  payload: PreviewPayload | null,
  scope: ExportScope,
): Rendered | null {
  const key = renderKey(payload, scope);
  const [done, setDone] = useState<Rendered | null>(null);
  useEffect(() => {
    if (!payload) return;
    let live = true;
    const empty = {
      key,
      spec: null,
      built: null,
      doc: null,
      blocked: null,
      error: null,
    };
    (async (): Promise<Rendered> => {
      const row = payload.application;
      const blocked = exportBlocker(scope, "pdf", row.cover_letter);
      if (blocked) return { ...empty, blocked };
      if (!wasmAllowed()) return { ...empty, error: NO_WASM };
      const template =
        payload.layout == null
          ? FALLBACK_SPEC
          : parseLayoutSpec(payload.layout);
      const spec = applyAccent(template, payload.profile.accent_color);
      const built = await within(
        buildPdf({
          row,
          spec,
          db: payload.career,
          profile: payload.profile,
          scope,
        }),
        RENDER_LIMIT_MS,
        `The render didn't finish in ${RENDER_LIMIT_MS / 1000} seconds`,
      );
      return { ...empty, spec, built, doc: await openPdf(built.blob) };
    })().then(
      (r) => {
        if (live) setDone(r);
        else void r.doc?.loadingTask.destroy();
      },
      (err) => live && setDone({ ...empty, error: errorText(err) }),
    );
    return () => {
      live = false;
    };
  }, [payload, scope, key]);
  // A replaced document lets go of its pages.
  useEffect(() => () => void done?.doc?.loadingTask.destroy(), [done]);
  return done && done.key === key ? done : null;
}

/** The wrapper's width, followed as the host resizes the frame. */
function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.floor(entry.contentRect.width)),
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

function PageCanvas({
  doc,
  page,
  width,
  onError,
}: {
  doc: PdfDoc;
  page: number;
  width: number;
  onError: (text: string) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (!canvas.current || width <= 0) return;
    let task: RenderTask | null = null;
    let live = true;
    drawPage(doc, page, canvas.current, width, (t) => {
      task = t;
    }).catch((err) => {
      // A cancelled draw is the next one taking over, not a failure.
      if (live && (err as Error)?.name !== "RenderingCancelledException") {
        onError(errorText(err));
      }
    });
    return () => {
      live = false;
      (task as RenderTask | null)?.cancel();
    };
  }, [doc, page, width, onError]);
  return <canvas ref={canvas} className="page" aria-label={`Page ${page}`} />;
}

function Message({ children, tone }: { children: ReactNode; tone?: "error" }) {
  return (
    <p className={tone === "error" ? "message error" : "message"}>{children}</p>
  );
}

/** What the page waits for before there is anything to render, or null once there is. */
function waiting(h: HostState, id: number | null): ReactNode {
  if (h.error)
    return (
      <Message tone="error">
        The preview couldn't connect to Claude: {h.error}
      </Message>
    );
  if (!h.connected) return <Message>Connecting…</Message>;
  if (h.cancelled)
    return (
      <Message>The call was cancelled, so there's nothing to show.</Message>
    );
  if (!h.result)
    return (
      <Message>
        {h.pending ? "Waiting for jac…" : "Waiting for the call…"}
      </Message>
    );
  if (id == null) {
    return h.result.isError ? (
      <Message>Nothing to show: {refusalOf(h.result)}</Message>
    ) : (
      <Message>This answer names no application.</Message>
    );
  }
  return null;
}

export function PreviewApp({ app, host }: { app: App; host: HostStore }) {
  const h = useSyncExternalStore(host.subscribe, host.get);
  const id = h.result ? applicationIdOf(h.result) : null;
  const [scope, setScope] = useState<ExportScope>("complete");
  const load = usePayload(app, h.connected ? id : null);
  const payload = load?.payload ?? null;
  const rendered = useRendered(payload, scope);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<string | null>(null);
  const [drawError, setDrawError] = useState<string | null>(null);
  const frame = useRef<HTMLDivElement>(null);
  const width = useWidth(frame);

  const pages = rendered?.doc?.numPages ?? 0;
  const shown = Math.min(Math.max(page, 1), Math.max(pages, 1));
  const fullscreen = h.context.displayMode === "fullscreen";
  const caps = h.connected ? app.getHostCapabilities() : undefined;
  const canFullscreen = h.context.availableDisplayModes?.includes("fullscreen");
  const pageWidth = Math.floor(
    fullscreen
      ? Math.min(width, (window.innerHeight - FULLSCREEN_CHROME) * PAGE_ASPECT)
      : Math.min(width, INLINE_MAX_WIDTH),
  );

  const meta = payload
    ? exportMeta(payload.application, payload.profile)
    : null;
  const title = payload
    ? [payload.application.posting_detail?.title, meta?.recipient.company]
        .filter(Boolean)
        .join(" · ")
    : "";

  function pickScope(next: ExportScope) {
    setScope(next);
    turnTo(1);
  }

  function turnTo(n: number) {
    setPage(n);
    setDrawError(null);
  }

  async function download() {
    if (!rendered?.built || id == null) return;
    setStatus("Preparing the download…");
    try {
      const bytes = new Uint8Array(await rendered.built.blob.arrayBuffer());
      const res = await app.downloadFile(
        {
          contents: [
            {
              type: "resource",
              resource: {
                uri: `file:///${fileStem(id, scope)}.pdf`,
                mimeType: "application/pdf",
                blob: toBase64(bytes),
              },
            },
          ],
        },
        { timeout: 600_000 },
      );
      setStatus(
        res.isError
          ? "The download didn't start (cancelled or refused)."
          : null,
      );
    } catch (err) {
      setStatus(`The download failed: ${errorText(err)}`);
    }
  }

  function openInJac() {
    if (!payload) return;
    void app
      .openLink({ url: payload.url })
      .catch((err) =>
        setStatus(`Claude didn't open the link: ${errorText(err)}`),
      );
  }

  function toggleFullscreen() {
    void app
      .requestDisplayMode({ mode: fullscreen ? "inline" : "fullscreen" })
      .catch((err) => setStatus(`Claude kept the size: ${errorText(err)}`));
  }

  const before = waiting(h, id);
  let body: ReactNode;
  if (before) body = before;
  else if (!load) body = <Message>Loading the application from jac…</Message>;
  else if (load.error)
    body = (
      <Message tone="error">
        jac couldn't hand over the application: {load.error}
      </Message>
    );
  else if (!rendered)
    body = (
      <Message>
        Rendering the PDF, page fit included. On a phone this takes a few
        seconds…
      </Message>
    );
  else if (rendered.blocked)
    body = <Message tone="error">{rendered.blocked}</Message>;
  else if (rendered.error)
    body = (
      <Message tone="error">
        Rendering failed: {rendered.error}. The application is saved: open it in
        jac to see and export it.
      </Message>
    );
  else if (drawError)
    body = <Message tone="error">Drawing the page failed: {drawError}</Message>;
  else if (rendered.doc)
    body = (
      <PageCanvas
        doc={rendered.doc}
        page={shown}
        width={pageWidth}
        onError={setDrawError}
      />
    );

  const notices =
    rendered?.built && rendered.spec && payload
      ? previewNotices(
          rendered.built,
          rendered.spec.cv.pages,
          scope,
          payload.attachments,
        )
      : [];

  return (
    <div className={fullscreen ? "preview fullscreen" : "preview"}>
      {payload && (
        <header className="toolbar">
          <div className="title" title={title}>
            {title || `Application ${payload.application.id}`}
          </div>
          <div className="controls">
            <div className="segmented" role="group" aria-label="What to show">
              {SCOPES.map((s) => (
                <button
                  key={s.value}
                  type="button"
                  aria-pressed={scope === s.value}
                  onClick={() => pickScope(s.value)}
                >
                  {s.label}
                </button>
              ))}
            </div>
            {pages > 1 && (
              <div className="pager">
                <button
                  type="button"
                  aria-label="Previous page"
                  disabled={shown <= 1}
                  onClick={() => turnTo(shown - 1)}
                >
                  ‹
                </button>
                <span>
                  {shown} / {pages}
                </span>
                <button
                  type="button"
                  aria-label="Next page"
                  disabled={shown >= pages}
                  onClick={() => turnTo(shown + 1)}
                >
                  ›
                </button>
              </div>
            )}
            {caps?.downloadFile && rendered?.built && (
              <button type="button" onClick={() => void download()}>
                Download PDF
              </button>
            )}
            {caps?.openLinks && (
              <button type="button" onClick={openInJac}>
                Open in jac
              </button>
            )}
            {canFullscreen && (
              <button type="button" onClick={toggleFullscreen}>
                {fullscreen ? "Exit full screen" : "Full screen"}
              </button>
            )}
          </div>
        </header>
      )}
      <div className="frame" ref={frame}>
        {body}
      </div>
      {(notices.length > 0 || status) && (
        <footer className="notes">
          {notices.map((n) => (
            <p
              key={n.text}
              className={n.level === "warning" ? "warning" : undefined}
            >
              {n.text}
            </p>
          ))}
          {status && <p>{status}</p>}
        </footer>
      )}
    </div>
  );
}
