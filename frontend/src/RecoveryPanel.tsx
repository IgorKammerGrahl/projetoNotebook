import type { CellJson } from "./protocol";
import type { RecoveryEntry } from "./recovery";

interface Props {
  entries: RecoveryEntry[];
  cells: CellJson[];
  connected: boolean;
  blocked: (cell: CellJson) => boolean;
  creating: Set<string>;
  onRecover: (entry: RecoveryEntry, cell: CellJson) => void;
  onNew: (entry: RecoveryEntry) => void;
  onDiscard: (entry: RecoveryEntry) => void;
}

export function RecoveryPanel({ entries, cells, connected, blocked, creating, onRecover, onNew, onDiscard }: Props) {
  if (!entries.length) return null;
  return <section className="recovery-panel" aria-label="rascunhos recuperáveis">
    <h2>Rascunhos disponíveis neste navegador</h2>
    <p>Revise antes de recuperar. Recuperar salva o texto sem executá-lo. Uma cópia pode pertencer a outra aba aberta.</p>
    {entries.map((entry) => {
      const cell = cells.find((c) => c.uid === entry.cell && c.kind === entry.kind);
      const conflict = cell && cell.code !== entry.base && cell.code !== entry.code;
      return <article key={entry.id} className="recovery-entry" aria-label={`rascunho ${entry.kind}`}>
        <h3>{cell ? `Célula ${cell.id}` : "Célula não encontrada"} · {entry.kind}</h3>
        {conflict && <p role="status">Conflito: o código da célula mudou desde este rascunho. Escolha qual versão manter.</p>}
        {!cell && <p>A célula foi removida ou sua identidade mudou. Você pode recuperar o texto em uma nova célula.</p>}
        <details open={!!conflict}>
          <summary>Comparar conteúdo</summary>
          <label>Rascunho<textarea readOnly value={entry.code} aria-label="código do rascunho" /></label>
          {cell && <label>Versão atual no servidor<textarea readOnly value={cell.code} aria-label="código atual no servidor" /></label>}
        </details>
        {cell ? <button disabled={!connected || blocked(cell)}
          title={blocked(cell) ? "Salve ou resolva as alterações atuais desta célula antes de recuperar outra versão" : undefined}
          onClick={() => onRecover(entry, cell)}>{conflict ? "usar rascunho" : "recuperar rascunho"}</button>
          : <button disabled={!connected || creating.has(entry.id)} onClick={() => onNew(entry)}>recuperar em nova célula</button>}
        {" "}<button onClick={() => onDiscard(entry)} disabled={creating.has(entry.id)}>
          {conflict ? "manter versão do servidor" : "descartar rascunho"}
        </button>
      </article>;
    })}
  </section>;
}
