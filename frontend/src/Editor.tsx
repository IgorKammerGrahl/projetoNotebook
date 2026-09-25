// CodeMirror 6 editor for one cell. The local document is the source of truth while
// the user edits; code from the server replaces it only when the editor is not focused.
import { useEffect, useRef } from "react";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { python } from "@codemirror/lang-python";
import { bracketMatching, indentOnInput, syntaxHighlighting, defaultHighlightStyle } from "@codemirror/language";
import { Annotation, EditorState, Prec } from "@codemirror/state";
import { EditorView, keymap, lineNumbers, drawSelection } from "@codemirror/view";
import { diagnosticsExtension, setDiagnostics } from "./diagnostics";
import type { Diagnostic, Kind } from "./protocol";

const External = Annotation.define<boolean>();  // server-originated change: never echoed back

interface Props {
  code: string;
  kind: Kind;
  diagnostics: Diagnostic[];
  onChange: (code: string) => void;
  onRun: () => void;
  autoFocus?: boolean;
  label: string;
}

export function Editor({ code, kind, diagnostics, onChange, onRun, autoFocus, label }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const cb = useRef({ onChange, onRun });
  cb.current = { onChange, onRun };

  useEffect(() => {
    const v = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: code,
        extensions: [
          Prec.highest(keymap.of([{ key: "Shift-Enter", run: () => { cb.current.onRun(); return true; } }])),
          lineNumbers(), history(), drawSelection(), indentOnInput(), bracketMatching(),
          syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
          keymap.of([indentWithTab, ...defaultKeymap, ...historyKeymap]),
          kind === "python" || kind === "mojo" ? python() : [],  // Python grammar approximates Mojo (D-015)
          diagnosticsExtension,
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({ "aria-label": label }),
          EditorView.updateListener.of((u) => {
            if (u.docChanged && !u.transactions.some((t) => t.annotation(External))) {
              cb.current.onChange(u.state.doc.toString());
            }
          }),
        ],
      }),
    });
    view.current = v;
    if (autoFocus) v.focus();
    return () => v.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind]);

  useEffect(() => {  // external code (reload, another client): only when not being edited
    const v = view.current;
    if (v && !v.hasFocus && v.state.doc.toString() !== code) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: code }, annotations: External.of(true) });
    }
  }, [code]);

  const key = JSON.stringify(diagnostics);
  useEffect(() => {  // re-place only when the diagnostics really changed (keeps edit mapping)
    view.current?.dispatch({ effects: setDiagnostics.of(diagnostics) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return <div className="editor" ref={host} />;
}
