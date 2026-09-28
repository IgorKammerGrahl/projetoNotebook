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
  uid?: string;
  kind: Kind;
  code: string;
  version: string;
  edit_id: string | null;
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
  event?: { id: string; kind: "crashed" | "interrupted"; cid: number | null } | null;
}

export interface SaveState {
  status: "saving" | "saved" | "error" | "conflict";
  revision: number;
  saved_revision: number;
  error: string | null;
  conflict?: { message: string; preserved: string | null };
  copy?: { revision: number; path: string } | null;
  reload?: string | null;
}

export interface DocumentIdentity { id: string; name: string; session: string }

export type ServerMsg =
  | { type: "snapshot"; cells: CellJson[]; edges: [number, number][]; kernel: KernelState; save?: SaveState; document?: DocumentIdentity }
  | { type: "update"; cells: CellJson[]; order: number[]; edges: [number, number][]; kernel: KernelState; save?: SaveState }
  | { type: "save_status"; save: SaveState }
  | { type: "added"; cid: number; request?: string }
  | { type: "ack"; seq: number }
  | { type: "notice"; text: string }
  | { type: "conflict"; cid: number; cell: CellJson | null; request?: string; seq?: number }
  | { type: "error"; error: string; seq?: number };

export type ClientMsg =
  | { type: "edit"; cid: number; code: string; base_version: string; request: string }
  | { type: "run"; cid: number }
  | { type: "run_all" }
  | { type: "add"; code: string; kind: Kind; after: number | null; request?: string }
  | { type: "delete"; cid: number }
  | { type: "stop" }
  | { type: "retry_save" }
  | { type: "preserve_copy" | "reload_external"; revision: number; session: string }
  | { type: "reload_ready"; request: string; ready: boolean };
