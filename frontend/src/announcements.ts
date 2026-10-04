import type { ServerMsg, Status } from "./protocol";

const RESULT: Partial<Record<Status, string>> = {
  ok: "ok", error: "erro", "syntax-error": "erro de sintaxe", "compile-error": "erro de compilação", blocked: "bloqueada",
};

export class Announcements {
  readonly userRuns = new Set<number>();
  private eventId?: string;

  receive(m: ServerMsg): string | null {
    if (m.type !== "update" && m.type !== "snapshot") return null;
    const event = m.kernel.event;
    if (event && event.id !== this.eventId) {
      this.eventId = event.id;
      this.userRuns.clear(); // recovery must not overwrite this summary with per-cell results
      const subject = event.cid === null ? "O kernel" : `A célula ${event.cid}`;
      if (m.kernel.dead) return `${subject} sofreu um crash. O kernel não pôde ser recuperado automaticamente.`;
      if (event.kind === "restarted") return "O kernel foi reiniciado. As células que já tinham rodado serão reexecutadas.";
      return event.kind === "interrupted"
        ? `Execução interrompida${event.cid === null ? "" : ` na célula ${event.cid}`}. O kernel reinicia e as demais células elegíveis serão reexecutadas.`
        : `${subject} sofreu um crash. O kernel reinicia e as demais células elegíveis serão reexecutadas.`;
    }
    if (m.type === "snapshot") return null;
    const results = m.cells.filter((c) => this.userRuns.has(c.id) && RESULT[c.status]);
    for (const c of results) this.userRuns.delete(c.id);
    return results.length ? results.map((c) => `Célula ${c.id}: ${RESULT[c.status]}`).join(". ") : null;
  }
}
