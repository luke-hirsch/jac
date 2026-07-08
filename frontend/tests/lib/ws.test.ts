import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  openGenerationSocket,
  reconnectDelayMs,
  shouldReconnect,
  type SocketStatus,
} from "@/lib/ws";

/**
 * The reconnect policy is pure (delay schedule + close-code gate); the socket
 * lifecycle is exercised against a minimal fake WebSocket with fake timers —
 * no network, no DOM beyond a stubbed `location`.
 */

describe("reconnectDelayMs", () => {
  it("doubles from 1s and caps at 15s", () => {
    expect(reconnectDelayMs(0)).toBe(1000);
    expect(reconnectDelayMs(1)).toBe(2000);
    expect(reconnectDelayMs(2)).toBe(4000);
    expect(reconnectDelayMs(3)).toBe(8000);
    expect(reconnectDelayMs(4)).toBe(15000);
    expect(reconnectDelayMs(10)).toBe(15000);
  });
});

describe("shouldReconnect", () => {
  it("never retries a normal close or an auth/ownership rejection", () => {
    expect(shouldReconnect(1000)).toBe(false);
    expect(shouldReconnect(4401)).toBe(false);
    expect(shouldReconnect(4404)).toBe(false);
  });
  it("retries abnormal closes (server restart, dropped connection)", () => {
    expect(shouldReconnect(1006)).toBe(true);
    expect(shouldReconnect(1011)).toBe(true);
  });
});

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  close = vi.fn();
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
}

describe("openGenerationSocket", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("location", { protocol: "http:", host: "app.test" });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  function open(onEvent = () => {}) {
    const statuses: SocketStatus[] = [];
    const close = openGenerationSocket(7, onEvent, (s) => statuses.push(s));
    return { statuses, close };
  }

  it("connects to the run's ws path and parses JSON events", () => {
    const events: unknown[] = [];
    open((d) => events.push(d));
    const ws = FakeWebSocket.instances[0];
    expect(ws.url).toBe("ws://app.test/ws/jac/generations/7/");
    ws.onmessage?.({ data: JSON.stringify({ event: "progress", stage: "x" }) });
    ws.onmessage?.({ data: "not json" }); // ignored, no throw
    expect(events).toEqual([{ event: "progress", stage: "x" }]);
  });

  it("reconnects with backoff after an abnormal close and reports status", () => {
    const { statuses } = open();
    const first = FakeWebSocket.instances[0];
    first.onopen?.();
    first.onclose?.({ code: 1006 });

    expect(statuses.at(-1)).toEqual({ kind: "retrying", attempt: 0, delayMs: 1000 });
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1000);
    expect(FakeWebSocket.instances).toHaveLength(2);

    // A second failure backs off further; a successful open resets the schedule.
    FakeWebSocket.instances[1].onclose?.({ code: 1006 });
    expect(statuses.at(-1)).toEqual({ kind: "retrying", attempt: 1, delayMs: 2000 });
    vi.advanceTimersByTime(2000);
    FakeWebSocket.instances[2].onopen?.();
    FakeWebSocket.instances[2].onclose?.({ code: 1006 });
    expect(statuses.at(-1)).toEqual({ kind: "retrying", attempt: 0, delayMs: 1000 });
  });

  it("gives up on auth rejection", () => {
    const { statuses } = open();
    FakeWebSocket.instances[0].onclose?.({ code: 4401 });
    expect(statuses.at(-1)).toEqual({ kind: "closed" });
    vi.advanceTimersByTime(60000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("does not reconnect after the caller closes", () => {
    const { close } = open();
    const ws = FakeWebSocket.instances[0];
    close();
    expect(ws.close).toHaveBeenCalled();
    ws.onclose?.({ code: 1006 }); // the close event still fires
    vi.advanceTimersByTime(60000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
