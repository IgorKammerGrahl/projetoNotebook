import { useEffect, useRef, useState } from "react";
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
  onBusy: (id: number, busy: boolean) => void;
  rejected?: string;
  onDelete: () => void;
  onAdd: (kind: Kind) => void;
}

const KIND_LABEL: Record<Kind, string> = { python: "python", mojo: "mojo", markdown: "markdown", html: "html" };
const HAS_ERROR_PANEL = ["error", "syntax-error", "compile-error", "multiple-definition", "cycle", "blocked",
  "crashed", "interrupted"];

export function Cell({ cell, all, edges, elapsed, onEdit, onRun, onDelete, onAdd, onCancelEdits, onNotice, onBusy, rejected }: Props) {
  const l = look(cell, all, edges, elapsed);
  const graph = cell.kind === "python" || cell.kind === "mojo";
  const [editing, setEditing] = useState(!graph && cell.code === "");
  const callbacks = useRef({ onEdit, onRun, onCancelEdits, onNotice });
  callbacks.current = { onEdit, onRun, onCancelEdits, onNotice };
  const [draft] = useState(() => new Draft(cell, {
    edit: (m) => callbacks.current.onEdit(m), run: (at) => callbacks.current.onRun(at),
    cancel: () => callbacks.current.onCancelEdits(), notice: (text) => callbacks.current.onNotice(text),
  }));
  const [, refresh] = useState(0);
  const update = (fn: () => void) => { fn(); refresh((n) => n + 1); };
  useEffect(() => {
    draft.observe(cell, rejected);
    refresh((n) => n + 1);
  }, [cell.version, cell.edit_id, rejected, draft]);
  const busy = draft.busy;
  useEffect(() => { onBusy(cell.id, busy); }, [cell.id, busy, onBusy]);
  useEffect(() => () => onBusy(cell.id, false), [cell.id, onBusy]);
  const run = () => update(() => draft.requestRun());

  return (
    <section id={`cell-${cell.id}`} className={`cell rail-${l.rail} tone-${l.tone ?? "none"}`}
             aria-label={`célula ${cell.id}, ${cell.kind}`}>
      <header className="cell-head">
        <span className="cell-id">[{cell.id}] {KIND_LABEL[cell.kind]}</span>
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
  if (!previews.length && !cell.output && !(showError && cell.error)) return null;
  return (
    <div className={`output${dim ? " dim" : ""}${outdated ? " outdated" : ""}`}>
      {cell.output && <pre className="stdout">{cell.output}</pre>}
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

function PreviewRow({ name, p }: { name: string; p: Preview }) {
  return (
    <>
      <dt>{name}</dt>
      <dd>{"head" in p
        ? <><span className="type">ndarray {p.dtype} {`[${p.shape.join(", ")}]`}</span> [{p.head.join(", ")}{p.head.length < p.shape.reduce((a, b) => a * b, 1) ? ", …" : ""}]</>
        : <><span className="type">{p.type}</span> {p.repr}</>}</dd>
    </>
  );
}
