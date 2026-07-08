/** Per-run progress socket with auto-reconnect. Auth rides the same-origin session cookie
 *  on the handshake (no token). On every (re)connect the server pushes a fresh snapshot,
 *  so missed events reconcile themselves. */

export type SocketStatus =
  | { kind: "connecting" }
  | { kind: "open" }
  | { kind: "retrying"; attempt: number; delayMs: number }
  | { kind: "closed" }; // deliberate close, or a close we must not retry (auth)

/** Close codes that must not trigger a reconnect: normal closure, and the consumer's
 *  auth/ownership rejections (4401 unauthenticated, 4404 not the owner). */
export function shouldReconnect(code: number): boolean {
  return code !== 1000 && code !== 4401 && code !== 4404;
}

/** Exponential backoff: 1s, 2s, 4s, 8s, then capped at 15s. */
export function reconnectDelayMs(attempt: number): number {
  return Math.min(1000 * 2 ** attempt, 15000);
}

export function openGenerationSocket(
  id: number,
  onEvent: (data: unknown) => void,
  onStatus?: (status: SocketStatus) => void,
): () => void {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const url = `${scheme}://${location.host}/ws/jac/generations/${id}/`;
  let ws: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let attempt = 0;
  let closedByCaller = false;

  const connect = () => {
    onStatus?.({ kind: "connecting" });
    ws = new WebSocket(url);
    ws.onopen = () => {
      attempt = 0;
      onStatus?.({ kind: "open" });
    };
    ws.onmessage = (e) => {
      try {
        onEvent(JSON.parse(e.data));
      } catch {
        /* ignore non-JSON frames */
      }
    };
    ws.onclose = (e) => {
      // After a caller close this socket is stale — a newer one may already own the
      // status callback, so stay silent instead of overwriting it with "closed".
      if (closedByCaller) return;
      if (!shouldReconnect(e.code)) {
        onStatus?.({ kind: "closed" });
        return;
      }
      const delayMs = reconnectDelayMs(attempt);
      onStatus?.({ kind: "retrying", attempt, delayMs });
      attempt += 1;
      timer = setTimeout(connect, delayMs);
    };
  };

  connect();
  return () => {
    closedByCaller = true;
    clearTimeout(timer);
    ws?.close();
  };
}
