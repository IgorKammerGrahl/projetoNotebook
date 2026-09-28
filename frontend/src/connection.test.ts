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
  const onStatus = vi.fn(), onMessage = vi.fn(), onNotice = vi.fn(), onPendingChanges = vi.fn();
  const deps: Deps = {
    WS: Socket as unknown as Deps["WS"], now: Date.now,
    setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: (t) => clearTimeout(t as number),
  };
  const c = new Connection("ws://test", { onStatus, onMessage, onNotice, onPendingChanges }, deps);
  return { c, onStatus, onMessage, onNotice, onPendingChanges, socket: Socket.all[0] };
}

beforeEach(() => { vi.useFakeTimers(); Socket.all = []; });
afterEach(() => { vi.useRealTimers(); });

describe("connection lifecycle", () => {
  it("cancels an unsent recovery addition before reconnection without discarding other work", () => {
    const { c, socket } = setup();
    c.send({ type: "add", kind: "python", code: "recovered", after: null, request: "recovery" });
    c.send({ type: "add", kind: "markdown", code: "other", after: null, request: "other" });
    c.discardAddition("recovery");
    socket.open();
    expect(socket.sent.map((m) => m.request)).toEqual(["other"]);
    c.close();
  });
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

it.each([4999, 5001])("only replays offline executions younger than 5 s (%d ms)", (ms) => {
  const { c, socket, onNotice } = setup();
  c.send({ type: "run", cid: 1 });
  c.send({ type: "run_all" });
  c.send({ type: "edit", cid: 1, code: "offline", base_version: "original-version", request: "edit-1" });
  vi.advanceTimersByTime(ms);
  socket.open();
  expect(socket.sent.filter((m) => m.type === "run" || m.type === "run_all")).toHaveLength(ms < 5000 ? 2 : 0);
  expect(socket.sent.find((m) => m.type === "edit")).toMatchObject({ base_version: "original-version", code: "offline" });
  expect(onNotice).toHaveBeenCalledTimes(ms > 5000 ? 2 : 0);
  c.close();
});

it("retries only versioned edits after a lost ACK, retaining the original base and request", () => {
  const { c, socket } = setup();
  socket.open();
  c.send({ type: "edit", cid: 1, code: "mine", base_version: "v1", request: "edit-1" });
  socket.disconnect();
  vi.advanceTimersByTime(500);
  const replacement = Socket.all[1];
  replacement.open();
  expect(replacement.sent[0]).toMatchObject({ code: "mine", base_version: "v1", request: "edit-1" });
  c.close();
});

it.each(["add", "delete", "edit"] as const)("tracks unconfirmed %s even if the server reports an older snapshot as saved", (type) => {
  const { c, socket, onPendingChanges } = setup();
  if (type === "add") c.send({ type, code: "", kind: "markdown", after: null });
  else if (type === "delete") c.send({ type, cid: 1 });
  else c.send({ type, cid: 1, code: "mine", base_version: "v1", request: "r1" });
  expect(onPendingChanges).toHaveBeenLastCalledWith(true);
  socket.open();
  socket.receive({ type: "save_status", save: { status: "saved", revision: 0, saved_revision: 0, error: null } });
  expect(onPendingChanges).toHaveBeenLastCalledWith(true);
  socket.receive({ type: "ack", seq: socket.sent[0].seq });
  expect(onPendingChanges).toHaveBeenLastCalledWith(false);
  c.close();
});

it("keeps a resent edit pending until acknowledged on the replacement socket", () => {
  const { c, socket, onPendingChanges } = setup();
  socket.open();
  c.send({ type: "edit", cid: 1, code: "mine", base_version: "v1", request: "r1" });
  socket.disconnect();
  expect(onPendingChanges).toHaveBeenLastCalledWith(true);
  vi.advanceTimersByTime(500);
  const replacement = Socket.all[1];
  replacement.open();
  socket.receive({ type: "ack", seq: socket.sent[0].seq });
  expect(onPendingChanges).toHaveBeenLastCalledWith(true);
  replacement.receive({ type: "ack", seq: replacement.sent[0].seq });
  expect(onPendingChanges).toHaveBeenLastCalledWith(false);
  c.close();
});

it("discarding an offline draft clears pending changes; execution actions do not dirty the file", () => {
  const { c, onPendingChanges } = setup();
  c.send({ type: "run", cid: 1 });
  expect(onPendingChanges).toHaveBeenLastCalledWith(false);
  c.send({ type: "edit", cid: 1, code: "mine", base_version: "v1", request: "r1" });
  expect(onPendingChanges).toHaveBeenLastCalledWith(true);
  c.discardEdits(1);
  expect(onPendingChanges).toHaveBeenLastCalledWith(false);
  c.close();
});
