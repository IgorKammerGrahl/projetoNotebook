// Transport lifecycle is independent of React and injectable for deterministic tests.
// ACK confirms dispatch, not completion. Only version-checked edits may be retried;
// a missing ACK cannot tell us whether an execution ran before the connection failed.
import type { ClientMsg, ServerMsg } from "./protocol";

export const ACK_TIMEOUT_MS = 3000;
export const RUN_TTL_MS = 5000;
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
  onPendingChanges?: (pending: boolean) => void;
}
interface Pending { msg: ClientMsg; queuedAt: number; session?: string }
const browserDeps: Deps = {
  WS: WebSocket, now: () => Date.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: (t) => clearTimeout(t as number),
};

export class Connection {
  private sock: WebSocket | null = null;
  private seq = 0;
  private pending: Pending[] = [];
  private awaiting = new Map<number, Pending & { sentAt: number }>();
  private delay = 250;
  private closed = false;
  private retry: unknown = null;
  private watchdog: unknown = null;
  private session?: string;

  constructor(private url: string, private h: Handlers, private d: Deps = browserDeps) {
    this.connect();
  }

  send(msg: ClientMsg, queuedAt = this.d.now()) {
    if (this.closed) return;
    if (["preserve_copy", "reload_external", "reload_ready"].includes(msg.type) && this.sock?.readyState !== 1) {
      this.h.onNotice?.("Aguarde reconectar para resolver o conflito do arquivo.");
      return; // an explicit resolution must never become an offline command
    }
    if (msg.type === "edit") {
      this.pending = this.pending.filter((p) => !(p.msg.type === "edit" && p.msg.cid === msg.cid));
    }
    this.pending.push({ msg, queuedAt, session: "session" in msg ? msg.session : this.session });
    this.reportPendingChanges();
    this.flush(); // OPEN, not a possibly stale React connected flag, controls delivery
  }

  discardEdits(cid: number) {
    this.pending = this.pending.filter((p) => p.msg.type !== "edit" || p.msg.cid !== cid);
    this.reportPendingChanges();
  }

  discardAddition(request: string) {
    this.pending = this.pending.filter((p) => p.msg.type !== "add" || p.msg.request !== request);
    this.reportPendingChanges();
  }

  get hasPendingChanges() {
    const changesFile = (p: Pending) => p.msg.type === "edit" || p.msg.type === "add" || p.msg.type === "delete";
    return this.pending.some(changesFile) || [...this.awaiting.values()].some(changesFile);
  }

  private reportPendingChanges() {
    this.h.onPendingChanges?.(this.hasPendingChanges);
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
      // Version checks make edit retries safe; execution side effects cannot be replayed.
      const edits = [...this.awaiting.values()].filter((p) => p.msg.type === "edit");
      this.pending = [...edits, ...this.pending];
      this.awaiting.clear();
    }
    this.h.onStatus(false);
    this.reportPendingChanges();
    sock.close();
    this.retry = this.d.setTimeout(() => this.connect(), (this.delay = Math.min(this.delay * 2, 5000)));
  }

  private armWatchdog() {
    if (this.watchdog !== null || !this.awaiting.size) return;
    const oldest = Math.min(...[...this.awaiting.values()].map((p) => p.sentAt));
    this.watchdog = this.d.setTimeout(() => {
      this.watchdog = null;
      if (this.sock && this.awaiting.size && this.d.now() - Math.min(...[...this.awaiting.values()].map((p) => p.sentAt)) >= ACK_TIMEOUT_MS) {
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
      if (m.type === "snapshot") this.session = m.document?.session;
      if (m.type === "notice") this.h.onNotice?.(m.text);
      if (m.type === "ack" || ((m.type === "error" || m.type === "conflict") && m.seq !== undefined)) {
        this.awaiting.delete(m.seq!);
        this.d.clearTimeout(this.watchdog);
        this.watchdog = null;
        this.armWatchdog();
        this.reportPendingChanges();
      }
      if (m.type === "error") this.h.onNotice?.(m.error);
      if (m.type === "conflict" && !m.cell) this.h.onNotice?.("A célula editada foi removida no servidor; a edição não foi aplicada.");
      this.h.onMessage(m);
    };
    sock.onclose = () => this.reconnect(sock);
  }

  private flush() {
    while (this.pending.length && this.sock?.readyState === 1) {
      const p = this.pending[0];
      if (["run", "run_all", "stop", "restart"].includes(p.msg.type) && this.d.now() - p.queuedAt > RUN_TTL_MS) {
        this.pending.shift();
        this.h.onNotice?.("Ação de execução descartada: ficou mais de 5 s na fila offline. Execute novamente se desejar.");
        continue;
      }
      const seq = ++this.seq;
      try {
        this.sock.send(JSON.stringify({ ...p.msg, session: p.session, seq }));
      } catch {
        this.reconnect(this.sock);
        return; // send threw: keep this unsent message queued
      }
      this.pending.shift();
      this.awaiting.set(seq, { ...p, sentAt: this.d.now() });
      this.armWatchdog();
    }
  }
}
