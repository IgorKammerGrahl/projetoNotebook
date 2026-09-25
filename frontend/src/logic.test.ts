import { describe, expect, it } from "vitest";
import { Text } from "@codemirror/state";
import type { CellJson } from "./protocol";
import { empty, reduce } from "./store";
import { look, splitRefs } from "./status";
import { place, underlined } from "./diagnostics";

const cell = (id: number, over: Partial<CellJson> = {}): CellJson => ({
  id, kind: "python", code: "", status: "ok", error: "", output: "", previews: {}, defs: [], refs: [],
  upstream_modified: [], compiling: false, diagnostics: [], queue_position: null, ...over,
});

describe("status look (D-019)", () => {
  it("is silent for ok and idle: no chip, no rail", () => {
    for (const status of ["ok", "idle"] as const) {
      const l = look(cell(1, { status }), {}, []);
      expect(l.chips).toEqual([]);
      expect(l.rail).toBe("none");
    }
  });

  it("gives every attention/activity state an icon and a text", () => {
    const states = ["queued", "stale", "compiling", "running", "modified", "error", "syntax-error",
      "compile-error", "multiple-definition", "cycle", "blocked", "crashed", "interrupted"] as const;
    for (const status of states) {
      const [chip] = look(cell(1, { status }), {}, []).chips;
      expect(chip.icon, status).toBeTruthy();
      expect(chip.text.length, status).toBeGreaterThan(2);
    }
  });

  it("uses distinct rails for dashed modified, striped stale and thick crashed", () => {
    expect(look(cell(1, { status: "modified" }), {}, []).rail).toBe("dashed");
    expect(look(cell(1, { status: "stale" }), {}, []).rail).toBe("stripes");
    expect(look(cell(1, { status: "crashed" }), {}, []).rail).toBe("thick");
  });

  it("keeps the three kinds of 'outdated' textually distinct (glossary)", () => {
    const all = { 2: cell(2, { status: "running" }) };
    const stale = look(cell(3, { status: "stale" }), all, [[2, 3]]).chips[0].text;
    const upstream = look(cell(3, { upstream_modified: [2] }), all, [[2, 3]]).chips[0].text;
    const compilingOld = look(cell(3, { status: "compiling", output: "old" }), all, []).chips[0].text;
    expect(stale).toBe("vai reexecutar · aguardando [2]");
    expect(upstream).toBe("código de [2] mudou sem executar");
    expect(compilingOld).toBe("compilando nova versão · valor anterior");
    expect(new Set([stale, upstream, compilingOld]).size).toBe(3);
  });

  it("an ok cell with an upstream_modified flag shows only the outlined flag chip", () => {
    const l = look(cell(3, { upstream_modified: [2] }), {}, []);
    expect(l.rail).toBe("none");
    expect(l.chips).toHaveLength(1);
    expect(l.chips[0].outlined).toBe(true);
    expect(l.outdatedOutput).toBe(true);
  });

  it("shows a background build only when the status is not already compiling", () => {
    expect(look(cell(1, { status: "modified", compiling: true }), {}, []).chips.map((c) => c.text))
      .toContain("build em 2º plano");
    expect(look(cell(1, { status: "compiling", compiling: true }), {}, []).chips.map((c) => c.text))
      .not.toContain("build em 2º plano");
  });

  it("queued shows its place in line; blocked names the bad parent", () => {
    expect(look(cell(1, { status: "queued", queue_position: 2 }), {}, []).chips[0].text).toBe("na fila · 2º");
    const all = { 1: cell(1, { status: "error" }), 2: cell(2, { status: "ok" }) };
    expect(look(cell(3, { status: "blocked" }), all, [[1, 3], [2, 3]]).chips[0].text).toBe("bloqueada por [1]");
  });

  it("counts diagnostics", () => {
    const diag = { line: 1, col: 1, message: "x", source: "compile" as const };
    expect(look(cell(1, { diagnostics: [diag, diag] }), {}, []).chips[0].text).toBe("2 problemas");
  });

  it("splits [n] references for links", () => {
    expect(splitRefs("aguardando [2], [10]!")).toEqual(["aguardando ", 2, ", ", 10, "!"]);
  });
});

describe("store", () => {
  it("applies snapshot then updates, and drops cells missing from `order`", () => {
    let s = reduce(empty, { type: "snapshot", cells: [cell(1), cell(2)], edges: [[1, 2]], kernel: { restarts: 0, dead: false } });
    expect(s.order).toEqual([1, 2]);
    s = reduce(s, { type: "update", cells: [cell(2, { status: "running" })], order: [2], edges: [], kernel: { restarts: 1, dead: false } });
    expect(Object.keys(s.cells)).toEqual(["2"]);
    expect(s.cells[2].status).toBe("running");
    expect(s.kernel.restarts).toBe(1);
  });
});

describe("diagnostics", () => {
  const doc = Text.of(["def run(mut t: Int) raises:", "    t = nope + 1"]);

  it("places line/col on the word at col", () => {
    const p = place(doc, { line: 2, col: 9, message: "m", source: "compile" });
    expect(doc.sliceString(p.from, p.to)).toBe("nope");
  });

  it("clamps out-of-range lines and columns", () => {
    const p = place(doc, { line: 99, col: 999, message: "m", source: "compile" });
    expect(doc.lineAt(p.from).number).toBe(2);
  });

  it("hides underlines on the line being typed, shows the rest", () => {
    const a = place(doc, { line: 1, col: 13, message: "a", source: "compile" });
    const b = place(doc, { line: 2, col: 9, message: "b", source: "compile" });
    const lineOf = (pos: number) => doc.lineAt(pos).number;
    expect(underlined([a, b], lineOf, 2)).toEqual([a]);
    expect(underlined([a, b], lineOf, null)).toEqual([a, b]);
  });
});
