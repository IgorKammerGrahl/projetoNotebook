import type { RecoveryEntry } from "./recovery";

export interface RecoveryDatabase {
  read(document: string): Promise<unknown[]>;
  replace(entry: RecoveryEntry, previous?: RecoveryEntry): Promise<void>;
  remove(entries: RecoveryEntry[]): Promise<void>;
  subscribe?(refresh: () => void): () => void;
}

// Successful mutations resolve at transaction.complete, with strict durability.
// Replacement and removal are atomic; quota failure preserves the previous copy.
export class IndexedRecoveryDatabase implements RecoveryDatabase {
  private opened?: Promise<IDBDatabase>;
  private channel?: BroadcastChannel;

  private open() {
    return this.opened ??= new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open("notebook-recovery", 1);
      let blocked = false;
      request.onupgradeneeded = () => {
        request.result.createObjectStore("drafts", { keyPath: "id" }).createIndex("document", "document");
      };
      request.onblocked = () => { blocked = true; reject(new Error("Recovery database is blocked")); };
      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        if (blocked) { request.result.close(); return; }
        request.result.onversionchange = () => request.result.close();
        resolve(request.result);
      };
    });
  }

  async read(document: string): Promise<unknown[]> {
    const db = await this.open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("drafts", "readonly");
      const request = tx.objectStore("drafts").index("document").getAll(document);
      tx.oncomplete = () => resolve(request.result);
      tx.onabort = () => reject(tx.error ?? new Error("Recovery read aborted"));
    });
  }

  private async mutate(entry: RecoveryEntry | undefined, removed: RecoveryEntry[]) {
    const db = await this.open();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction("drafts", "readwrite", { durability: "strict" });
      const store = tx.objectStore("drafts");
      if (entry) store.add(entry); // immutable IDs: a collision must abort, never overwrite
      for (const old of removed) {
        const request = store.get(old.id);
        request.onsuccess = () => {
          try {
            if (JSON.stringify(request.result) === JSON.stringify(old)) store.delete(old.id);
          } catch { /* Unknown/cyclic data must be preserved, never treated as our copy. */ }
        };
      }
      tx.oncomplete = () => resolve();
      tx.onabort = () => reject(tx.error ?? new Error("Recovery write aborted"));
    });
    this.channel?.postMessage(null);
  }

  replace(entry: RecoveryEntry, previous?: RecoveryEntry) { return this.mutate(entry, previous ? [previous] : []); }
  remove(entries: RecoveryEntry[]) { return this.mutate(undefined, entries); }

  subscribe(refresh: () => void) {
    const channel = new BroadcastChannel("notebook-recovery");
    channel.onmessage = refresh;
    this.channel = channel;
    return () => { channel.close(); if (this.channel === channel) this.channel = undefined; };
  }
}
