"""Reactive engine: Python (D-008) and Mojo (D-009) cells in one graph."""
import ast
import contextlib
import io
import itertools
import symtable
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import mojo
from .fmt import Cell

# Statuses: pending | ok | error | syntax-error | compile-error | multiple-definition | cycle | blocked
GRAPH_KINDS = ("python", "mojo")


def _private(name: str) -> bool:
    return name.startswith("_")


def _top_level_nodes(tree: ast.AST):
    """Nodes in the module scope, without descending into nested scopes."""
    scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
              ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, scopes))


def _global_refs(table: symtable.SymbolTable) -> set[str]:
    """Names referenced in nested scopes that resolve to globals."""
    refs = set()
    for child in table.get_children():
        refs |= {s.get_name() for s in child.get_symbols() if s.is_global() and s.is_referenced()}
        refs |= _global_refs(child)
    return refs


def analyze(code: str) -> tuple[set[str], set[str]]:
    """(defs, refs) for a cell's globals. Raises SyntaxError."""
    tree = ast.parse(code)
    top = symtable.symtable(code, "<cell>", "exec")
    defs, refs = set(), _global_refs(top)
    for s in top.get_symbols():
        if s.is_comp_iter():
            continue
        if s.is_assigned() or s.is_imported():
            defs.add(s.get_name())
        if s.is_referenced():
            refs.add(s.get_name())
    for node in _top_level_nodes(tree):
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            refs.add(node.target.id)
        elif isinstance(node, ast.Delete):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    refs.add(t.id)
                    defs.discard(t.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            # bound only inside the handler: neither exported nor read from other cells
            defs.discard(node.name)
            refs.discard(node.name)
    defs = {n for n in defs if not _private(n)}
    refs = {n for n in refs if not _private(n)} - defs
    return defs, refs


@dataclass
class CellState:
    code: str
    kind: str = "python"
    defs: set[str] = field(default_factory=set)
    refs: set[str] = field(default_factory=set)
    status: str = "pending"
    error: str = ""
    output: str = ""
    ran_defs: set[str] = field(default_factory=set)  # names bound by the last run


class Engine:
    def __init__(self, cache_dir: Path | str = ".nbcache"):
        self.cells: dict[int, CellState] = {}  # insertion order = file order
        self.ns: dict = {"__name__": "__notebook__"}
        self.mojo = mojo.MojoRunner(Path(cache_dir))
        self._ids = itertools.count(1)

    # --- public API: each call returns the ids that ran, in order ---

    def add(self, code: str, kind: str = "python") -> tuple[int, list[int]]:
        cid = next(self._ids)
        self.cells[cid] = self._analyzed(code, kind)
        return cid, self._react({cid}, self.cells[cid].defs)

    def edit(self, cid: int, code: str) -> list[int]:
        old = self.cells[cid].defs
        state = self._analyzed(code, self.cells[cid].kind)
        state.ran_defs = self.cells[cid].ran_defs
        self.cells[cid] = state
        return self._react({cid}, old | state.defs)

    def delete(self, cid: int) -> list[int]:
        cell = self.cells.pop(cid)
        for name in cell.ran_defs:
            self.ns.pop(name, None)
        return self._react(set(), cell.defs)

    def load(self, cells: list[Cell]) -> list[int]:
        """Add graph cells in file order and run them all once, topologically."""
        for c in cells:
            if c.kind in GRAPH_KINDS:
                self.cells[next(self._ids)] = self._analyzed(c.code, c.kind)
        return self.run_all()

    def run_all(self) -> list[int]:
        return self._react(set(self.cells), set())

    # --- internals ---

    @staticmethod
    def _analyzed(code: str, kind: str) -> CellState:
        state = CellState(code, kind)
        try:
            if kind == "mojo":
                iface = mojo.parse_interface(code)
                state.defs, state.refs = {n for n, _ in iface.outs}, {n for n, _ in iface.ins}
            else:
                state.defs, state.refs = analyze(code)
        except SyntaxError:
            state.status, state.error = "syntax-error", traceback.format_exc(limit=0)
        except mojo.InterfaceError as e:
            state.status, state.error = "syntax-error", f"interface {e}"
        return state

    def _graph(self):
        definers: dict[str, list[int]] = {}
        for cid, c in self.cells.items():
            for name in c.defs:
                definers.setdefault(name, []).append(cid)
        # Every definer is a parent, so readers of a duplicated name get blocked.
        parents = {cid: {d for n in c.refs for d in definers.get(n, ())}
                   for cid, c in self.cells.items()}
        children = {cid: set() for cid in self.cells}
        for cid, ps in parents.items():
            for p in ps:
                children[p].add(cid)
        dup = {cid for ds in definers.values() if len(ds) > 1 for cid in ds}
        return parents, children, dup

    def _react(self, roots: set[int], names: set[str]) -> list[int]:
        parents, children, dup = self._graph()
        roots = roots | {cid for cid, c in self.cells.items() if (c.defs | c.refs) & names}
        stale, todo = set(), list(roots)
        while todo:
            cid = todo.pop()
            if cid not in stale:
                stale.add(cid)
                todo.extend(children[cid])

        for cid in stale:  # clear everything these cells defined last time
            for name in self.cells[cid].ran_defs:
                self.ns.pop(name, None)
            self.cells[cid].ran_defs = set()

        order, cyclic, downstream = self._toposort(stale, parents, children)
        ran = []
        for cid in order:
            cell = self.cells[cid]
            if cell.status == "syntax-error":
                continue
            if cid in dup:
                cell.status, cell.error = "multiple-definition", self._dup_msg(cid)
            elif cid in cyclic:
                cell.status, cell.error = "cycle", "cell is part of a dependency cycle"
            elif cid in downstream or any(self.cells[p].status != "ok" for p in parents[cid]):
                cell.status, cell.error = "blocked", "an ancestor cell has an error"
            else:
                self._run(cid)
                ran.append(cid)
        return ran

    def _toposort(self, stale, parents, children):
        """Kahn over the stale subgraph, ties broken by file position.
        Nodes left over are cycle members or their descendants; those are
        never run, whatever their file position."""
        pos = {cid: i for i, cid in enumerate(self.cells)}
        indeg = {cid: len(parents[cid] & stale) for cid in stale}
        ready = sorted((c for c in stale if indeg[c] == 0), key=pos.get)
        order = []
        while ready:
            cid = ready.pop(0)
            order.append(cid)
            for ch in children[cid] & stale:
                indeg[ch] -= 1
                if indeg[ch] == 0:
                    ready.append(ch)
                    ready.sort(key=pos.get)
        left = stale - set(order)

        # ponytail: O(n²) reachability; Tarjan's SCC if notebooks reach thousands of cells
        def on_cycle(start):
            seen, todo = set(), list(children[start] & left)
            while todo:
                c = todo.pop()
                if c == start:
                    return True
                if c not in seen:
                    seen.add(c)
                    todo.extend(children[c] & left)
            return False

        cyclic = {c for c in left if on_cycle(c)}
        return order + sorted(left, key=pos.get), cyclic, left - cyclic

    def _dup_msg(self, cid: int) -> str:
        mine = self.cells[cid].defs
        clash = sorted(n for o, c in self.cells.items() if o != cid for n in c.defs & mine)
        return f"name(s) defined by more than one cell: {', '.join(clash)}"

    def _run(self, cid: int):
        if self.cells[cid].kind == "mojo":
            return self._run_mojo(cid)
        cell, out = self.cells[cid], io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                exec(compile(cell.code, f"<cell {cid}>", "exec"), self.ns)
            cell.status, cell.error = "ok", ""
            cell.ran_defs = set(cell.defs)
        except BaseException:
            cell.status, cell.error = "error", traceback.format_exc()
            for name in cell.defs:  # no half-defined state for descendants
                self.ns.pop(name, None)
        cell.output = out.getvalue()

    def _run_mojo(self, cid: int):
        cell = self.cells[cid]
        iface = mojo.parse_interface(cell.code)  # already validated by _analyzed
        cell.output = ""
        try:
            lib = self.mojo.load(cell.code, iface)
        except mojo.CompileError as e:
            cell.status, cell.error = "compile-error", str(e)
            return
        missing = [n for n, _ in iface.ins if n not in self.ns]
        try:
            if missing:
                raise mojo.CellError(f"name(s) not defined by any cell: {', '.join(missing)}")
            outs, cell.output = self.mojo.call(lib, iface, {n: self.ns[n] for n, _ in iface.ins})
        except mojo.CellError as e:
            cell.status, cell.error, cell.output = "error", str(e), e.output
            return
        self.ns.update(outs)
        cell.status, cell.error, cell.ran_defs = "ok", "", set(cell.defs)
