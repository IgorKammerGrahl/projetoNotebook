// React binding for the notebook connection (token from the page URL, D-016).
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { ClientMsg, ServerMsg } from "./protocol";
import { Connection } from "./connection";
import { empty, reduce, type NotebookState } from "./store";

type Action = ServerMsg | { type: "status"; connected: boolean };

function reducer(state: NotebookState, a: Action): NotebookState {
  return a.type === "status" ? { ...state, connected: a.connected } : reduce(state, a);
}

export function useNotebook(onMessage?: (m: ServerMsg) => void) {
  const [state, dispatch] = useReducer(reducer, empty);
  const [notice, setNotice] = useState("");
  const conn = useRef<Connection | null>(null);
  const handler = useRef(onMessage);
  handler.current = onMessage;

  useEffect(() => {
    const token = new URLSearchParams(location.search).get("token") ?? "";
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?token=${encodeURIComponent(token)}`;
    const c = new Connection(url, {
      onMessage: (m) => { dispatch(m); handler.current?.(m); },
      onStatus: (connected) => dispatch({ type: "status", connected }),
      onNotice: setNotice,
    });
    conn.current = c;
    return () => c.close();
  }, []);

  const send = useCallback((m: ClientMsg, queuedAt?: number) => conn.current?.send(m, queuedAt), []);
  const discardEdits = useCallback((cid: number) => conn.current?.discardEdits(cid), []);
  return { state, send, discardEdits, notice, setNotice, clearNotice: () => setNotice("") };
}
