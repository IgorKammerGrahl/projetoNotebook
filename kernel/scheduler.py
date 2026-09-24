"""Scheduler (D-012): the graph and every cell's state, as a pure state machine.

No I/O. Events come in as method calls (edit, run, compiled, ran, kernel_died,
stop...); each returns the actions the driver must perform, in order:

    Compile  build a Mojo cell (may run in parallel, may be cancelled)
    Cancel   kill an in-flight build
    Exec     run one cell in the executor (one at a time)
    Delete   drop names from the executor's namespace
    Restart  start a fresh kernel process (D-013)
    Kill     kill the kernel process (stop button)

Executions happen strictly one at a time: a new Exec is only emitted after
`ran()` or `kernel_died()` reports the previous one.
"""
import itertools
import re
from dataclasses import dataclass, field

from . import mojo
from .analysis import analyze

GRAPH_KINDS = ("python", "mojo")
_UNSAFE = re.compile(r"\bunsafe_\w+")


@dataclass
class Compile:
    cid: int
    code: str


@dataclass
class Cancel:
    cid: int


@dataclass
class Exec:
    cid: int
    kind: str
    code: str
    defs: list
    delete: list
    artifact: object = None


@dataclass
class Delete:
    names: list


@dataclass
class Restart:
    pass


@dataclass
class Kill:
    pass


# Statuses: idle | edited | stale | compiling | running | ok | error | syntax-error | compile-error
#           | multiple-definition | cycle | blocked | crashed | interrupted
@dataclass
class CellState:
    code: str
    kind: str = "python"
    defs: set[str] = field(default_factory=set)
    refs: set[str] = field(default_factory=set)
    syntax_error: str = ""            # static; set by analysis
    status: str = "idle"
    error: str = ""
    output: str = ""
    previews: dict = field(default_factory=dict)
    ran_defs: set[str] = field(default_factory=set)     # names this cell holds in the executor now
    defs_at_run: set[str] = field(default_factory=set)  # names claimed since the last run (every edit's defs)
    ran_once: bool = False
    quarantine: str = ""              # "" | "crashed" | "interrupted" (D-013)
    artifact: object = None           # Mojo: built artifact for `artifact_code`
    artifact_code: str | None = None
    compiling: str | None = None      # Mojo: code of the in-flight build


def _analyzed(code: str, kind: str) -> CellState:
    state = CellState(code, kind)
    if kind not in GRAPH_KINDS:  # markdown / html: kept for the file, never scheduled
        return state
    try:
        if kind == "mojo":
            iface = mojo.parse_interface(code)
            state.defs, state.refs = {n for n, _ in iface.outs}, {n for n, _ in iface.ins}
        else:
            state.defs, state.refs = analyze(code)
    except SyntaxError as e:
        state.syntax_error = f"{type(e).__name__}: {e}"
    except mojo.InterfaceError as e:
        state.syntax_error = f"interface {e}"
    return state


def toposort(nodes: set, parents, children, pos) -> tuple[list, set, set]:
    """Kahn over `nodes`, ties broken by file position. Returns (order, cyclic,
    downstream): leftover nodes are cycle members or their descendants."""
    indeg = {c: len(parents[c] & nodes) for c in nodes}
    ready = sorted((c for c in nodes if indeg[c] == 0), key=pos.get)
    order = []
    while ready:
        cid = ready.pop(0)
        order.append(cid)
        for ch in children[cid] & nodes:
            indeg[ch] -= 1
            if indeg[ch] == 0:
                ready.append(ch)
                ready.sort(key=pos.get)
    left = nodes - set(order)

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


