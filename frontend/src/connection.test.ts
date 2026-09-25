import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ACK_TIMEOUT_MS, Connection, type Deps } from "./connection";

class Socket {
  static all: Socket[] = [];
  readyState = 0;
  onopen?: () => void;
  onclose?: () => void;
  onmessage?: (event: { data: string }) => void;
  sent: Record<string, unknown>[] = [];
  constructor() { Socket.all.push(this); }
  send(data: string) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 2; } // a broken peer may never finish the close handshake
  open() { this.readyState = 1; this.onopen?.(); }
  disconnect() { this.readyState = 3; this.onclose?.(); }
  receive(m: unknown) { this.onmessage?.({ data: JSON.stringify(m) }); }
}

function setup() {
  const onStatus = vi.fn(), onMessage = vi.fn(), onNotice = vi.fn();
  const deps: Deps = {
    WS: Socket as unknown as Deps["WS"], now: Date.now,
    setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: (t) => clearTimeout(t as number),
  };
  const c = new Connection("ws://test", { onStatus, onMessage, onNotice }, deps);
  return { c, onStatus, onMessage, onNotice, socket: Socket.all[0] };
}

beforeEach(() => { vi.useFakeTimers(); Socket.all = []; });
afterEach(() => { vi.useRealTimers(); });

describe("connection lifecycle", () => {
  it("writes to an OPEN socket even before a snapshot has set UI connected", () => {
    const { c, socket, onStatus } = setup();
    c.send({ type: "stop" });
    expect(socket.sent).toEqual([]);
    socket.open();
    expect(onStatus).toHaveBeenLastCalledWith(true);
    expect(socket.sent).toHaveLength(1);
    c.send({ type: "run", cid: 1 });
    expect(socket.sent).toHaveLength(2);
    c.close();
  });

  it("ignores a late open, message and close after disposal (React effect cleanup)", () => {
    const { c, socket, onStatus, onMessage } = setup();
    c.send({ type: "stop" });
    c.close();
    socket.open();
    socket.receive({ type: "error", error: "old connection" });
    socket.disconnect();
    vi.advanceTimersByTime(10000);
    expect(socket.sent).toEqual([]);
    expect(onStatus).not.toHaveBeenCalled();
    expect(onMessage).not.toHaveBeenCalled();
    expect(Socket.all).toHaveLength(1);
  });

  it("reconnects without a close event after missing ACK, without replaying a sent run", () => {
    const { c, socket, onStatus, onMessage, onNotice } = setup();
    socket.open();
    c.send({ type: "run", cid: 1 });
    vi.advanceTimersByTime(ACK_TIMEOUT_MS);
    expect(onStatus).toHaveBeenLastCalledWith(false);
    expect(onNotice).toHaveBeenCalledOnce();
    c.send({ type: "stop" });
    vi.advanceTimersByTime(500);
    const replacement = Socket.all[1];
    replacement.open();
    expect(replacement.sent.map((m) => m.type)).toEqual(["stop"]);
    onMessage.mockClear();
    socket.receive({ type: "error", error: "late" });
    socket.disconnect();
    expect(onMessage).not.toHaveBeenCalled();
    expect(onStatus).toHaveBeenLastCalledWith(true);
    replacement.receive({ type: "ack", seq: replacement.sent[0].seq });
    vi.advanceTimersByTime(10000);
    expect(Socket.all).toHaveLength(2);
    c.close();
  });

  it("keeps reconnecting status consistent and sends new actions immediately on open", () => {
    const { c, socket, onStatus } = setup();
    socket.open();
    socket.disconnect();
    expect(onStatus).toHaveBeenLastCalledWith(false);
    c.send({ type: "run", cid: 2 });
    vi.advanceTimersByTime(500);
    const replacement = Socket.all[1];
    replacement.open();
    c.send({ type: "stop" });
    expect(onStatus).toHaveBeenLastCalledWith(true);
    expect(replacement.sent.map((m) => m.type)).toEqual(["run", "stop"]);
    c.close();
  });
});
