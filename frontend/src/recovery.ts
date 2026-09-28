import type { CellJson, Kind, SaveState } from "./protocol";
import type { RecoveryDatabase } from "./recovery-db";

const MAX_TEXT = 1_000_000;
const MAX_RECORD = MAX_TEXT * 2 + 1000;
type CellSource = Pick<CellJson, "uid" | "kind" | "code" | "version">;
export interface RecoveryEntry {
  schema: 1;
  id: string;
  document: string;
  cell: string;
  kind: Kind;
  base: string;
  code: string;
  updated: number;
}
type Own = { entry: RecoveryEntry; slot: string; committed: boolean; failed?: boolean; accepted?: string;
  consumes?: RecoveryEntry; ready?: Promise<boolean>; clearing?: boolean };

function valid(value: unknown): value is RecoveryEntry {
  if (!value || typeof value !== "object") return false;
  const e = value as RecoveryEntry;
  try { return e.schema === 1 && typeof e.id === "string" && /^[0-9a-f-]{36}$/.test(e.id) &&
    typeof e.document === "string" && /^[0-9a-f]{64}$/.test(e.document) &&
    typeof e.cell === "string" && /^[0-9a-f]{32}$/.test(e.cell) &&
    ["python", "mojo", "markdown", "html"].includes(e.kind) &&
    typeof e.base === "string" && e.base.length <= MAX_TEXT &&
    typeof e.code === "string" && e.code.length <= MAX_TEXT && Number.isFinite(e.updated) &&
    JSON.stringify(e).length <= MAX_RECORD;
  } catch { return false; }
}

export class RecoveryStore {
  entries: RecoveryEntry[] = [];
  warning = "";
  private document = "";
  private session = "";
  private generation = 0;
  private own = new Map<string, Own>();
  private owned = new Map<string, string>(); // immutable key -> page/cell slot
  private written = new Map<string, RecoveryEntry>();
  private queue: Promise<unknown> = Promise.resolve();

  constructor(private db: RecoveryDatabase, private changed: () => void,
              private id = () => crypto.randomUUID(), private now = Date.now) {}

  get localStatus() {
    if (!this.own.size) return "";
    if ([...this.own.values()].some((d) => d.failed)) return "cópia local incompleta";
    return [...this.own.values()].every((d) => d.committed)
      ? "rascunho protegido neste navegador" : "protegendo rascunho…";
  }

  open(document: string, session = ""): Promise<boolean> {
    if (this.document === document && this.session === session) return Promise.resolve(true);
    if (this.document !== document) this.warning = "";
    this.entries = [];
    this.document = document;
    this.session = session;
    this.generation++;
    this.own.clear();
    this.owned.clear();
    this.changed();
    return this.refresh();
  }

  private fail() {
    this.warning = "Não foi possível atualizar a cópia de recuperação neste navegador. Mantenha a aba aberta até salvar no arquivo ou copie seu código.";
    this.changed();
  }

  private enqueue(work: () => Promise<boolean>) {
    const result = this.queue.then(work).catch(() => { this.fail(); return false; });
    this.queue = result;
    return result;
  }

  async settled() { await this.queue; }
  subscribe() { return this.db.subscribe?.(() => { void this.refresh(); }) ?? (() => {}); }

  private async load() {
    const document = this.document, generation = this.generation;
    const records = await this.db.read(document);
    if (this.generation !== generation) return;
    const consumed = new Set([...this.own.values()].map((d) => d.consumes?.id));
    const entries: RecoveryEntry[] = [];
    for (const entry of records) {
      if (!valid(entry) || entry.document !== document) {
        this.warning = "Há uma cópia de recuperação inválida neste navegador. Ela foi preservada, mas não será aplicada.";
      } else if (!this.owned.has(entry.id) && !consumed.has(entry.id)) entries.push(entry);
    }
    entries.sort((a, b) => a.updated - b.updated);
    this.entries = entries;
    this.changed();
  }

  refresh = () => this.enqueue(async () => { if (this.document) await this.load(); return true; });
  hasOwn(uid?: string) { return !!uid && this.own.has(uid); }

