// D-019: what a cell shows for its state. Pure (tested). Never color alone: every chip
// has an icon and a text; ok and idle are silent (no chip, no rail).
import type { CellJson, Status } from "./protocol";
import { parentsOf } from "./store";

export type Tone =
  | "muted" | "queued" | "compiling" | "running" | "modified" | "error" | "blocked" | "crashed"
  | "interrupted";

export type Icon =
  | "list-ordered" | "hourglass" | "hammer" | "loader" | "pencil" | "circle-x" | "braces"
  | "copy" | "refresh" | "ban" | "zap" | "square" | "git-branch" | "cog" | "alert";

export type Rail = "none" | "solid" | "dashed" | "stripes" | "thick";

/** A piece of text; `[n]` references render as links to cell n. */
export interface Chip {
  tone: Tone;
  icon: Icon;
  text: string;
  outlined?: boolean;  // secondary chips (flags) are outlined, main-state chips filled
  spin?: boolean;
  title?: string;
}

export interface Look {
  rail: Rail;
  tone: Tone | null;
  chips: Chip[];
  dimOutput: boolean;       // stale: the value shown is about to be replaced
  outdatedOutput: boolean;  // upstream_modified: dashed amber top border on the output
}

const ACTIVE: Status[] = ["queued", "stale", "running", "compiling"];

export const ordinal = (n: number) => `${n}º`;
const refs = (ids: number[]) => ids.map((i) => `[${i}]`).join(", ");

export function look(cell: CellJson, all: Record<number, CellJson>, edges: [number, number][],
                     elapsed?: number): Look {
  const chips: Chip[] = [];
  const parents = parentsOf(edges, cell.id);
  const seconds = elapsed !== undefined ? ` ${(elapsed / 1000).toFixed(1).replace(".", ",")} s` : "";
  let rail: Rail = "solid";
  let tone: Tone | null = null;

  switch (cell.status) {
    case "ok":
    case "idle":
      rail = "none";  // silent by default (review item 4)
      break;
    case "queued":
      tone = "queued";
      chips.push({ tone, icon: "list-ordered", text: `na fila · ${ordinal(cell.queue_position ?? 1)}` });
      break;
    case "stale": {
      tone = "queued";
      rail = "stripes";
      const waiting = parents.filter((p) => all[p] && ACTIVE.includes(all[p].status));
      chips.push({
        tone, icon: "hourglass",
        text: `vai reexecutar · aguardando ${refs(waiting.length ? waiting : parents)}`,
        title: "Um ancestral vai rodar; o valor mostrado é o anterior e será substituído.",
      });
      break;
    }
    case "compiling": {
      tone = "compiling";
      const hasValue = Object.keys(cell.previews).length > 0 || cell.output !== "";
      chips.push({
        tone, icon: "hammer",
        text: hasValue ? `compilando nova versão · valor anterior${seconds}` : `compilando…${seconds}`,
        title: hasValue ? "O valor mostrado é da versão anterior até o build terminar e a célula rodar." : undefined,
      });
      break;
    }
    case "running":
      tone = "running";
      chips.push({ tone, icon: "loader", spin: true, text: `executando${seconds}` });
      break;
    case "modified":
      tone = "modified";
      rail = "dashed";
      chips.push({ tone, icon: "pencil", text: "modificada — Shift+Enter executa",
                   title: "O código difere do último executado." });
      break;
    case "error":
      tone = "error";
      chips.push({ tone, icon: "circle-x", text: "erro" });
      break;
    case "syntax-error":
      tone = "error";
      chips.push({ tone, icon: "braces", text: "erro de sintaxe" });
      break;
    case "compile-error":
      tone = "error";
      chips.push({ tone, icon: "hammer", text: "erro de compilação" });
      break;
    case "multiple-definition": {
      tone = "error";
      const clash = cell.defs.filter((d) => Object.values(all).some((o) => o.id !== cell.id && o.defs.includes(d)));
      const others = Object.values(all).filter((o) => o.id !== cell.id && o.defs.some((d) => clash.includes(d)));
      chips.push({ tone, icon: "copy", text: `${clash.map((n) => `\`${n}\``).join(", ")} também definido em ${refs(others.map((o) => o.id))}` });
      break;
    }
    case "cycle":
      tone = "error";
      chips.push({ tone, icon: "refresh", text: `ciclo com ${refs(parents)}` });
      break;
    case "blocked": {
      tone = "blocked";
      const bad = parents.filter((p) => all[p] && !["ok", "modified"].includes(all[p].status));
      chips.push({ tone, icon: "ban", text: `bloqueada por ${refs(bad.length ? bad : parents)}` });
      break;
    }
    case "crashed":
      tone = "crashed";
      rail = "thick";
      chips.push({ tone, icon: "zap", text: "derrubou o kernel · quarentena" });
      break;
    case "interrupted":
      tone = "interrupted";
      chips.push({ tone, icon: "square", text: "interrompida por você" });
      break;
  }

  // Flags: secondary, outlined chips on top of the main state.
  if (cell.upstream_modified.length > 0) {
    chips.push({ tone: "modified", icon: "git-branch", outlined: true,
                 text: `código de ${refs(cell.upstream_modified)} mudou sem executar`,
                 title: "O valor mostrado está certo para o código que rodou; nada vai reexecutar sozinho." });
  }
  if (cell.compiling && cell.status !== "compiling") {
    chips.push({ tone: "muted", icon: "cog", spin: true, outlined: true, text: "build em 2º plano" });
  }
  if (cell.diagnostics.length > 0) {
    const n = cell.diagnostics.length;
    chips.push({ tone: "error", icon: "alert", outlined: true, text: `${n} ${n === 1 ? "problema" : "problemas"}` });
  }

  return {
    rail, tone, chips,
    dimOutput: cell.status === "stale",
    outdatedOutput: cell.upstream_modified.length > 0,
  };
}

/** Split a text on `[n]` references so they can be rendered as links. */
export function splitRefs(text: string): (string | number)[] {
  const out: (string | number)[] = [];
  let last = 0;
  for (const m of text.matchAll(/\[(\d+)\]/g)) {
    if (m.index! > last) out.push(text.slice(last, m.index));
    out.push(Number(m[1]));
    last = m.index! + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
