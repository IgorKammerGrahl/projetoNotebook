import type { CellJson, ClientMsg } from "./protocol";
import { RUN_TTL_MS } from "./connection";

type Source = Pick<CellJson, "id" | "code" | "version" | "edit_id">;
type Edit = Extract<ClientMsg, { type: "edit" }>;
interface Actions {
  edit: (message: Edit) => void;
  run: (queuedAt: number) => void;
  cancel: () => void;
  notice: (text: string) => void;
}

// One outstanding edit per cell. Further keystrokes retain their original base
// until our own accepted edit supplies its successor; foreign changes never rebase.
export class Draft {
  code: string;
  focused = false;
  conflict = false;
  pending: Edit | null = null;
  private runAt: number | null = null;
  private rejected?: string;

  constructor(public server: Source, private actions: Actions,
              private now = Date.now, private requestId: () => string = () => crypto.randomUUID()) {
    this.code = server.code;
  }

  get busy() { return !!this.pending || this.conflict || this.code !== this.server.code; }

  change(code: string) {
    this.code = code;
    this.flush();
  }

  observe(server: Source, rejected?: string) {
    const previous = this.server;
    this.server = server;
    if (rejected && rejected !== this.rejected) {
      this.rejected = rejected;
      if (this.pending?.request === rejected) {
        this.pending = null;
        this.conflict = true;
        this.runAt = null;
      }
    }
    if (this.pending && server.edit_id === this.pending.request) {
      this.pending = null;
      this.flush();
    } else if (server.version !== previous.version) {
      if (this.code !== server.code && (this.focused || this.pending || this.conflict || this.code !== previous.code)) {
        this.conflict = true;
        this.runAt = null;
      } else if (!this.conflict && !this.pending) this.code = server.code;
    }
  }

  keepMine() {
    this.actions.cancel();
    this.pending = null;
    this.conflict = false;
    this.flush(); // explicit choice uses the latest server version, still checked atomically
  }

  loadServer() {
    this.actions.cancel();
    this.pending = null;
    this.conflict = false;
    this.runAt = null;
    this.code = this.server.code;
  }

  requestRun() {
    if (this.conflict) {
      this.actions.notice("Resolva o conflito da célula antes de executar.");
      return;
    }
    this.runAt = this.now();
    this.flush();
  }

  private flush() {
    if (this.conflict || this.pending) return;
    if (this.code !== this.server.code) {
      this.pending = { type: "edit", cid: this.server.id, code: this.code,
        base_version: this.server.version, request: this.requestId() };
      this.actions.edit(this.pending);
    } else if (this.runAt !== null) {
      const at = this.runAt;
      this.runAt = null;
      if (this.now() - at > RUN_TTL_MS) this.actions.notice("Execução descartada: aguardou a edição por mais de 5 s.");
      else this.actions.run(at);
    }
  }
}
