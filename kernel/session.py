"""Session: the server-side asynchronous driver (D-012, D-013, D-014).

Drives the pure Scheduler with the Executor in a separate kernel process.
Every scheduler action goes through one worker that applies them in order, so
a Restart always completes before the next Exec is written. Mojo builds run as
tasks in parallel (bounded) and can be cancelled.
"""
import asyncio
import json
import os
import signal
import socket
import sys
import time
from dataclasses import asdict
from pathlib import Path

from . import mojo
from .fmt import Cell
from .scheduler import Cancel, Compile, Delete, Exec, Kill, Restart, Scheduler

ROOT = Path(__file__).resolve().parent.parent


def _reason(returncode: int) -> str:
    if returncode is not None and returncode < 0:
        try:
            return signal.Signals(-returncode).name
        except ValueError:
            pass
    return f"exit code {returncode}"


class _Kernel:
    """One kernel process generation."""

    def __init__(self, proc, reader, writer, generation):
        self.proc, self.reader, self.writer, self.generation = proc, reader, writer, generation

    async def send(self, msg: dict):
        try:
            self.writer.write(json.dumps(msg).encode() + b"\n")
            await self.writer.drain()
        except (ConnectionError, OSError):
            pass  # it died; its reader reports the death


class Session:
    def __init__(self, cache_dir: Path | str, max_builds: int | None = None, core_dumps: bool = False,
                 speculate_debounce: float = 0.3):
        self.sched = Scheduler()
        self.core_dumps = core_dumps  # debug: let a crashing kernel write a core dump
        self.cache_dir = Path(cache_dir)
        self.max_builds = max_builds or max(1, (os.cpu_count() or 1) // 4)  # review: nproc // 4
        self.restarts = 0
        self.compiles = 0
        self.log: list[tuple[float, str, object]] = []  # (time, event, detail) for tests/benchmarks
        self.listeners = []                               # fn(changed: dict[cid, CellState]) per step
        self._sem = asyncio.Semaphore(self.max_builds)
        self._builds: dict[int, asyncio.Task] = {}
        self._actions: asyncio.Queue = asyncio.Queue()
        self._idle = asyncio.Event()
        self._kernel: _Kernel | None = None
        self._generation = 0
        self._tasks: list[asyncio.Task] = []
        self.speculate_debounce = speculate_debounce  # background Mojo build after this idle time (s)
        self._spec_timers: dict[int, asyncio.TimerHandle] = {}

    # ---------------- lifecycle ----------------

    async def start(self):
        await self._start_kernel()
        self._tasks.append(asyncio.create_task(self._worker()))
        self._after()

    async def close(self):
        for h in self._spec_timers.values():
            h.cancel()
        for t in list(self._builds.values()) + self._tasks:
            t.cancel()
        await asyncio.gather(*self._builds.values(), *self._tasks, return_exceptions=True)
        if self._kernel and self._kernel.proc.returncode is None:
            self._kernel.proc.kill()
            await self._kernel.proc.wait()

    async def idle(self, timeout: float = 120):
        """Wait until nothing is running, building or queued."""
        await asyncio.sleep(0)
        await asyncio.wait_for(self._idle.wait(), timeout)

    # ---------------- user events ----------------

    def load(self, cells: list[Cell]):
        """Every cell kind is kept (the server owns the file); only python/mojo are scheduled."""
        for c in cells:
            self.sched.add(c.code, c.kind)
        self._push(self.sched.run_all())

    def to_file_cells(self) -> list[Cell]:
        return [Cell(c.kind, c.code) for c in self.sched.cells.values()]

    def add(self, code: str, kind: str = "python", after: int | None = None) -> int:
        cid = self.sched.add(code, kind, after)
        self._after()
        return cid

    def edit(self, cid: int, code: str):
        self._push(self.sched.edit(cid, code))
        if self.sched.cells[cid].kind == "mojo":  # debounce: every edit restarts the timer
            old = self._spec_timers.pop(cid, None)
            if old:
                old.cancel()
            self._spec_timers[cid] = asyncio.get_running_loop().call_later(
                self.speculate_debounce, self._speculate, cid)
            self._after()

    def _speculate(self, cid: int):
        self._spec_timers.pop(cid, None)
        if cid in self.sched.cells:
            self._push(self.sched.speculate(cid))
        else:
            self._after()

    def run(self, cid: int):
        self._push(self.sched.run(cid))

    def run_all(self):
        self._push(self.sched.run_all())

    def delete(self, cid: int):
        self._push(self.sched.delete(cid))

    def stop(self):
        self._push(self.sched.stop())

    # ---------------- internals ----------------

    def _push(self, actions):
        for a in actions:
            self._actions.put_nowait(a)
        self._after()

    def _after(self):
        if self.sched.changed:
            changed = {cid: self.sched.cells[cid] for cid in self.sched.changed if cid in self.sched.cells}
            self.sched.changed.clear()
            for fn in self.listeners:
                fn(changed)
        busy = (self.sched.running is not None or self._builds or self._spec_timers or not self._actions.empty()
                or (self.sched.queue and not self.sched.kernel_dead))
        self._idle.clear() if busy else self._idle.set()

    async def _worker(self):
        while True:
            a = await self._actions.get()
            try:
                await self._apply(a)
            finally:
                self._after()

    async def _apply(self, a):
        if isinstance(a, Compile):
            old = self._builds.pop(a.cid, None)
            if old:
                old.cancel()
            self._builds[a.cid] = asyncio.create_task(self._build(a.cid, a.code))
        elif isinstance(a, Cancel):
            t = self._builds.pop(a.cid, None)
            if t:
                t.cancel()
                self.log.append((time.perf_counter(), "cancel", a.cid))
        elif isinstance(a, Exec):
            self.log.append((time.perf_counter(), "exec", a.cid))
            await self._kernel.send({"op": "exec", **asdict(a)})
        elif isinstance(a, Delete):
            await self._kernel.send({"op": "delete", "names": a.names})
        elif isinstance(a, Restart):
            await self._start_kernel()
            self.restarts += 1
            self.log.append((time.perf_counter(), "restart", self.restarts))
        elif isinstance(a, Kill):
            if self._kernel.proc.returncode is None:
                self._kernel.proc.kill()

    async def _build(self, cid: int, code: str):
        me = asyncio.current_task()
        try:
            async with self._sem:
                self.log.append((time.perf_counter(), "build-start", cid))
                try:
                    art, built = await mojo.build_async(code, self.cache_dir)
                    self.compiles += built
                    result = {"artifact": asdict(art)}
                except mojo.CompileError as e:
                    result = {"error": str(e), "diagnostics": e.diagnostics}
                self.log.append((time.perf_counter(), "build-end", cid))
        except asyncio.CancelledError:
            self.log.append((time.perf_counter(), "build-killed", cid))
            return
        finally:
            if self._builds.get(cid) is me:
                del self._builds[cid]
        self._push(self.sched.compiled(cid, code, **result))

    async def _start_kernel(self):
        self._generation += 1
        parent, child = socket.socketpair()
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")]))}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "kernel.kernelproc", str(child.fileno()),
            *(["--core-dumps"] if self.core_dumps else []),
            pass_fds=[child.fileno()], env=env)
        child.close()
        reader, writer = await asyncio.open_connection(sock=parent)
        self._kernel = _Kernel(proc, reader, writer, self._generation)
        self._tasks.append(asyncio.create_task(self._read(self._kernel)))

    async def _read(self, k: _Kernel):
        try:
            while True:
                line = await k.reader.readline()
                if not line:
                    break
                if self.sched.running is not None:
                    cid = self.sched.running.cid
                    self.log.append((time.perf_counter(), "ran", cid))
                    self._push(self.sched.ran(cid, json.loads(line)))
        except (ConnectionError, OSError):
            pass  # killed with unread input -> RST instead of EOF: the same death
        rc = await k.proc.wait()
        k.writer.close()
        if k.generation == self._generation:  # an old generation never reports twice
            self.log.append((time.perf_counter(), "died", _reason(rc)))
            self._push(self.sched.kernel_died(_reason(rc)))
