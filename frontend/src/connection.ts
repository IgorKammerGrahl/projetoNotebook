// Transport lifecycle is independent of React and injectable for deterministic tests.
// ACK confirms dispatch, not completion. Already-written commands are never replayed:
// a missing ACK cannot tell us whether a side effect ran before the connection failed.
import type { ClientMsg, ServerMsg } from "./protocol";

export const ACK_TIMEOUT_MS = 3000;
export interface Deps {
  WS: new (url: string) => WebSocket;
  now: () => number;
  setTimeout: (fn: () => void, ms: number) => unknown;
  clearTimeout: (t: unknown) => void;
}
export interface Handlers {
  onMessage: (m: ServerMsg) => void;
  onStatus: (connected: boolean) => void;
  onNotice?: (text: string) => void;
}
interface Pending { msg: ClientMsg; queuedAt: number }
const browserDeps: Deps = {
  WS: WebSocket, now: () => Date.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: (t) => clearTimeout(t as number),
};

export class Connection {
  private sock: WebSocket | null = null;
  private seq = 0;
  private pending: Pending[] = [];
  private awaiting = new Map<number, number>();
  private delay = 250;
  private closed = false;
  private retry: unknown = null;
  private watchdog: unknown = null;

  constructor(private url: string, private h: Handlers, private d: Deps = browserDeps) {
    this.connect();
  }

  send(msg: ClientMsg) {
    if (this.closed) return;
    if (msg.type === "edit") {
      this.pending = this.pending.filter((p) => !(p.msg.type === "edit" && p.msg.cid === msg.cid));
    }
    this.pending.push({ msg, queuedAt: this.d.now() });
    this.flush(); // OPEN, not a possibly stale React connected flag, controls delivery
  }

  close() {
    this.closed = true;
    this.d.clearTimeout(this.retry);
    this.d.clearTimeout(this.watchdog);
    const sock = this.sock;
    this.sock = null; // late events from this socket must not affect the next effect
    sock?.close();
  }

  private reconnect(sock: WebSocket) {
    if (this.closed || this.sock !== sock) return;
    this.sock = null;
    this.d.clearTimeout(this.watchdog);
    this.watchdog = null;
    if (this.awaiting.size) {
      this.h.onNotice?.("Conexão perdida sem confirmação. Verifique o resultado antes de executar novamente.");
      this.awaiting.clear();
    }
    this.h.onStatus(false);
    sock.close();
    this.retry = this.d.setTimeout(() => this.connect(), (this.delay = Math.min(this.delay * 2, 5000)));
  }

  private armWatchdog() {
    if (this.watchdog !== null || !this.awaiting.size) return;
    const oldest = Math.min(...this.awaiting.values());
    this.watchdog = this.d.setTimeout(() => {
      this.watchdog = null;
      if (this.sock && this.awaiting.size && this.d.now() - Math.min(...this.awaiting.values()) >= ACK_TIMEOUT_MS) {
        this.reconnect(this.sock); // do not wait for an unresponsive peer's close handshake
      } else this.armWatchdog();
    }, Math.max(0, ACK_TIMEOUT_MS - (this.d.now() - oldest)));
  }

  private connect() {
    if (this.closed) return;
    const sock = new this.d.WS(this.url);
    this.sock = sock;
    sock.onopen = () => {
      if (this.closed || this.sock !== sock) return;
      this.delay = 250;
      this.h.onStatus(true);
      this.flush();
    };
    sock.onmessage = (e: MessageEvent) => {
      if (this.closed || this.sock !== sock) return;
      const m = JSON.parse(e.data) as ServerMsg;
      if (m.type === "ack" || (m.type === "error" && m.seq !== undefined)) {
        this.awaiting.delete(m.seq!);
        this.d.clearTimeout(this.watchdog);
        this.watchdog = null;
        this.armWatchdog();
      }
      if (m.type === "error") this.h.onNotice?.(m.error);
      this.h.onMessage(m);
    };
    sock.onclose = () => this.reconnect(sock);
  }

  private flush() {
    while (this.pending.length && this.sock?.readyState === 1) {
      const p = this.pending[0];
      const seq = ++this.seq;
      try {
        this.sock.send(JSON.stringify({ ...p.msg, seq }));
      } catch {
        this.reconnect(this.sock);
        return; // send threw: keep this unsent message queued
      }
      this.pending.shift();
      this.awaiting.set(seq, this.d.now());
      this.armWatchdog();
    }
  }
}
