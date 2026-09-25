import { useEffect, useRef, useState } from "react";
import type { Kind, ServerMsg, Status } from "./protocol";
import { useNotebook } from "./socket";
import { Cell } from "./Cell";
import { Graph } from "./Graph";
import { Network, Play, Skull, Square, WifiOff } from "./icons";

const TIMED: Status[] = ["running", "compiling"];
const ANNOUNCE: Partial<Record<Status, string>> = {
  ok: "ok", error: "erro", "syntax-error": "erro de sintaxe", "compile-error": "erro de compilação",
  crashed: "derrubou o kernel", interrupted: "interrompida", blocked: "bloqueada",
};

export function App() {
  const [announcement, setAnnouncement] = useState("");
  const userRuns = useRef(new Set<number>());   // cells whose result the user is waiting for
  const onMessage = (m: ServerMsg) => {
    if (m.type !== "update") return;
    for (const c of m.cells) {
      if (userRuns.current.has(c.id) && ANNOUNCE[c.status]) {
        userRuns.current.delete(c.id);
        setAnnouncement(`Célula ${c.id}: ${ANNOUNCE[c.status]}`);
      }
    }
  };
  const { state, send, notice, clearNotice } = useNotebook(onMessage);
  const { cells, order, edges, kernel, connected } = state;

  // elapsed time of running / compiling cells, ticking every 100 ms
  const since = useRef(new Map<number, { status: Status; t: number }>());
  const [, setTick] = useState(0);  // only forces a re-render; time is read at render
  for (const id of order) {
    const c = cells[id];
    const prev = since.current.get(id);
    if (TIMED.includes(c.status) && prev?.status !== c.status) since.current.set(id, { status: c.status, t: Date.now() });
    if (!TIMED.includes(c.status)) since.current.delete(id);
  }
  const anyTimed = order.some((id) => TIMED.includes(cells[id].status));
  useEffect(() => {
    if (!anyTimed) return;
    const t = setInterval(() => setTick((x) => x + 1), 100);
    return () => clearInterval(t);
  }, [anyTimed]);

  const running = order.some((id) => cells[id].status === "running");
  const [showGraph, setShowGraph] = useState(true);
  const run = (id: number) => { userRuns.current.add(id); send({ type: "run", cid: id }); };

  return (
    <div className="app">
      <header className="topbar">
        <strong>Notebook</strong>
        {!connected && <span className="chip tone-error"><WifiOff size={14} aria-hidden="true" /> desconectado · reconectando…</span>}
        {kernel.dead && <span className="chip tone-error"><Skull size={14} aria-hidden="true" /> kernel morto</span>}
        {kernel.restarts > 0 && <span className="muted">reinícios do kernel: {kernel.restarts}</span>}
        <span className="spacer" />
        <button onClick={() => send({ type: "run_all" })}><Play size={15} aria-hidden="true" /> rodar tudo</button>
        <button onClick={() => send({ type: "stop" })} disabled={!running} className="stop"
                title="Mata o kernel; a célula em execução fica interrompida e o resto é reexecutado">
          <Square size={15} aria-hidden="true" /> parar
        </button>
        <button onClick={() => setShowGraph((g) => !g)} aria-pressed={showGraph}>
          <Network size={15} aria-hidden="true" /> grafo
        </button>
      </header>
      {notice && <div role="status" className="connection-notice">{notice} <button onClick={clearNotice}>fechar aviso</button></div>}
      <div className="body">
        <main className="cells">
          {order.length === 0 && connected && (
            <button onClick={() => send({ type: "add", code: "", kind: "python", after: null })}>+ primeira célula</button>
          )}
          {order.map((id) => (
            <Cell key={id} cell={cells[id]} all={cells} edges={edges}
                  elapsed={since.current.has(id) ? Math.max(0, Date.now() - since.current.get(id)!.t) : undefined}
                  onEdit={(code) => send({ type: "edit", cid: id, code })}
                  onRun={() => run(id)}
                  onDelete={() => send({ type: "delete", cid: id })}
                  onAdd={(kind: Kind) => send({ type: "add", code: "", kind, after: id })} />
          ))}
        </main>
        {showGraph && <aside className="graph-panel"><Graph cells={cells} order={order} edges={edges} /></aside>}
      </div>
      <div className="sr-only" aria-live="polite">{announcement}</div>
    </div>
  );
}
