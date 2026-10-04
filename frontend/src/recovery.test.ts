import { describe, expect, it, vi } from "vitest";
import { RecoveryStore, type RecoveryEntry } from "./recovery";
import type { RecoveryDatabase } from "./recovery-db";
import { Draft } from "./draft";
import type { CellJson, SaveState } from "./protocol";

const document = "a".repeat(64);
const source = (code = "a = 1", version = "v1"): CellJson => ({ uid: "b".repeat(32),
  id: 1, kind: "python", code, version, edit_id: null, status: "ok", error: "", output: "",
  previews: {}, images: [], duration_ms: null, defs: [], refs: [], upstream_modified: [], compiling: false, diagnostics: [], queue_position: null });
const saved: SaveState = { status: "saved", revision: 1, saved_revision: 1, error: null };
class Database implements RecoveryDatabase {
  records = new Map<string, RecoveryEntry>();
  async read(doc: string) { return [...this.records.values()].filter((e) => e.document === doc); }
  async replace(entry: RecoveryEntry, previous?: RecoveryEntry) {
    if (this.records.has(entry.id)) throw new Error("duplicate key");
    this.records.set(entry.id, entry);
    if (previous) await this.remove([previous]);
  }
  async remove(entries: RecoveryEntry[]) {
    for (const e of entries) if (JSON.stringify(this.records.get(e.id)) === JSON.stringify(e)) this.records.delete(e.id);
  }
}
async function store(db: RecoveryDatabase) {
  const s = new RecoveryStore(db, vi.fn());
  await s.open(document, "server-1");
  return s;
}

function deferred() {
  let resolve!: (value: boolean) => void;
  const promise = new Promise<boolean>((r) => { resolve = r; });
  return { promise, resolve };
}

