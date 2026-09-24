"""Synchronous in-process driver: Scheduler + Executor in one process, Mojo
builds inline. Used by the CLI and the semantic tests. The server (D-012) drives
the same Scheduler asynchronously, with the Executor in a kernel process."""
from collections import deque
from dataclasses import asdict
from pathlib import Path

from . import mojo
from .executor import Executor
from .fmt import Cell
from .scheduler import GRAPH_KINDS, Cancel, Compile, Delete, Exec, Scheduler


class Engine:
    def __init__(self, cache_dir: Path | str = ".nbcache"):
        self.sched = Scheduler()
        self.ex = Executor()
        self.cache_dir = Path(cache_dir)
        self.compiles = 0  # builds that really ran mojo (disk-cache hits excluded)

    @property
    def cells(self):
        return self.sched.cells

    @property
    def ns(self):
        return self.ex.ns

    # Each call returns the ids that ran, in order.

    def add(self, code: str, kind: str = "python") -> tuple[int, list[int]]:
        cid = self.sched.add(code, kind)
        return cid, self._drive(self.sched.run(cid))

    def edit(self, cid: int, code: str) -> list[int]:
        """Edit and run (Shift+Enter)."""
        return self._drive(self.sched.edit(cid, code) + self.sched.run(cid))

    def delete(self, cid: int) -> list[int]:
        return self._drive(self.sched.delete(cid))

    def load(self, cells: list[Cell]) -> list[int]:
        for c in cells:
            if c.kind in GRAPH_KINDS:
                self.sched.add(c.code, c.kind)
        return self.run_all()

    def run_all(self) -> list[int]:
        return self._drive(self.sched.run_all())

    def _drive(self, actions) -> list[int]:
        ran, todo = [], deque(actions)
        while todo:
            a = todo.popleft()
            if isinstance(a, Compile):
                try:
                    artifact, built = mojo.build(a.code, self.cache_dir)
                    self.compiles += built
                    todo.extend(self.sched.compiled(a.cid, a.code, artifact))
                except mojo.CompileError as e:
                    todo.extend(self.sched.compiled(a.cid, a.code, error=str(e)))
            elif isinstance(a, Exec):
                ran.append(a.cid)
                todo.extend(self.sched.ran(a.cid, self.ex.execute(asdict(a))))
            elif isinstance(a, Delete):
                self.ex.delete(a.names)
            elif not isinstance(a, Cancel):  # nothing is ever in flight here
                raise RuntimeError(f"{type(a).__name__} needs the server: the kernel runs in-process here")
        return ran
