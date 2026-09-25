// WebSocket connection to the notebook server (token from the page URL, D-016).
import { useCallback, useEffect, useReducer, useRef } from "react";
import type { ClientMsg, ServerMsg } from "./protocol";
import { empty, reduce, type NotebookState } from "./store";

type Action = ServerMsg | { type: "disconnected" };

function reducer(state: NotebookState, a: Action): NotebookState {
  return a.type === "disconnected" ? { ...state, connected: false } : reduce(state, a);
}

export function useNotebook(onMessage?: (m: ServerMsg) => void) {
  const [state, dispatch] = useReducer(reducer, empty);
  const ws = useRef<WebSocket | null>(null);
  const outbox = useRef<ClientMsg[]>([]);  // messages sent while disconnected: flushed on reconnect
  const handler = useRef(onMessage);
  handler.current = onMessage;

  useEffect(() => {
    const token = new URLSearchParams(location.search).get("token") ?? "";
    let retry: ReturnType<typeof setTimeout>, delay = 250, closed = false;
    const connect = () => {
      const sock = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?token=${encodeURIComponent(token)}`);
      ws.current = sock;
      sock.onopen = () => {
        delay = 250;
        for (const m of outbox.current.splice(0)) sock.send(JSON.stringify(m));
      };
      sock.onmessage = (e) => {
        const m = JSON.parse(e.data) as ServerMsg;
        dispatch(m);
        handler.current?.(m);
      };
      sock.onclose = () => {
        dispatch({ type: "disconnected" });
        if (!closed) retry = setTimeout(connect, (delay = Math.min(delay * 2, 5000)));
      };
    };
    connect();
    return () => { closed = true; clearTimeout(retry); ws.current?.close(); };
  }, []);

  const send = useCallback((m: ClientMsg) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(m));
      return;
    }
    // never drop a Shift+Enter or an edit silently; an edit carries the whole code,
    // so only the latest one per cell needs to survive
    if (m.type === "edit") outbox.current = outbox.current.filter((x) => !(x.type === "edit" && x.cid === m.cid));
    outbox.current.push(m);
  }, []);

  return { state, send };
}
