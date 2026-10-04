import { useLayoutEffect, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import type { CellJson, ClientMsg, Kind, Preview } from "./protocol";
import { Draft } from "./draft";
import { look } from "./status";
import { Editor } from "./Editor";
import { Play, StateIcon, Trash } from "./icons";
import { Refs } from "./Refs";

interface Props {
  cell: CellJson;
  all: Record<number, CellJson>;
  edges: [number, number][];
  elapsed?: number;
  onEdit: (message: Extract<ClientMsg, { type: "edit" }>) => void;
  onRun: (queuedAt: number) => void;
  onCancelEdits: () => void;
  onNotice: (text: string) => void;
  onBusy: (id: number, busy: boolean, check?: () => boolean) => void;
  rejected?: string;
  restore?: { id: string; entryId: string; code: string; version: string; expectedCode: string };
  onRestoreRejected: (entryId: string, code: string) => void;
  onDraft: (code: string, base: string) => Promise<boolean>;
  onAccepted: (code: string, version: string) => void;
  onDiscardDraft: () => void;
  onDelete: () => void;
  onAdd: (kind: Kind) => void;
}

const KIND_LABEL: Record<Kind, string> = { python: "python", mojo: "mojo", markdown: "markdown", html: "html" };
const HAS_ERROR_PANEL = ["error", "syntax-error", "compile-error", "multiple-definition", "cycle", "blocked",
  "crashed", "interrupted"];

export function Cell({ cell, all, edges, elapsed, onEdit, onRun, onDelete, onAdd, onCancelEdits, onNotice, onBusy, rejected,
                       restore, onRestoreRejected, onDraft, onAccepted, onDiscardDraft }: Props) {
  const l = look(cell, all, edges, elapsed);
  const graph = cell.kind === "python" || cell.kind === "mojo";
  const [editing, setEditing] = useState(!graph && cell.code === "");
  const callbacks = useRef({ onEdit, onRun, onCancelEdits, onNotice, onDraft, onAccepted, onDiscardDraft });
  callbacks.current = { onEdit, onRun, onCancelEdits, onNotice, onDraft, onAccepted, onDiscardDraft };
  const [draft] = useState(() => new Draft(cell, {
    edit: (m) => callbacks.current.onEdit(m), run: (at) => callbacks.current.onRun(at),
    cancel: () => callbacks.current.onCancelEdits(), notice: (text) => callbacks.current.onNotice(text),
    persist: (code, base) => callbacks.current.onDraft(code, base),
    accepted: (code, version) => callbacks.current.onAccepted(code, version),
    discard: () => callbacks.current.onDiscardDraft(),
  }));
  const [, refresh] = useState(0);
  const update = (fn: () => void) => { fn(); refresh((n) => n + 1); };
  useLayoutEffect(() => { draft.activate(); return () => draft.dispose(); }, [draft]);
  useLayoutEffect(() => {
    draft.observe(cell, rejected);
    refresh((n) => n + 1);
  }, [cell.version, cell.edit_id, rejected, draft]);
  const restored = useRef("");
  useLayoutEffect(() => {
    if (!restore || restored.current === restore.id) return;
    restored.current = restore.id;
    if (!draft.restore(restore.code, restore.version, restore.expectedCode)) {
      onRestoreRejected(restore.entryId, restore.code);
      return;
    }
    setEditing(true);
    refresh((n) => n + 1);
  }, [restore, draft]);
  const busy = draft.busy;
  useLayoutEffect(() => { onBusy(cell.id, busy, () => draft.busy); }, [cell.id, busy, onBusy, draft]);
  useLayoutEffect(() => () => onBusy(cell.id, false), [cell.id, onBusy]);
  const run = () => update(() => draft.requestRun());

  return (
    <section id={`cell-${cell.id}`} className={`cell rail-${l.rail} tone-${l.tone ?? "none"}`}
             aria-label={`célula ${cell.id}, ${cell.kind}`}>
      <header className="cell-head">
        <span className="cell-id">[{cell.id}] {KIND_LABEL[cell.kind]}</span>
        {cell.duration_ms != null && (cell.status === "ok" || cell.status === "error") && (
          <span className="duration" title="tempo da última execução (sem a compilação)">{took(cell.duration_ms)}</span>
        )}
        {l.chips.map((c, i) => (
          <span key={i} className={`chip tone-${c.tone}${c.outlined ? " outlined" : ""}`} title={c.title}>
            <StateIcon name={c.icon} spin={c.spin} /> <Refs text={c.text} />
          </span>
        ))}
        <span className="spacer" />
        {graph && (
          <button className="icon-btn" onClick={run} disabled={draft.conflict} title="Executar (Shift+Enter)" aria-label="executar célula">
            <Play size={15} aria-hidden="true" />
          </button>
        )}
        <button className="icon-btn" onClick={onDelete} title="Apagar célula" aria-label="apagar célula">
          <Trash size={15} aria-hidden="true" />
        </button>
      </header>

      {draft.conflict && <div className="edit-conflict" role="status">
        <p>O código mudou no servidor. Sua versão foi mantida no editor.</p>
        <button onClick={() => update(() => draft.keepMine())}>manter a minha versão</button>{" "}
        <button onClick={() => update(() => draft.loadServer())}>carregar a do servidor</button>
      </div>}

      {graph || editing ? (
        <Editor code={draft.code} kind={cell.kind} diagnostics={cell.diagnostics} label={`código da célula ${cell.id}`}
                autoFocus={editing} onChange={(code) => update(() => draft.change(code))}
                onFocusChange={(focused) => { draft.focused = focused; }}
                onRun={graph ? run : () => setEditing(false)} />
      ) : (
        <Rendered cell={{ ...cell, code: draft.code }} onEdit={() => setEditing(true)} />
      )}

      {graph && <Output cell={cell} dim={l.dimOutput} outdated={l.outdatedOutput} showError={HAS_ERROR_PANEL.includes(cell.status)} />}

      <div className="add-row" role="group" aria-label="adicionar célula abaixo">
        {(["python", "mojo", "markdown", "html"] as Kind[]).map((k) => (
          <button key={k} onClick={() => onAdd(k)}>+ {KIND_LABEL[k]}</button>
        ))}
      </div>
    </section>
  );
}

function Rendered({ cell, onEdit }: { cell: CellJson; onEdit: () => void }) {
  if (cell.kind === "html") {
    // D-015: sandboxed, no allow-scripts, no allow-same-origin
    return (
      <div className="rendered" onDoubleClick={onEdit} title="Duplo clique para editar">
        <iframe sandbox="" srcDoc={cell.code} title={`html da célula ${cell.id}`} className="html-frame" />
      </div>
    );
  }
  const html = DOMPurify.sanitize(marked.parse(cell.code, { async: false }) as string);
  return <div className="rendered markdown" onDoubleClick={onEdit} title="Duplo clique para editar"
              dangerouslySetInnerHTML={{ __html: html }} />;
}

function Output({ cell, dim, outdated, showError }: { cell: CellJson; dim: boolean; outdated: boolean; showError: boolean }) {
  const previews = Object.entries(cell.previews);
  if (!previews.length && !cell.output && !cell.images.length && !(showError && cell.error)) return null;
  return (
    <div className={`output${dim ? " dim" : ""}${outdated ? " outdated" : ""}`}>
      {cell.output && <pre className="stdout">{cell.output}</pre>}
      {cell.images.map((png, i) => (
        <img key={i} className="figure" src={`data:image/png;base64,${png}`} alt={`figura ${i + 1} da célula ${cell.id}`} />
      ))}
      {previews.length > 0 && (
        <dl className="previews">
          {previews.map(([name, p]) => <PreviewRow key={name} name={name} p={p} />)}
        </dl>
      )}
      {showError && cell.error && (
        <pre className={`error-panel tone-${cell.status === "crashed" ? "crashed" : cell.status === "interrupted" ? "interrupted" : "error"}`}>
          <Refs text={cell.error} />
        </pre>
      )}
    </div>
  );
}

const took = (ms: number) => ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1).replace(".", ",")} s`;
const num = (v: number | string) => typeof v === "number" && !Number.isInteger(v) ? String(Number(v.toPrecision(4))) : String(v);

function PreviewRow({ name, p }: { name: string; p: Preview }) {
  if (!("head" in p)) return <><dt>{name}</dt><dd><span className="type">{p.type}</span> {p.repr}</dd></>;
  const [rows, cols] = p.shape;
  return (
    <>
      <dt>{name}</dt>
      <dd>
        <span className="type">ndarray {p.dtype} {`[${p.shape.join(", ")}]`}</span>
        {p.min !== undefined && <span className="stats">mín {num(p.min)} · máx {num(p.max!)} · média {num(p.mean!)}</span>}
        {p.rows ? (
          <table className="corner" aria-label={`canto superior esquerdo de ${name}`}>
            <tbody>
              {p.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j}>{num(v)}</td>)}{cols > r.length && <td>…</td>}</tr>)}
              {rows > p.rows.length && <tr><td>⋮</td></tr>}
            </tbody>
          </table>
        ) : <> [{p.head.map(num).join(", ")}{p.head.length < p.shape.reduce((a, b) => a * b, 1) ? ", …" : ""}]</>}
      </dd>
    </>
  );
}
