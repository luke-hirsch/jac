/**
 * fflate's async `unzlib`, on this thread. png-js (react-pdf's PNG decoder: the signature, the
 * QR) inflates through it, and fflate runs that in a Web Worker started from a blob: URL. Claude's
 * sandbox refuses the worker, and the callback never comes: the render waits forever. The
 * connector build hands png-js this module instead of fflate (vite.connector.config.ts); the
 * SPA keeps the worker. Same signature, same callback, a microtask later.
 */
import { unzlibSync } from "fflate";

type Callback = (err: Error | null, data: Uint8Array | null) => void;

export function unzlib(
  data: Uint8Array,
  optsOrCb: Callback | object,
  maybeCb?: Callback,
): () => void {
  const cb = (typeof optsOrCb === "function" ? optsOrCb : maybeCb) as Callback;
  queueMicrotask(() => {
    let out: Uint8Array;
    try {
      out = unzlibSync(data);
    } catch (err) {
      cb(err as Error, null);
      return;
    }
    cb(null, out);
  });
  return () => {}; // fflate hands back a terminate function; nothing runs to stop
}
