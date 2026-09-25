"""Asynchronous, cancellable, parallel Mojo builds (D-014). Every source carries a
unique nonce so no build is short-circuited by the compiler's global cache."""
import asyncio
import os
import shutil
import subprocess
import time
import uuid

import pytest

from kernel import mojo
from kernel.fmt import Cell
from kernel.session import Session

pytestmark = pytest.mark.skipif(not shutil.which("mojo"), reason="mojo not on PATH (run through pixi)")


def cell(body="    t = a * 2", out="mut t: Int"):
    return f"# nonce {uuid.uuid4().hex}\ndef run(a: Int, {out}) raises:\n{body}"


def published(cache):
    return sorted(p.name for p in cache.iterdir() if p.suffix in (".so", ".tmp"))


def mojo_builds_running(tag):
    r = subprocess.run(["pgrep", "-f", f"mojo build.*{tag}"], capture_output=True, text=True)
    return r.stdout.split()


def test_killed_build_publishes_nothing(tmp_path):  # review adjustment 2
    code = cell()

    async def main():
        task = asyncio.create_task(mojo.build_async(code, tmp_path))
        await asyncio.sleep(0.6)  # well inside the ~1.5 s build
        assert mojo_builds_running(tmp_path.name), "build should be running"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert published(tmp_path) == []                   # no .so, no partial .tmp
    assert not mojo_builds_running(tmp_path.name)      # the whole process group is gone
    art, built = mojo.build(code, tmp_path)            # and the key still builds fine afterwards
    assert built and published(tmp_path) == [f"{mojo.cache_key(code)}.so"]


def session_run(fn):
    def wrapper(tmp_path):
        async def main():
            s = Session(tmp_path)
            await s.start()
            try:
                await fn(s, tmp_path)
            finally:
                await s.close()
        asyncio.run(main())
    wrapper.__name__ = fn.__name__
    return wrapper


@session_run
async def test_compiling_status_and_stale_descendants(s, cache):
    s.load([Cell("python", "a = 4"), Cell("python", "q = 'free'")])
    await s.idle()
    a, q = s.sched.cells
    m = s.add(cell(), "mojo")
    r = s.add("u = t + 1")
    s.run(m)
    await asyncio.sleep(0.3)
    assert s.sched.cells[m].status == "compiling" and s.sched.cells[r].status == "stale"
    s.edit(q, "q = 'still interactive'")
    s.run(q)                                   # the kernel is free while Mojo compiles
    await asyncio.sleep(0.3)
    assert s.sched.cells[q].status == "ok" and s.sched.cells[m].status == "compiling"
    await s.idle()
    assert s.sched.cells[r].previews["u"]["repr"] == "9"


@session_run
async def test_new_edit_kills_the_build_in_progress(s, cache):
    s.load([Cell("python", "a = 4")])
    await s.idle()
    first, second = cell("    t = a * 2"), cell("    t = a * 3")
    m = s.add(first, "mojo")
    s.run(m)
    await asyncio.sleep(0.5)
    assert s.sched.cells[m].status == "compiling"
    s.edit(m, second)                          # a new edit cancels (kills) the build
    s.run(m)
    await s.idle()
    events = [e for _, e, cid in s.log if cid == m]
    assert "build-killed" in events
    assert s.sched.cells[m].previews["t"]["repr"] == "12"                   # second version ran
    assert not (cache / f"{mojo.cache_key(first)}.so").exists()           # first never published
    assert (cache / f"{mojo.cache_key(second)}.so").exists()
    assert s.compiles == 1


@session_run
async def test_independent_cells_compile_in_parallel(s, cache):
    s.load([Cell("python", "a = 4"),
            Cell("mojo", cell(out="mut t1: Int", body="    t1 = a")),
            Cell("mojo", cell(out="mut t2: Int", body="    t2 = a")),
            Cell("mojo", cell(out="mut t3: Int", body="    t3 = a"))])
    await s.idle()
    assert [c.status for c in s.sched.cells.values()] == ["ok"] * 4 and s.compiles == 3
    spans = {}
    for t, ev, cid in s.log:
        if ev in ("build-start", "build-end"):
            spans.setdefault(cid, []).append(t)
    (s1, e1), (s2, e2), (s3, e3) = spans.values()
    assert max(s1, s2, s3) < min(e1, e2, e3)   # all three were building at the same time


# ---------------- speculative builds (review item 7) ----------------

@session_run
async def test_speculative_build_is_debounced_and_reused_by_run(s, cache):
    assert s.speculate_debounce == 0.3                  # review: 300 ms default
    s.load([Cell("python", "a = 4")])
    await s.idle()
    m = s.add(cell(), "mojo")
    for _ in range(3):                                  # typing: three edits 0.1 s apart
        code = cell("    t = a * 5")
        s.edit(m, code)
        await asyncio.sleep(0.1)
    await s.idle()                                      # debounce fires once, build runs
    assert s.compiles == 1 and s.sched.cells[m].status == "idle"
    assert s.sched.cells[m].artifact_code == code and s.sched.cells[m].diagnostics == []
    s.run(m)
    await s.idle()
    assert s.sched.cells[m].previews["t"]["repr"] == "20" and s.compiles == 1   # no rebuild


@session_run
async def test_speculative_error_arrives_as_diagnostics_not_as_result(s, cache):
    s.speculate_debounce = 0.1
    s.load([Cell("python", "a = 4")])
    await s.idle()
    m = s.add(cell(), "mojo")
    s.edit(m, cell("    t = a * 2\n    t += undefined_name"))
    await s.idle()
    c = s.sched.cells[m]
    assert c.status == "idle" and c.error == ""
    assert [d["line"] for d in c.diagnostics] == [4] and c.diagnostics[0]["col"] > 1
    assert "undefined_name" in c.diagnostics[0]["message"]