  record(cell: CellSource, code: string, base: string, consumes?: RecoveryEntry): Promise<boolean> {
    if (!this.document || !cell.uid || code.length > MAX_TEXT || base.length > MAX_TEXT) {
      this.fail(); return Promise.resolve(false);
    }
    const previous = this.own.get(cell.uid);
    if (previous?.entry.code === code && !consumes && !previous.clearing && !previous.failed)
      return previous.ready ?? Promise.resolve(previous.committed);
    const entry: RecoveryEntry = { schema: 1, id: this.id(), document: this.document, cell: cell.uid,
      kind: cell.kind, base: previous?.entry.base ?? base, code, updated: this.now() };
    if (!valid(entry)) { this.fail(); return Promise.resolve(false); }
    const generation = this.generation, slot = `${generation}:${cell.uid}`;
    const own: Own = { entry, slot, committed: false, consumes: consumes ?? previous?.consumes };
    this.own.set(cell.uid, own);
    this.owned.set(entry.id, slot);
    this.changed();
    own.ready = this.enqueue(async () => {
      const old = this.written.get(slot);
      try { await this.db.replace(entry, old); }
      catch (error) { own.failed = true; throw error; }
      this.written.set(slot, entry);
      own.committed = true;
      if (old) this.owned.delete(old.id);
      if (this.generation === generation) await this.load();
      return true;
    });
    return own.ready;
  }

  accepted(uid: string | undefined, code: string, version: string) {
    const own = uid && this.own.get(uid);
    if (own && own.entry.code === code && own.accepted !== version) {
      own.accepted = version;
      this.changed();
    }
  }

  discard(entry: RecoveryEntry) {
    if (entry.document !== this.document) return Promise.resolve(false);
    return this.enqueue(async () => { await this.db.remove([entry]); await this.load(); return true; });
  }

  discardOwn(uid?: string) {
    const own = uid && this.own.get(uid);
    if (!own) return;
    // Explicitly loading the server version abandons this local copy only.
    this.own.delete(uid!);
    void this.enqueue(async () => {
      const old = this.written.get(own.slot);
      await this.db.remove([own.entry, ...(old ? [old] : [])]);
      this.owned.delete(own.entry.id);
      if (old) this.owned.delete(old.id);
      await this.load();
      return true;
    });
  }

  cancelRecovery(uid: string | undefined, entryId: string, code: string) {
    const own = uid ? this.own.get(uid) : undefined;
    if (own?.consumes?.id !== entryId) return;
    own.consumes = undefined; // the original offer has never been deleted
    if (own.entry.code === code && !own.accepted) this.discardOwn(uid);
    else void this.refresh();
  }

  async recover(entry: RecoveryEntry, cell: CellSource): Promise<boolean> {
    if (entry.document !== this.document || !cell.uid || this.hasOwn(cell.uid)) return false;
    const generation = this.generation;
    const current = await this.enqueue(async () => (await this.db.read(entry.document))
      .some((e) => valid(e) && JSON.stringify(e) === JSON.stringify(entry)));
    if (!current || generation !== this.generation || this.hasOwn(cell.uid)) return false;
    const ready = this.record(cell, entry.code, cell.code, entry);
    const own = this.own.get(cell.uid);
    if (!await ready || generation !== this.generation || this.own.get(cell.uid) !== own) {
      this.cancelRecovery(cell.uid, entry.id, entry.code);
      return false;
    }
    return true; // keep the original offer until the chosen text is durably saved in the notebook
  }

  sync(cells: CellJson[], save: SaveState | null, connected: boolean) {
    if (!connected) return;
    for (const [uid, own] of this.own) {
      const cell = cells.find((c) => c.uid === uid);
      if (!cell) {
        this.own.delete(uid);
        for (const [id, slot] of this.owned) if (slot === own.slot) this.owned.delete(id);
        void this.refresh();
      } else if (!own.clearing && own.committed && save?.status === "saved" && save.revision === save.saved_revision &&
          own.accepted === cell.version && own.entry.code === cell.code && own.entry.kind === cell.kind) {
        own.clearing = true;
        void this.enqueue(async () => {
          // Failure retains the copy without an automatic retry loop.
          await this.db.remove([own.entry, ...(own.consumes ? [own.consumes] : [])]);
          if (this.own.get(uid) === own) this.own.delete(uid);
          if (this.written.get(own.slot)?.id === own.entry.id) this.written.delete(own.slot);
          this.owned.delete(own.entry.id);
          await this.load();
          return true;
        });
      }
    }
  }
}
