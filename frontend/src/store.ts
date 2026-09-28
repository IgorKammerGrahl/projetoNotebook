// Notebook state as the server reports it. Pure reducer (tested).
import type { CellJson, DocumentIdentity, KernelState, SaveState, ServerMsg } from "./protocol";

export interface NotebookState {
  cells: Record<number, CellJson>;
  order: number[];
  edges: [number, number][];
  kernel: KernelState;
  connected: boolean;
  save: SaveState | null;
  document?: DocumentIdentity;
  conflicts: Record<number, string | undefined>;
}

export const empty: NotebookState = {
  cells: {}, order: [], edges: [], kernel: { restarts: 0, dead: false }, connected: false, save: null, conflicts: {},
};

export function reduce(state: NotebookState, msg: ServerMsg): NotebookState {
  switch (msg.type) {
    case "snapshot":
      return {
        cells: Object.fromEntries(msg.cells.map((c) => [c.id, c])),
        order: msg.cells.map((c) => c.id),
        edges: msg.edges,
        kernel: msg.kernel,
        connected: true,
        save: msg.save ?? null,
        document: msg.document,
        conflicts: msg.document?.session === state.document?.session ? state.conflicts : {},
      };
    case "update": {
      const cells = { ...state.cells };
      for (const c of msg.cells) cells[c.id] = c;
      for (const id of Object.keys(cells).map(Number)) if (!msg.order.includes(id)) delete cells[id];
      return { ...state, cells, order: msg.order, edges: msg.edges, kernel: msg.kernel, save: msg.save ?? null };
    }
    case "save_status":
      return { ...state, save: msg.save };
    case "conflict":
      return { ...state, cells: msg.cell ? { ...state.cells, [msg.cid]: msg.cell } : state.cells,
        conflicts: { ...state.conflicts, [msg.cid]: msg.request } };
    default:
      return state;
  }
}

export function parentsOf(edges: [number, number][], id: number): number[] {
  return edges.filter(([, c]) => c === id).map(([p]) => p);
}
