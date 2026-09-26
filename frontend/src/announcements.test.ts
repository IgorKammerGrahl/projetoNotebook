import { expect, it } from "vitest";
import { Announcements } from "./announcements";
import type { CellJson, ServerMsg } from "./protocol";

function update(id: string, kind: "crashed" | "interrupted", cells: CellJson[] = []): Extract<ServerMsg, { type: "update" }> {
  return { type: "update", cells, order: [1, 2], edges: [], kernel: { dead: false, restarts: 1, event: { id, kind, cid: 1 } } };
}

it.each(["crashed", "interrupted"] as const)("announces %s once during run-all and recovery, including reconnect", (kind) => {
  const a = new Announcements(); // run-all has no individual userRuns
  expect(a.receive(update("event-1", kind))).toContain(kind === "crashed" ? "crash" : "interrompida");
  expect(a.receive(update("event-1", kind))).toBeNull();
  const recovery = update("event-1", kind);
  expect(a.receive({ ...recovery, type: "snapshot" })).toBeNull();
  expect(a.receive(update("event-2", kind))).not.toBeNull(); // same cell, another actual event
});

it("gives the incident priority over individual results and clears recovery announcements", () => {
  const a = new Announcements();
  a.userRuns.add(2);
  const ok = { id: 2, status: "ok" } as CellJson;
  const summary = a.receive(update("event-1", "crashed", [ok]));
  expect(summary).toContain("crash");
  expect(summary).not.toContain("Célula 2: ok");
  expect(a.receive(update("event-1", "crashed", [ok]))).toBeNull();
});

it("does not promise recovery when the kernel has exhausted its restart attempts", () => {
  const a = new Announcements();
  const m = update("event-terminal", "crashed");
  m.kernel.dead = true;
  expect(a.receive(m)).toContain("não pôde ser recuperado");
  expect(a.receive(m)).toBeNull();
});
