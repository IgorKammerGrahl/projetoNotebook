import { useState } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import type { CellJson, Kind, Preview } from "./protocol";
import { look } from "./status";
import { Editor } from "./Editor";
import { Play, StateIcon, Trash } from "./icons";
import { Refs } from "./Refs";

interface Props {
  cell: CellJson;
  all: Record<number, CellJson>;
  edges: [number, number][];
  elapsed?: number;
  onEdit: (code: string) => void;
  onRun: () => void;
  onDelete: () => void;
  onAdd: (kind: Kind) => void;
}

const KIND_LABEL: Record<Kind, string> = { python: "python", mojo: "mojo", markdown: "markdown", html: "html" };
const HAS_ERROR_PANEL = ["error", "syntax-error", "compile-error", "multiple-definition", "cycle", "blocked",
  "crashed", "interrupted"];

export function Cell({ cell, all, edges, elapsed, onEdit, onRun, onDelete, onAdd }: Props) {
  const l = look(cell, all, edges, elapsed);
  const graph = cell.kind === "python" || cell.kind === "mojo";
  const [editing, setEditing] = useState(!graph && cell.code === "");

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
          <button className="icon-btn" onClick={onRun} title="Executar (Shift+Enter)" aria-label="executar célula">
            <Play size={15} aria-hidden="true" />
          </button>
        )}
        <button className="icon-btn" onClick={onDelete} title="Apagar célula" aria-label="apagar célula">
          <Trash size={15} aria-hidden="true" />
        </button>
      </header>

      {graph || editing ? (
        <Editor code={cell.code} kind={cell.kind} diagnostics={cell.diagnostics} label={`código da célula ${cell.id}`}
                autoFocus={editing} onChange={onEdit}
                onRun={graph ? onRun : () => setEditing(false)} />
      ) : (
        <Rendered cell={cell} onEdit={() => setEditing(true)} />
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