@session_run
async def test_edit_during_speculative_build_kills_it(s, cache):
    s.speculate_debounce = 0.05
    s.load([Cell("python", "a = 4")])
    await s.idle()
    m = s.add(cell(), "mojo")
    s.edit(m, cell())
    await asyncio.sleep(0.6)                            # first speculative build is running
    assert s.sched.cells[m].compiling is not None
    final = cell("    t = a * 7")
    s.edit(m, final)
    await s.idle()
    assert [e for _, e, cid in s.log if cid == m].count("build-killed") == 1
    assert s.sched.cells[m].artifact_code == final and s.compiles == 1


@session_run
async def test_run_while_speculative_build_in_flight_reuses_it(s, cache):
    s.speculate_debounce = 0.05
    s.load([Cell("python", "a = 4")])
    await s.idle()
    m = s.add(cell(), "mojo")
    s.edit(m, cell("    t = a * 3"))
    await asyncio.sleep(0.4)
    s.run(m)
    assert s.sched.cells[m].status == "compiling"
    await s.idle()
    assert s.sched.cells[m].previews["t"]["repr"] == "12" and s.compiles == 1
    assert [e for _, e, cid in s.log if cid == m].count("build-start") == 1


# ---------------- niced, preemptible speculative builds (review) ----------------

def mojo_nice_values(tag):
    """Nice value of every running `mojo build` whose command line mentions `tag`."""
    out = []
    for pid in mojo_builds_running(tag):
        try:
            fields = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
            out.append(int(fields[16]))  # field 19 of stat: nice
        except (FileNotFoundError, ProcessLookupError):
            pass
    return out


def events(s, cid):
    return {e: t for t, e, c in s.log if c == cid}


def session_slots(n, debounce=0.05):
    def deco(fn):
        def wrapper(tmp_path):
            async def main():
                s = Session(tmp_path, max_builds=n, speculate_debounce=debounce)
                await s.start()
                try:
                    await fn(s, tmp_path)
                finally:
                    await s.close()
            asyncio.run(main())
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


@session_slots(1)
async def test_speculative_build_does_not_delay_explicit_run_beyond_its_own_build(s, cache):
    s.load([Cell("python", "a = 4")])
    await s.idle()
    A = s.add(cell(out="mut ta: Int", body="    ta = a"), "mojo")
    B = s.add(cell(out="mut tb: Int", body="    tb = a * 3"), "mojo")
    s.edit(A, cell(out="mut ta: Int", body="    ta = a + 1"))   # A starts building speculatively
    await asyncio.sleep(0.5)
    assert "build-start" in events(s, A) and s.sched.cells[A].compiling
    base = os.nice(0)  # the server's priority (a daemon like ananicy-cpp may shift it, e.g. -4)
    assert all(n > base for n in mojo_nice_values(cache.name)) and mojo_nice_values(cache.name)  # lower priority
    t0 = time.perf_counter()
    s.run(B)                                                        # Shift+Enter on B, the only slot is busy
    while not s.sched.cells[B].previews:
        await asyncio.sleep(0.005)
    latency = time.perf_counter() - t0
    eb, ea = events(s, B), events(s, A)
    assert "build-preempted" in ea                                  # A was killed to free the slot
    assert eb["build-start"] - t0 < 0.3                             # B started right away
    build_b = eb["build-end"] - eb["build-start"]
    assert latency < build_b + 0.3, (latency, build_b)              # not delayed beyond B's own build
    assert s.sched.cells[B].previews["tb"]["repr"] == "12"
    await s.idle()                                                  # A is rebuilt once the slot frees
    assert s.sched.cells[A].artifact_code == s.sched.cells[A].code


@session_slots(1)
async def test_explicit_build_is_not_niced(s, cache):
    s.load([Cell("python", "a = 4")])
    await s.idle()
    m = s.add(cell(), "mojo")
    s.run(m)
    await asyncio.sleep(0.5)
    assert set(mojo_nice_values(cache.name)) == {os.nice(0)}        # same priority as the server
    await s.idle()


@session_slots(1)
async def test_promoted_build_is_not_preempted(s, cache):
    s.load([Cell("python", "a = 4")])
    await s.idle()
    A = s.add(cell(out="mut ta: Int", body="    ta = a"), "mojo")
    B = s.add(cell(out="mut tb: Int", body="    tb = a"), "mojo")
    s.edit(A, cell(out="mut ta: Int", body="    ta = a + 2"))
    await asyncio.sleep(0.4)
    s.run(A)                    # A's speculative build is now needed: promoted
    await asyncio.sleep(0.05)
    s.run(B)                    # B must wait for the slot, not kill A
    await s.idle()
    ea, eb = events(s, A), events(s, B)
    assert "build-promoted" in ea and "build-preempted" not in ea
    assert eb["build-start"] >= ea["build-end"]
    assert s.sched.cells[A].previews["ta"]["repr"] == "6" and s.sched.cells[B].previews["tb"]["repr"] == "4"


@session_slots(1)
async def test_speculative_does_not_preempt_speculative(s, cache):
    s.load([Cell("python", "a = 4")])
    await s.idle()
    A = s.add(cell(out="mut ta: Int", body="    ta = a"), "mojo")
    B = s.add(cell(out="mut tb: Int", body="    tb = a"), "mojo")
    s.edit(A, cell(out="mut ta: Int", body="    ta = a + 1"))
    await asyncio.sleep(0.4)
    s.edit(B, cell(out="mut tb: Int", body="    tb = a + 1"))  # B's speculative build waits its turn
    await s.idle()
    ea, eb = events(s, A), events(s, B)
    assert "build-preempted" not in ea and eb["build-start"] >= ea["build-end"]
