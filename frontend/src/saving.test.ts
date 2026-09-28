import { describe, expect, it } from "vitest";
import type { SaveState } from "./protocol";
import { savingStatus } from "./saving";
import { empty, reduce } from "./store";

const saved: SaveState = { status: "saved", revision: 3, saved_revision: 3, error: null };
const writing: SaveState = { status: "saving", revision: 4, saved_revision: 3, error: null };
const failed: SaveState = { ...writing, status: "error", error: "Sem espaço em disco." };

describe("saving status", () => {
  it("requires a disk confirmation for the current revision", () => {
    expect(savingStatus(saved, true, false).text).toBe("salvo");
    expect(savingStatus(writing, true, false).text).toBe("salvando…");
    expect(savingStatus({ ...writing, status: "saved" }, true, false).text).toBe("salvando…");
  });

  it("does not present a saved server copy as saved local drafts or unconfirmed mutations", () => {
    expect(savingStatus(saved, true, true).text).toBe("alterações pendentes");
    expect(savingStatus(saved, false, true).text).toBe("salvamento não confirmado");
    expect(savingStatus(saved, false, false).text).toBe("salvamento não confirmado");
    expect(savingStatus(null, true, false).text).toBe("aguardando confirmação");
  });

  it("keeps a disk failure visible even with newer local changes, and offers a connected retry", () => {
    const status = savingStatus(failed, true, true);
    expect(status.text).toBe("falha ao salvar");
    expect(status.detail).toContain("Sem espaço em disco.");
    expect(status.retry).toBe(true);
    expect(savingStatus(failed, false, false).retry).toBe(false);
  });

  it("ACKs do not clear errors or advance the saved revision", () => {
    let state = reduce(empty, { type: "save_status", save: failed });
    state = reduce(state, { type: "ack", seq: 42 });
    expect(state.save).toEqual(failed);
    state = reduce(state, { type: "save_status", save: { ...writing, saved_revision: 4, status: "saved" } });
    expect(savingStatus(state.save, true, false).text).toBe("salvo");
  });

  it("snapshots replace persistence state on reconnect, including a new server's revision zero", () => {
    let state = reduce(empty, { type: "save_status", save: failed });
    state = reduce(state, { type: "snapshot", cells: [], edges: [], kernel: empty.kernel,
      save: { ...saved, revision: 0, saved_revision: 0 } });
    expect(state.save?.status).toBe("saved");
    expect(state.save?.revision).toBe(0);
    state = reduce(state, { type: "snapshot", cells: [], edges: [], kernel: empty.kernel });
    expect(state.save).toBeNull(); // an older server cannot confirm a disk write
  });
});
