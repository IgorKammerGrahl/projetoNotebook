// Diagnostics in the editor (D-019). Pure helpers (tested) + a CodeMirror extension.
//
// - Server diagnostics carry (line, col) for the code of the build that produced them.
//   They are converted to document positions once, when they arrive, and from then on
//   mapped through every edit, so an underline stays on the text it was about.
// - While the user types, the diagnostics on the cursor's line are not underlined; they
//   show once the cursor leaves the line or after TYPING_PAUSE_MS without keystrokes.
//   The gutter marker and the header count are always shown.
import { RangeSet, StateEffect, StateField, type Text } from "@codemirror/state";
import { Decoration, EditorView, GutterMarker, ViewPlugin, gutter, type DecorationSet } from "@codemirror/view";
import type { Diagnostic } from "./protocol";

export const TYPING_PAUSE_MS = 800;

export interface Placed { from: number; to: number; message: string; source: string }

/** 1-based line/col -> [from, to): to the end of the word at `col`, else one char. */
export function place(doc: Text, d: Diagnostic): Placed {
  const line = doc.line(Math.min(Math.max(d.line, 1), doc.lines));
  const from = Math.min(line.from + Math.max(d.col - 1, 0), line.to);
  const word = /^\w+/.exec(line.text.slice(from - line.from));
  const to = word ? from + word[0].length : Math.min(from + 1, line.to);
  return to > from ? { from, to, message: d.message, source: d.source }
                   : { from: line.from, to: line.to, message: d.message, source: d.source };
}

/** Which placed diagnostics get an underline right now. */
export function underlined(diags: Placed[], lineOf: (pos: number) => number, typingLine: number | null): Placed[] {
  return typingLine === null ? diags : diags.filter((d) => lineOf(d.from) !== typingLine);
}

export const setDiagnostics = StateEffect.define<Diagnostic[]>();
const typingPaused = StateEffect.define<null>();

const diagField = StateField.define<Placed[]>({
  create: () => [],
  update(value, tr) {
    for (const e of tr.effects) if (e.is(setDiagnostics)) return e.value.map((d) => place(tr.state.doc, d));
    if (!tr.docChanged) return value;
    return value.map((d) => ({ ...d, from: tr.changes.mapPos(d.from, 1), to: tr.changes.mapPos(d.to, -1) }))
                .map((d) => (d.to > d.from ? d : { ...d, to: Math.min(d.from + 1, tr.state.doc.length) }));
  },
});

/** Line where the user is typing, or null once they paused or moved away. */
const typingField = StateField.define<number | null>({
  create: () => null,
  update(value, tr) {
    for (const e of tr.effects) if (e.is(typingPaused)) return null;
    const line = tr.state.doc.lineAt(tr.state.selection.main.head).number;
    if (tr.docChanged && (tr.isUserEvent("input") || tr.isUserEvent("delete"))) return line;
    return value !== null && line !== value ? null : value;
  },
});

const pauseTimer = ViewPlugin.fromClass(class {
  timer: ReturnType<typeof setTimeout> | undefined;
  constructor(readonly view: EditorView) {}
  update(u: { docChanged: boolean }) {
    if (!u.docChanged) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.view.dispatch({ effects: typingPaused.of(null) }), TYPING_PAUSE_MS);
  }
  destroy() { clearTimeout(this.timer); }
});

const decorations = EditorView.decorations.compute([diagField, typingField], (state): DecorationSet => {
  const shown = underlined(state.field(diagField), (p) => state.doc.lineAt(p).number, state.field(typingField));
  return Decoration.set(
    shown.map((d) => Decoration.mark({ class: "cm-diag", attributes: { title: `${d.message} (${d.source})` } })
                              .range(d.from, d.to)), true);
});

class DiagMarker extends GutterMarker {
  constructor(readonly text: string) { super(); }
  eq(other: DiagMarker) { return other.text === this.text; }
  toDOM() {
    const el = document.createElement("span");
    el.className = "cm-diag-marker";
    el.textContent = "●";
    el.title = this.text;
    el.setAttribute("aria-label", `problema: ${this.text}`);
    return el;
  }
}

const diagGutter = gutter({
  class: "cm-diag-gutter",
  lineMarkerChange: (u) => u.startState.field(diagField) !== u.state.field(diagField),
  markers: (view) => {
    const byLine = new Map<number, string[]>();
    for (const d of view.state.field(diagField)) {
      const l = view.state.doc.lineAt(d.from);
      byLine.set(l.from, [...(byLine.get(l.from) ?? []), d.message]);
    }
    return RangeSet.of([...byLine].sort(([a], [b]) => a - b)
      .map(([from, msgs]) => new DiagMarker(msgs.join("\n")).range(from)));
  },
});

export const diagnosticsExtension = [diagField, typingField, pauseTimer, decorations, diagGutter];