class Scheduler:
    MAX_ORPHAN_DEATHS = 3  # kernel deaths in a row with no cell to blame (D-013)

    def __init__(self):
        self.cells: dict[int, CellState] = {}  # insertion order = file order
        self.queue: set[int] = set()           # scheduled, waiting to run
        self.running: Exec | None = None
        self.stop_requested = False
        self.orphan_deaths = 0
        self.kernel_dead = False
        self.changed: set[int] = set()         # for the driver to broadcast
        self._ids = itertools.count(1)

    # ---------------- events ----------------

    def add(self, code: str, kind: str = "python", after: int | None = None) -> int:
        cid = next(self._ids)
        state = _analyzed(code, kind)
        if after is None or after not in self.cells:
            self.cells[cid] = state
        else:
            items = list(self.cells.items())
            i = [c for c, _ in items].index(after) + 1
            self.cells = dict(items[:i] + [(cid, state)] + items[i:])
        self.changed.add(cid)
        return cid

    def edit(self, cid: int, code: str) -> list:
        """New code, not run (explicit execution, review item 3). Cancels an in-flight build."""
        c = self.cells[cid]
        actions = [Cancel(cid)] if c.compiling else []
        c.compiling = None
        new = _analyzed(code, c.kind)
        c.defs_at_run |= c.defs  # readers/co-definers of names it drops must re-run on the next run
        c.code, c.defs, c.refs, c.syntax_error = new.code, new.defs, new.refs, new.syntax_error
        c.quarantine = ""
        if cid in self.queue:
            actions += self._ensure_compiled(cid)  # runs the new code when its turn comes
        elif self.running is None or self.running.cid != cid:
            self._set(cid, "edited")
        return actions

    def run(self, cid: int) -> list:
        """Explicit run (Shift+Enter): the cell plus everything its names reach."""
        c = self.cells[cid]
        c.quarantine = ""  # an explicit run lifts quarantine: each retry is a user action
        names = c.defs_at_run | c.defs | c.ran_defs
        roots = {cid} | {o for o, x in self.cells.items() if (x.defs | x.refs) & names}
        return self._schedule(roots)

    def run_all(self) -> list:
        return self._schedule(set(self.cells))

    def delete(self, cid: int) -> list:
        c = self.cells.pop(cid)
        self.queue.discard(cid)
        actions = [Cancel(cid)] if c.compiling else []
        if c.ran_defs:
            actions.append(Delete(sorted(c.ran_defs)))
        names = c.defs | c.ran_defs | c.defs_at_run
        roots = {o for o, x in self.cells.items() if (x.defs | x.refs) & names}
        return actions + self._schedule(roots)

    def compiled(self, cid: int, code: str, artifact=None, error: str = "") -> list:
        c = self.cells.get(cid)
        if c is None or c.compiling != code:
            return []  # obsolete: the cell was edited, deleted or rebuilt meanwhile
        c.compiling = None
        actions = []
        if error:
            if cid in self.queue:
                actions += self._resolve(cid, "compile-error", error)
            else:
                self._set(cid, "compile-error", error)
        else:
            c.artifact, c.artifact_code = artifact, code
            if cid in self.queue:
                self._set(cid, "stale")
        return actions + self._dispatch()

    def ran(self, cid: int, result: dict) -> list:
        ex, self.running = self.running, None
        self.orphan_deaths = 0
        c = self.cells.get(cid)
        if c is None:  # deleted while running
            return ([Delete(ex.defs)] if result["status"] == "ok" and ex.defs else []) + self._dispatch()
        c.output, c.previews = result["output"], result["previews"]
        if result["status"] == "ok":
            c.ran_defs = set(ex.defs)
            c.ran_once = True
            self._set(cid, "ok" if c.code == ex.code else "edited")
        else:
            self._set(cid, result["status"], result["error"])
        return self._dispatch()

    def stop(self) -> list:
        """Stop button: kill the kernel; the running cell ends up `interrupted`."""
        if self.running is None:
            return []
        self.stop_requested = True
        return [Kill()]

    def kernel_died(self, reason: str) -> list:
        """D-013: quarantine the running cell, restart, re-run the rest."""
        ex, self.running = self.running, None
        stopped, self.stop_requested = self.stop_requested, False
        if ex is not None and ex.cid in self.cells:
            c = self.cells[ex.cid]
            if stopped:
                c.quarantine = "interrupted"
                self._set(ex.cid, "interrupted",
                          "interrupted by the stop button (the kernel was restarted); "
                          "run or edit this cell to execute it again")
            else:
                c.quarantine = "crashed"
                self._set(ex.cid, "crashed", self._crash_message(ex.cid, reason))
            self.orphan_deaths = 0
        else:
            self.orphan_deaths += 1
            if self.orphan_deaths > self.MAX_ORPHAN_DEATHS:
                self.kernel_dead = True
                return []
        for c in self.cells.values():
            c.ran_defs = set()  # the kernel's memory is gone
        roots = {cid for cid, c in self.cells.items() if c.ran_once or cid in self.queue}
        return [Restart()] + self._schedule(roots)

    # ---------------- internals ----------------

    def _set(self, cid, status, error=""):
        c = self.cells[cid]
        c.status, c.error = status, error
        self.changed.add(cid)

    def _graph(self):
        definers: dict[str, list[int]] = {}
        for cid, c in self.cells.items():
            for name in c.defs:
                definers.setdefault(name, []).append(cid)
        # Every definer is a parent, so readers of a duplicated name get blocked.
        parents = {cid: {d for n in c.refs for d in definers.get(n, ())} for cid, c in self.cells.items()}
        children = {cid: set() for cid in self.cells}
        for cid, ps in parents.items():
            for p in ps:
                children[p].add(cid)
        dup = {cid for ds in definers.values() if len(ds) > 1 for cid in ds}
        return parents, children, dup

    def _healthy(self, cid) -> bool:
        """A parent whose names are in the executor: ok, or edited after an ok run
        (its old values are still there, as in Jupyter)."""
        c = self.cells[cid]
        return c.status == "ok" or (c.status == "edited" and bool(c.ran_defs))

    def _ancestors(self, cid, parents) -> set:
        seen, todo = set(), list(parents[cid])
        while todo:
            p = todo.pop()
            if p not in seen:
                seen.add(p)
                todo.extend(parents[p])
        return seen

    def _crash_message(self, cid, reason) -> str:
        """Review item 1: the quarantine may blame the wrong cell (memory corrupted
        upstream through unsafe_*). Don't solve it; make it visible."""
        parents, _, _ = self._graph()
        pos = {c: i for i, c in enumerate(self.cells)}
        suspects = []
        for a in sorted(self._ancestors(cid, parents), key=pos.get):
            apis = sorted(set(_UNSAFE.findall(self.cells[a].code)))
            if apis:
                suspects.append(f"[{a}] ({', '.join(apis)})")
        msg = f"the kernel process died ({reason}) while running cell [{cid}]."
        if suspects:
            msg += (" Upstream cells using unsafe_* APIs, which may have corrupted memory "
                    "before this cell ran: " + "; ".join(suspects) + ".")
        else:
            msg += " No upstream cell uses unsafe_* APIs."
        return msg + " This cell is quarantined: edit or run it to try again."

    def _schedule(self, roots: set) -> list:
        _, children, _ = self._graph()
        plan, todo = set(), list(roots)
        while todo:
            cid = todo.pop()
            if cid not in plan:
                plan.add(cid)
                todo.extend(children[cid])
        actions = []
        for cid in sorted(plan, key=list(self.cells).index):
            c = self.cells[cid]
            if c.kind not in GRAPH_KINDS:
                continue
            if c.quarantine:  # never re-run automatically; its readers get blocked
                continue
            if c.syntax_error:
                self.queue.discard(cid)
                actions += self._drop(cid)
                self._set(cid, "syntax-error", c.syntax_error)
                continue
            self.queue.add(cid)
            if self.running is None or self.running.cid != cid:
                self._set(cid, "stale")
            if c.kind == "mojo":
                actions += self._ensure_compiled(cid)
        return actions + self._dispatch()

    def _ensure_compiled(self, cid) -> list:
        c = self.cells[cid]
        if c.kind != "mojo" or c.syntax_error or c.artifact_code == c.code or c.compiling == c.code:
            return []
        actions = [Cancel(cid)] if c.compiling else []
        c.compiling = c.code
        self._set(cid, "compiling")
        return actions + [Compile(cid, c.code)]

    def _drop(self, cid) -> list:
        c = self.cells[cid]
        names, c.ran_defs = sorted(c.ran_defs), set()
        return [Delete(names)] if names else []

    def _resolve(self, cid, status, error) -> list:
        """Take a queued cell out of the queue without running it."""
        self.queue.discard(cid)
        self._set(cid, status, error)
        return self._drop(cid)

    def _dup_msg(self, cid) -> str:
        mine = self.cells[cid].defs
        clash = sorted(n for o, c in self.cells.items() if o != cid for n in c.defs & mine)
        return f"name(s) defined by more than one cell: {', '.join(clash)}"

    def _dispatch(self) -> list:
        """Settle whatever can be settled; start the next runnable cell if the kernel is free."""
        actions = []
        while self.queue and not self.kernel_dead:
            parents, children, dup = self._graph()
            pos = {c: i for i, c in enumerate(self.cells)}
            order, cyclic, downstream = toposort(self.queue, parents, children, pos)
            pending = self.queue | ({self.running.cid} if self.running else set())
            for cid in order:
                c = self.cells[cid]
                if cyclic or downstream:  # settle the whole cycle at once, or it "breaks" midway
                    for x in cyclic:
                        actions += self._resolve(x, "cycle", "cell is part of a dependency cycle")
                    for x in downstream:
                        actions += self._resolve(x, "blocked", "an ancestor cell is in a dependency cycle")
                elif c.syntax_error:  # edited while queued
                    actions += self._resolve(cid, "syntax-error", c.syntax_error)
                elif cid in dup:
                    actions += self._resolve(cid, "multiple-definition", self._dup_msg(cid))
                elif parents[cid] & pending:
                    continue  # an ancestor still has to run (or is running)
                elif bad := sorted(p for p in parents[cid] if not self._healthy(p)):
                    actions += self._resolve(cid, "blocked", "ancestor cell(s) not ok: "
                                             + ", ".join(f"[{p}] {self.cells[p].status}" for p in bad))
                elif self.running is not None:
                    continue  # runnable, but the kernel is busy
                elif c.kind == "mojo" and c.artifact_code != c.code:
                    continue  # still compiling; independent cells may run meanwhile
                else:
                    self.queue.discard(cid)
                    ex = Exec(cid, c.kind, c.code, sorted(c.defs), sorted(c.ran_defs), c.artifact)
                    c.ran_defs, c.defs_at_run = set(), set(c.defs)
                    self.running = ex
                    self._set(cid, "running")
                    return actions + [ex]
                break  # the queue changed: recompute the order
            else:
                return actions  # nothing more can move until an event arrives
        return actions
