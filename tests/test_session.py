"""Server-side driver with a real kernel process (D-012, D-013, stop button)."""
import asyncio
import shutil
import time

import pytest

from kernel.fmt import Cell
from kernel.session import Session

needs_mojo = pytest.mark.skipif(not shutil.which("mojo"), reason="mojo not on PATH (run through pixi)")

SEGV_MOJO = """def run(a: Int, mut c: Int) raises:
    Pointer[Int, MutAnyOrigin](unsafe_from_address=8).unsafe_store(a)"""
GOOD_MOJO = """def run(a: Int, mut c: Int) raises:
    c = a * 10"""
SEGV_PY = "import ctypes as _ct\n_ct.string_at(0)"  # private name: two cells may both use it


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    return tmp_path_factory.mktemp("nbcache")


def session_test(fn):
    """Run an async test body against a started Session; always tear the kernel down."""
    def wrapper(cache, *args, **kwargs):
        async def main():
            s = Session(cache)
            await s.start()
            try:
                await fn(s, *args, **kwargs)
            finally:
                await s.close()
        asyncio.run(main())
    wrapper.__name__ = fn.__name__
    return wrapper


def statuses(s):
    return [c.status for c in s.sched.cells.values()]


def previews(s, cids):
    return {cid: s.sched.cells[cid].previews for cid in cids}


async def wait_running(s, cid, timeout=30):
    t0 = time.monotonic()
    while not (s.sched.running and s.sched.running.cid == cid):
        assert time.monotonic() - t0 < timeout, "cell never started"
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)  # let it get into the loop


@session_test
async def test_python_and_mojo_run_in_the_kernel_process(s):
    s.load([Cell("python", "import numpy as np\nxs = np.arange(5, dtype=np.float64)"),
            Cell("mojo", "def run(xs: ArrayIn[DType.float64], mut t: Float64) raises:\n"
                         "    for i in range(len(xs)):\n        t += xs[i]"),
            Cell("python", "u = t * 2\nprint('u =', u)")])
    await s.idle()
    assert statuses(s) == ["ok", "ok", "ok"]
    x, m, u = s.sched.cells
    assert s.sched.cells[x].previews["xs"] == {"type": "ndarray", "shape": [5], "dtype": "float64",
                                               "head": [0.0, 1.0, 2.0, 3.0, 4.0]}
    assert s.sched.cells[u].previews["u"]["repr"] == "20.0" and s.sched.cells[u].output == "u = 20.0\n"
    assert s.restarts == 0


@needs_mojo
@session_test
async def test_segfault_recovery_restores_the_rest_without_crash_loop(s):  # D-013, mandatory
    s.load([Cell("python", "a = 21"),                 # A
            Cell("python", "b = [a, a + 1]"),          # B reads A
            Cell("mojo", GOOD_MOJO),                   # C reads A (will segfault)
            Cell("python", "d = c + 1"),               # D reads C
            Cell("python", "e = 'independent'")])      # E
    await s.idle()
    A, B, C, D, E = s.sched.cells
    assert statuses(s) == ["ok"] * 5
    before = previews(s, [A, B, E])

    s.edit(C, SEGV_MOJO)
    s.run(C)
    await s.idle()
    await asyncio.sleep(0.5)  # a crash loop would show up as more restarts here
    await s.idle()
    assert s.restarts == 1
    assert statuses(s) == ["ok", "ok", "crashed", "blocked", "ok"]
    assert "SIGSEGV" in s.sched.cells[C].error and "cell [3]" in s.sched.cells[C].error
    assert previews(s, [A, B, E]) == before     # the rest is back to its previous state

    s.edit(C, GOOD_MOJO)
    s.run(C)
    await s.idle()
    assert statuses(s) == ["ok"] * 5 and s.restarts == 1
    assert s.sched.cells[D].previews["d"]["repr"] == "211"


