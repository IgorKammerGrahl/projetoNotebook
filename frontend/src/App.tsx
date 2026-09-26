import { useCallback, useEffect, useRef, useState } from "react";
import type { Kind, ServerMsg, Status } from "./protocol";
import { useNotebook } from "./socket";
import { Cell } from "./Cell";
import { Graph } from "./Graph";
import { Network, Play, Skull, Square, WifiOff } from "./icons";
import { Announcements } from "./announcements";

const TIMED: Status[] = ["running", "compiling"];
export function App() {
  const [announcement, setAnnouncement] = useState({ text: "", serial: 0 });
  const announcements = useRef(new Announcements());
  const onMessage = (m: ServerMsg) => {
    const text = announcements.current.receive(m);
    if (text) setAnnouncement((prev) => ({ text, serial: prev.serial + 1 }));
  };
  const { state, send, discardEdits, notice, setNotice, clearNotice } = useNotebook(onMessage);
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
  const run = (id: number, at: number) => { announcements.current.userRuns.add(id); send({ type: "run", cid: id }, at); };
  const [busyEditors, setBusyEditors] = useState(new Set<number>());
  const onBusy = useCallback((id: number, busy: boolean) => setBusyEditors((prev) => {
    if (prev.has(id) === busy) return prev;
    const next = new Set(prev);
    if (busy) next.add(id); else next.delete(id);
    return next;
  }), []);

  return (
    <div className="app">
      <header className="topbar">
        <strong>Notebook</strong>
        {!connected && <span className="chip tone-error"><WifiOff size={14} aria-hidden="true" /> desconectado · reconectando…</span>}
        {kernel.dead && <span className="chip tone-error"><Skull size={14} aria-hidden="true" /> kernel morto</span>}
        {kernel.restarts > 0 && <span className="muted">reinícios do kernel: {kernel.restarts}</span>}
        <span className="spacer" />
        <button onClick={() => send({ type: "run_all" })} disabled={busyEditors.size > 0}
                title={busyEditors.size ? "Aguarde as edições pendentes e resolva os conflitos" : undefined}><Play size={15} aria-hidden="true" /> rodar tudo</button>
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
                  onEdit={send} rejected={state.conflicts[id]} onBusy={onBusy}
                  onNotice={setNotice} onCancelEdits={() => discardEdits(id)}
                  onRun={(at) => run(id, at)}
                  onDelete={() => send({ type: "delete", cid: id })}
                  onAdd={(kind: Kind) => send({ type: "add", code: "", kind, after: id })} />
          ))}
        </main>
        {showGraph && <aside className="graph-panel"><Graph cells={cells} order={order} edges={edges} /></aside>}
      </div>
      <div className="sr-only" aria-live="polite" aria-atomic="true"><span key={announcement.serial}>{announcement.text}</span></div>
    </div>
  );
}
