// Wire types of the notebook WebSocket (kernel/web.py).

export type Kind = "python" | "mojo" | "markdown" | "html";

export type Status =
  | "idle" | "modified" | "queued" | "stale" | "compiling" | "running" | "ok"
  | "error" | "syntax-error" | "compile-error" | "multiple-definition" | "cycle"
  | "blocked" | "crashed" | "interrupted";

export interface Diagnostic {
  line: number;
  col: number;
  message: string;
  source: "compile" | "interface";
}

export type Preview =
  | { type: "ndarray"; shape: number[]; dtype: string; head: (number | string)[] }
  | { type: string; repr: string };

export interface CellJson {
  id: number;
  kind: Kind;
  code: string;
  status: Status;
  error: string;
  output: string;
  previews: Record<string, Preview>;
  defs: string[];
  refs: string[];
  upstream_modified: number[];
  compiling: boolean;
  diagnostics: Diagnostic[];
  queue_position: number | null;
}

export interface KernelState {
  restarts: number;
  dead: boolean;
}

export type ServerMsg =
  | { type: "snapshot"; cells: CellJson[]; edges: [number, number][]; kernel: KernelState }
  | { type: "update"; cells: CellJson[]; order: number[]; edges: [number, number][]; kernel: KernelState }
  | { type: "added"; cid: number; request?: string }
  | { type: "error"; error: string };

export type ClientMsg =
  | { type: "edit"; cid: number; code: string }
  | { type: "run"; cid: number }
  | { type: "run_all" }
  | { type: "add"; code: string; kind: Kind; after: number | null; request?: string }
  | { type: "delete"; cid: number }
  | { type: "stop" };