@session_test
async def test_two_crashing_cells_restart_twice_and_settle(s):
    s.load([Cell("python", "a = 1"), Cell("python", SEGV_PY + "\nx = a"), Cell("python", SEGV_PY + "\ny = a"),
            Cell("python", "z = a + 1")])
    await s.idle()
    await asyncio.sleep(0.5)
    await s.idle()
    assert s.restarts == 2
    assert statuses(s) == ["ok", "crashed", "crashed", "ok"]


@needs_mojo
@session_test
async def test_crash_in_innocent_cell_lists_upstream_unsafe_cells(s):  # review item 1
    s.load([Cell("python", "import numpy as np\nxs = np.ones(4)"),
            Cell("mojo", "def run(xs: ArrayIn[DType.float64], mut ys: ArrayOut[DType.float64]) raises:\n"
                         "    ys.alloc(len(xs))\n"
                         "    for i in range(len(xs)):\n        ys.unsafe_set(i, xs.unsafe_get(i) * 2.0)"),
            Cell("python", "total = float(xs.sum())"),            # upstream, no unsafe_*
            Cell("python", "w = float(ys[0])\n" + SEGV_PY)])       # the innocent one that dies
    await s.idle()
    X, U, T, V = s.sched.cells
    msg = s.sched.cells[V].error
    assert s.sched.cells[V].status == "crashed" and "while running cell [4]" in msg
    assert f"[{U}] (unsafe_get, unsafe_set)" in msg
    assert f"[{X}]" not in msg and f"[{T}]" not in msg
    assert s.sched.cells[U].status == "ok" and s.restarts == 1


@session_test
async def test_stop_button_interrupts_python_infinite_loop(s):
    s.load([Cell("python", "a = 1"), Cell("python", "e = 'kept'")])
    await s.idle()
    A, E = s.sched.cells
    loop = s.add("while True:\n    pass\nb = a")
    reader = s.add("c = b")
    s.run(loop)
    await wait_running(s, loop)
    t0 = time.perf_counter()
    s.stop()
    await s.idle()
    recovery = time.perf_counter() - t0
    cells = s.sched.cells
    assert cells[loop].status == "interrupted" and "stop button" in cells[loop].error
    assert "died" not in cells[loop].error                  # distinct from a crash
    assert cells[reader].status == "blocked"
    assert cells[A].status == cells[E].status == "ok" and s.restarts == 1
    assert recovery < 5, recovery

    s.run(loop)                                              # explicit re-run lifts the quarantine
    await wait_running(s, loop)
    s.stop()
    await s.idle()
    assert cells[loop].status == "interrupted" and s.restarts == 2

    s.edit(loop, "b = a + 1")                                # editing lifts it too
    s.run(loop)
    await s.idle()
    assert cells[loop].status == "ok" and cells[reader].previews["c"]["repr"] == "2"


@needs_mojo
@session_test
async def test_stop_button_interrupts_mojo_infinite_loop(s):
    s.load([Cell("python", "a = 1")])
    await s.idle()
    loop = s.add("def run(a: Int, mut n: Int) raises:\n    while n >= 0:\n        n = (n + a) % 7", "mojo")
    s.run(loop)
    await wait_running(s, loop, timeout=60)  # includes the build
    s.stop()
    await s.idle()
    assert s.sched.cells[loop].status == "interrupted" and s.restarts == 1
    assert s.sched.cells[next(iter(s.sched.cells))].status == "ok"


@session_test
async def test_stop_with_unread_messages_in_the_kernel_socket(s):
    """Regression: a kernel killed with unread input answers with RST, not EOF.
    The reader saw ConnectionResetError, died silently, and the session hung."""
    s.load([Cell("python", "a = 1"), Cell("python", "gone = 2")])
    await s.idle()
    A, G = s.sched.cells
    loop = s.add("while True:\n    pass")
    s.run(loop)
    await wait_running(s, loop)
    s.delete(G)          # a Delete the looping kernel will never read
    await asyncio.sleep(0.2)
    s.stop()
    await s.idle(timeout=15)
    assert s.sched.cells[loop].status == "interrupted" and s.restarts == 1
    assert s.sched.cells[A].status == "ok"