describe("durable local drafts", () => {
  it("commits typing before sending and retains later typing until that exact version is saved", async () => {
    const db = new Database(), s = await store(db), c = source();
    const edit = vi.fn((m) => expect([...db.records.values()].some((e) => e.code === m.code)).toBe(true));
    const draft = new Draft(c, { edit, run: vi.fn(), cancel: vi.fn(), notice: vi.fn(),
      persist: (code, base) => s.record(c, code, base),
      accepted: (code, version) => s.accepted(c.uid, code, version) });
    draft.change("first");
    expect(edit).not.toHaveBeenCalled();
    await s.settled();
    expect(edit).toHaveBeenCalledOnce();
    draft.change("last");
    await s.settled();
    expect(edit).toHaveBeenCalledOnce(); // first edit is still awaiting server acceptance
    expect((await store(db)).entries[0]).toMatchObject({ code: "last", base: "a = 1" });
    s.sync([source("last", "v2")], saved, true);
    await s.settled();
    expect(db.records.size).toBe(1); // matching text alone does not prove this draft was accepted
    draft.observe({ ...source("first", "v2"), edit_id: edit.mock.calls[0][0].request });
    expect(edit).toHaveBeenCalledTimes(2);
    s.sync([source("first", "v2")], saved, true);
    await s.settled();
    expect(db.records.size).toBe(1);
    draft.observe({ ...source("last", "v3"), edit_id: edit.mock.calls[1][0].request });
    s.sync([source("last", "v3")], { ...saved, status: "saving", revision: 2 }, true);
    await s.settled();
    expect(db.records.size).toBe(1);
    s.sync([source("last", "v3")], saved, false);
    await s.settled();
    expect(db.records.size).toBe(1);
    s.sync([source("last", "v3")], saved, true);
    await s.settled();
    expect(db.records.size).toBe(0);
  });

  it("does not confuse a reversion with an old saved snapshot", async () => {
    const db = new Database(), s = await store(db), c = source();
    await s.record(c, "changed", c.code);
    s.accepted(c.uid, "changed", "v2");
    await s.record(c, c.code, c.code);
    s.sync([c], saved, true);
    await s.settled();
    expect(db.records.size).toBe(1);
  });

  it("isolates notebooks and tabs and cannot erase a later immutable version", async () => {
    const db = new Database(), a = await store(db), b = await store(db), c = source();
    await a.record(c, "A", c.code);
    await b.refresh();
    const offered = b.entries[0];
    await b.record(c, "B", c.code);
    await a.record(c, "A latest", c.code);
    await b.discard(offered);
    expect(db.records.size).toBe(2);
    const fresh = await store(db);
    expect(fresh.entries.map((e) => e.code).sort()).toEqual(["A latest", "B"]);
    await fresh.open("c".repeat(64));
    expect(fresh.entries).toEqual([]);
    await a.open(document, "server-2");
    expect(a.entries).toHaveLength(2);
  });

  it("preserves the original offer if writing a recovery destination fails", async () => {
    const db = new Database(), a = await store(db), c = source();
    await a.record(c, "backup", c.code);
    const fresh = await store(db), entry = fresh.entries[0];
    db.replace = async () => { throw new Error("quota"); };
    expect(await fresh.recover(entry, c)).toBe(false);
    await fresh.settled();
    expect(fresh.warning).toContain("Não foi possível");
    expect((await store(db)).entries[0].code).toBe("backup");
  });

  it("keeps the last committed copy across failure and removes it only after a later write succeeds", async () => {
    const db = new Database(), s = await store(db), c = source(), replace = db.replace.bind(db);
    await s.record(c, "committed", c.code);
    db.replace = async () => { throw new Error("quota"); };
    expect(await s.record(c, "failed", c.code)).toBe(false);
    expect((await store(db)).entries[0].code).toBe("committed");
    expect(s.localStatus).toBe("cópia local incompleta");
    db.replace = replace;
    await s.record(c, "latest", c.code);
    expect((await store(db)).entries.map((e) => e.code)).toEqual(["latest"]);
  });

  it("reports unavailable storage and preserves malformed records without applying them", async () => {
    const db = new Database();
    db.read = async () => { throw new Error("blocked"); };
    expect((await store(db)).warning).toContain("Não foi possível");
    const malformed = new Database();
    malformed.records.set("broken", { document, code: "bad" } as RecoveryEntry);
    const fresh = await store(malformed);
    expect(fresh.entries).toEqual([]);
    expect(fresh.warning).toContain("inválida");
    expect(malformed.records.size).toBe(1);
  });

  it("clears foreign offers on a notebook switch even when reading fails", async () => {
    const db = new Database(), a = await store(db), c = source();
    await a.record(c, "draft for A", c.code);
    const b = await store(db), foreign = b.entries[0], read = db.read.bind(db);
    db.read = async () => { throw new Error("blocked"); };
    await b.open("c".repeat(64));
    expect(b.entries).toEqual([]);
    db.read = read;
    expect(await b.recover(foreign, c)).toBe(false);
    await b.discard(foreign);
    expect((await store(db)).entries[0].code).toBe("draft for A");
  });

  it("rejects oversized records before replacing their readable predecessor", async () => {
    const db = new Database(), s = await store(db), c = source();
    await s.record(c, "backup", c.code);
    expect(await s.record(c, "\u0000".repeat(400_000), c.code)).toBe(false);
    expect(s.warning).toContain("Não foi possível");
    expect((await store(db)).entries[0].code).toBe("backup");
  });

  it("offers removed cells, keeping both copies until the recovered destination is saved", async () => {
    const db = new Database(), s = await store(db), c = source();
    await s.record(c, "recover", c.code);
    s.sync([], saved, true);
    await s.settled();
    expect(s.entries).toHaveLength(1);
    const entry = s.entries[0], other = { ...c, uid: "d".repeat(32) };
    expect(await s.recover(entry, other)).toBe(true);
    expect(db.records.size).toBe(2);
    expect(s.entries).toHaveLength(0); // selected source is hidden, still retained on disk
    s.accepted(other.uid, "recover", "v2");
    s.sync([{ ...other, code: "recover", version: "v2" }], saved, true);
    await s.settled();
    expect(db.records.size).toBe(0);
  });

  it("refuses recovery if new typing arrives during the asynchronous read", async () => {
    const db = new Database(), a = await store(db), c = source();
    await a.record(c, "offered", c.code);
    const b = await store(db), entry = b.entries[0], read = db.read.bind(db);
    const entered = deferred(), release = deferred();
    let first = true;
    db.read = async (doc) => {
      if (first) { first = false; entered.resolve(true); await release.promise; }
      return read(doc);
    };
    const recovery = b.recover(entry, c);
    await entered.promise;
    const typing = b.record(c, "new typing", c.code);
    release.resolve(true);
    expect(await recovery).toBe(false);
    await typing;
    expect((await store(db)).entries.map((e) => e.code).sort()).toEqual(["new typing", "offered"]);
  });

  it("does not spin retrying a failed cleanup", async () => {
    const db = new Database(), s = await store(db), c = source();
    await s.record(c, "saved", c.code);
    s.accepted(c.uid, "saved", "v2");
    db.remove = vi.fn(async () => { throw new Error("unavailable"); });
    s.sync([source("saved", "v2")], saved, true);
    await s.settled();
    s.sync([source("saved", "v2")], saved, true);
    await s.settled();
    expect(db.remove).toHaveBeenCalledOnce();
    expect(db.records.size).toBe(1);
  });

  it("writes a fresh copy when focused text needs protection while its saved copy is being removed", async () => {
    const db = new Database(), s = await store(db), c = source();
    await s.record(c, "X", c.code);
    s.accepted(c.uid, "X", "v2");
    const entered = deferred(), release = deferred(), remove = db.remove.bind(db);
    let first = true;
    db.remove = async (entries) => {
      if (first) { first = false; entered.resolve(true); await release.promise; }
      await remove(entries);
    };
    s.sync([source("X", "v2")], saved, true);
    await entered.promise;
    // A remote edit replaces X with Y, while the focused local editor retains X.
    const protectedAgain = s.record(source("Y", "v3"), "X", "X");
    release.resolve(true);
    expect(await protectedAgain).toBe(true);
    expect(s.hasOwn(c.uid)).toBe(true);
    expect((await store(db)).entries.map((e) => e.code)).toEqual(["X"]);
  });

  it("restores only text, refuses changed versions, and does not overwrite typing during recovery", () => {
    const actions = { edit: vi.fn(), run: vi.fn(), cancel: vi.fn(), notice: vi.fn() };
    const draft = new Draft(source(), actions);
    draft.change("pending");
    draft.requestRun();
    draft.restore("recovered", "v1");
    const request = actions.edit.mock.calls.at(-1)![0].request;
    draft.observe({ ...source("recovered", "v2"), edit_id: request });
    expect(actions.run).not.toHaveBeenCalled();
    draft.restore("another", "v1");
    expect(draft.conflict).toBe(true);
    expect(actions.edit).toHaveBeenCalledTimes(2);
    expect(draft.restore("older offer", "v2", "recovered")).toBe(false);
    expect(draft.code).toBe("another");
  });

  it("ignores storage completions after editor disposal", async () => {
    const done = deferred();
    const actions = { edit: vi.fn(), run: vi.fn(), cancel: vi.fn(), notice: vi.fn(), persist: () => done.promise };
    const draft = new Draft(source(), actions);
    draft.change("new");
    draft.requestRun();
    draft.dispose();
    done.resolve(true);
    await done.promise;
    expect(actions.edit).not.toHaveBeenCalled();
    expect(actions.run).not.toHaveBeenCalled();
  });
});
