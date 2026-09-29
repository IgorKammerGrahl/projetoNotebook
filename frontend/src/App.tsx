import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Kind, ServerMsg, Status } from "./protocol";
import { useNotebook } from "./socket";
import { Cell } from "./Cell";
import { Graph } from "./Graph";
import { Network, Play, Skull, Square, WifiOff } from "./icons";
import { Announcements } from "./announcements";
import { savingStatus } from "./saving";
import { RecoveryStore, type RecoveryEntry } from "./recovery";
import { RecoveryPanel } from "./RecoveryPanel";
import { IndexedRecoveryDatabase } from "./recovery-db";

const TIMED: Status[] = ["running", "compiling"];
export function App() {
  const [recoveryTick, refreshRecovery] = useState(0);
  const [recovery] = useState(() => new RecoveryStore(new IndexedRecoveryDatabase(), () => refreshRecovery((n) => n + 1)));
  const [restores, setRestores] = useState<Record<string, {
    id: string; entryId: string; code: string; version: string; expectedCode: string;
  }>>({});
  const adding = useRef(new Map<string, RecoveryEntry>());
  const [creating, setCreating] = useState(new Set<string>());
  const [addedDrafts, setAddedDrafts] = useState<{ cid: number; entry: RecoveryEntry }[]>([]);
  const [additionFailed, setAdditionFailed] = useState(0);
  const [announcement, setAnnouncement] = useState({ text: "", serial: 0 });
  const announcements = useRef(new Announcements());
  const onMessage = (m: ServerMsg) => {
    if (m.type === "error" && adding.current.size) setAdditionFailed((n) => n + 1);
    if (m.type === "added" && m.request && adding.current.has(m.request)) {
      const entry = adding.current.get(m.request)!;
      adding.current.delete(m.request);
      setAddedDrafts((prev) => [...prev, { cid: m.cid, entry }]);
    }
    const text = announcements.current.receive(m);
    if (text) setAnnouncement((prev) => ({ text, serial: prev.serial + 1 }));
  };
  const { state, send, discardEdits, discardAddition, pendingChanges, hasPendingChanges, notice, setNotice, clearNotice } = useNotebook(onMessage);
  const { cells, order, edges, kernel, connected } = state;
  useEffect(() => {
    if (connected && !additionFailed) return;
    if (!adding.current.size) { if (additionFailed) setAdditionFailed(0); return; }
    for (const request of adding.current.keys()) discardAddition(request);
    adding.current.clear();
    setCreating(new Set());
    setNotice("A criação da célula não foi confirmada. Verifique se ela apareceu antes de tentar recuperar novamente.");
    setAdditionFailed(0);
  }, [connected, additionFailed, discardAddition, setNotice]);
  useLayoutEffect(() => {
    if (state.document) recovery.open(state.document.id, state.document.session);
  }, [recovery, state.document?.id, state.document?.session]);
  useEffect(() => recovery.subscribe(), [recovery]);
  useEffect(() => {
    for (const { cid, entry } of addedDrafts) {
      const cell = cells[cid];
      if (!cell) continue;
      setAddedDrafts((prev) => prev.filter((d) => d.entry.id !== entry.id));
      void (async () => {
        if (cell.code === entry.code && cell.kind === entry.kind && await recovery.recover(entry, cell)) {
          recovery.accepted(cell.uid, cell.code, cell.version);
        }
        setCreating((prev) => { const next = new Set(prev); next.delete(entry.id); return next; });
      })();
    }
    recovery.sync(Object.values(cells), state.save, connected);
  }, [recovery, recoveryTick, cells, state.save, connected, addedDrafts]);

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
  const busyChecks = useRef(new Map<number, () => boolean>());
  const onBusy = useCallback((id: number, busy: boolean, check?: () => boolean) => {
    if (check) busyChecks.current.set(id, check); else busyChecks.current.delete(id);
    setBusyEditors((prev) => {
      if (prev.has(id) === busy) return prev;
      const next = new Set(prev);
      if (busy) next.add(id); else next.delete(id);
      return next;
    });
  }, []);
  const saving = savingStatus(state.save, connected, pendingChanges || busyEditors.size > 0);
  const reload = state.save?.reload;
  const readinessSent = useRef<string | null>(null);
  useEffect(() => {
    if (!reload) { readinessSent.current = null; return; }
    if (!connected || readinessSent.current === reload) return;
    readinessSent.current = reload;
    // After React applies inert, inspect actual drafts/transport, not a delayed UI flag.
    send({ type: "reload_ready", request: reload,
      ready: !hasPendingChanges() && ![...busyChecks.current.values()].some((check) => check()) });
  }, [reload, connected, hasPendingChanges, send]);
  const resolutionDisabled = !connected || !!reload || pendingChanges || busyEditors.size > 0;

  return (
    <div className="app">
      <header className="topbar">
        <strong>Notebook</strong>
        {recovery.localStatus && <span role="status" aria-label="recuperação local" className="muted">{recovery.localStatus}</span>}
        <span role="status" aria-label="salvamento" aria-atomic="true" title={saving.detail}
              className={saving.error ? "chip tone-error" : "muted"}>{saving.text}</span>
        {!connected && <span className="chip tone-error"><WifiOff size={14} aria-hidden="true" /> desconectado · reconectando…</span>}
        {kernel.dead && <span className="chip tone-error"><Skull size={14} aria-hidden="true" /> kernel morto</span>}
        {kernel.restarts > 0 && <span className="muted">reinícios do kernel: {kernel.restarts}</span>}
        <span className="spacer" />
        <button onClick={() => send({ type: "run_all" })} disabled={!!reload || busyEditors.size > 0}
                title={busyEditors.size ? "Aguarde as edições pendentes e resolva os conflitos" : undefined}><Play size={15} aria-hidden="true" /> rodar tudo</button>
        <button onClick={() => send({ type: "stop" })} disabled={!!reload || !running} className="stop"
                title="Mata o kernel; a célula em execução fica interrompida e o resto é reexecutado">
          <Square size={15} aria-hidden="true" /> parar
        </button>
        <button onClick={() => setShowGraph((g) => !g)} aria-pressed={showGraph}>
          <Network size={15} aria-hidden="true" /> grafo
        </button>
      </header>
      {saving.retry && <div className="connection-notice">
        <span>{saving.detail}</span>{" "}
        <button onClick={() => send({ type: "retry_save" })}>tentar salvar novamente</button>
      </div>}
      {state.save?.status === "conflict" && <section className="connection-notice" aria-label="conflito no arquivo">
        <p role="alert">{saving.detail} Suas edições continuam nesta sessão. O autosave está suspenso.</p>
        {state.save.conflict?.preserved && <p>Versão deslocada preservada em: <code>{state.save.conflict.preserved}</code></p>}
        {state.save.copy && <p>Cópia da sessão: <code>{state.save.copy.path}</code>
          {state.save.copy.revision !== state.save.revision && " (há edições mais recentes; preserve outra cópia)"}</p>}
        <p>Primeiro preserve uma cópia. Depois carregue o arquivo externo, sem executar suas células.
          Todas as abas abertas precisam estar sem edições pendentes.</p>
        <button disabled={resolutionDisabled} onClick={() => send({ type: "preserve_copy",
          revision: state.save!.revision, session: state.document!.session })}>preservar cópia da sessão</button>{" "}
        <button disabled={resolutionDisabled || state.save.copy?.revision !== state.save.revision}
          onClick={() => send({ type: "reload_external", revision: state.save!.revision,
            session: state.document!.session })}>carregar versão externa</button>
      </section>}
      {notice && <div role="status" className="connection-notice">{notice} <button onClick={clearNotice}>fechar aviso</button></div>}
      {recovery.warning && <p role="alert" className="connection-notice">{recovery.warning}</p>}
      <div inert={!!reload}><RecoveryPanel entries={recovery.entries} cells={Object.values(cells)} connected={connected}
        blocked={(cell) => busyEditors.has(cell.id) || recovery.hasOwn(cell.uid)} creating={creating}
        onDiscard={(entry) => recovery.discard(entry)}
        onRecover={async (entry, cell) => {
          if (await recovery.recover(entry, cell)) setRestores((prev) => ({ ...prev,
            [`${state.document?.session}:${cell.uid}`]: { id: crypto.randomUUID(), entryId: entry.id,
              code: entry.code, version: cell.version, expectedCode: cell.code } }));
        }}
        onNew={(entry) => {
          const request = crypto.randomUUID();
          adding.current.set(request, entry);
          setCreating((prev) => new Set(prev).add(entry.id));
          send({ type: "add", code: entry.code, kind: entry.kind, after: order.at(-1) ?? null, request });
        }} /></div>
      <div className="body" inert={!!reload}>
        <main className="cells">
          {order.length === 0 && connected && (
            <button onClick={() => send({ type: "add", code: "", kind: "python", after: null })}>+ primeira célula</button>
          )}
          {order.map((id) => (
            <Cell key={`${state.document?.id}:${state.document?.session}:${cells[id].uid ?? id}`} cell={cells[id]} all={cells} edges={edges}
                  elapsed={since.current.has(id) ? Math.max(0, Date.now() - since.current.get(id)!.t) : undefined}
                  onEdit={send} rejected={state.conflicts[id]} onBusy={onBusy}
                  onNotice={setNotice} onCancelEdits={() => discardEdits(id)}
                  restore={restores[`${state.document?.session}:${cells[id].uid}`]}
                  onDraft={(code, base) => recovery.record(cells[id], code, base)}
                  onAccepted={(code, version) => recovery.accepted(cells[id].uid, code, version)}
                  onDiscardDraft={() => recovery.discardOwn(cells[id].uid)}
                  onRestoreRejected={(entryId, code) => {
                    recovery.cancelRecovery(cells[id].uid, entryId, code);
                    setNotice("A célula mudou durante a recuperação. Revise o rascunho novamente.");
                  }}
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
