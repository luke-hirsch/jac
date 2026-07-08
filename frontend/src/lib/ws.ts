/** Open the per-run progress socket. Auth rides the same-origin session cookie on the
 *  handshake (no token). Returns a close fn. `onEvent` gets each parsed JSON message. */
export function openGenerationSocket(
  id: number,
  onEvent: (data: unknown) => void,
): () => void {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${scheme}://${location.host}/ws/jac/generations/${id}/`);
  ws.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch {
      /* ignore non-JSON frames */
    }
  };
  return () => ws.close();
}
