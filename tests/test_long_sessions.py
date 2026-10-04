"""Long sessions (D-026): restart button, restart indicator, build cache limit and
recovery from a build that vanished from the cache."""
import asyncio
import os
import time
import uuid

from kernel import mojo
from kernel.fmt import Cell
from kernel.scheduler import Restart, Scheduler
from kernel.session import Session
from tests.test_session import cache, needs_mojo, session_test, statuses, wait_running  # noqa: F401 (fixture)


@session_test
async def test_restart_reruns_what_had_run_and_shows_it_while_restarting(s):
    seen = []
    s.listeners.append(lambda changed: seen.append(s.restarting))
    s.load([Cell("python", "x = 2"), Cell("python", "y = x * 10")])
    await s.idle()
    s.restart()
    await s.idle()
    assert s.restarts == 1 and statuses(s) == ["ok", "ok"]
    assert s.sched.cells[2].previews["y"]["repr"] == "20"  # recomputed in the new kernel
    assert s.sched.kernel_event["kind"] == "restarted" and s.sched.kernel_event["cid"] is None
    assert True in seen and not s.restarting


@session_test
async def test_restart_interrupts_a_running_cell_instead_of_rerunning_it(s):
    s.load([Cell("python", "x = 1"), Cell("python", "while True:\n    pass")])
    await wait_running(s, 2)
    s.restart()
    await s.idle()
    assert statuses(s) == ["ok", "interrupted"]  # a hung cell must not run again on its own
    assert s.sched.kernel_event["kind"] == "interrupted" and s.restarts == 1


def test_restart_revives_a_dead_kernel():
    sched = Scheduler()
    sched.kernel_dead = True  # too many deaths with no cell to blame (D-013): no process left to kill
    actions = sched.restart()
    assert isinstance(actions[0], Restart) and not sched.kernel_dead
    assert sched.kernel_event["kind"] == "restarted"


def _file(path, size, age):
    path.write_bytes(b"x" * size)
    t = time.time() - age
    os.utime(path, (t, t))


def test_evict_drops_least_recently_used_builds_but_never_live_or_young_ones(tmp_path):
    _file(tmp_path / "live.so", 100, 2000)          # oldest, but a cell uses it
    _file(tmp_path / "live.mojo", 10, 2000)
    _file(tmp_path / "old1.so", 100, 1000)
    _file(tmp_path / "old1.mojo", 10, 1000)
    _file(tmp_path / "old2.so", 100, 500)
    _file(tmp_path / "old2.mojo", 10, 500)
    _file(tmp_path / "fresh.mojo", 10, 5)            # a build in flight: source + temp output
    _file(tmp_path / "fresh.0a1b.tmp", 50, 5)
    _file(tmp_path / "dead.mojo", 10, 7200)          # source of a build that failed long ago
    freed = mojo.evict(tmp_path, 250, keep={str((tmp_path / "live.so").resolve())})
    assert freed == 230
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fresh.0a1b.tmp", "fresh.mojo", "live.mojo", "live.so"]
    assert mojo.evict(tmp_path, 10_000, keep=set()) == 0  # under the limit: nothing to do


@needs_mojo
def test_old_builds_are_evicted_once_a_new_one_replaces_them(tmp_path):
    v1 = f"# {uuid.uuid4().hex}\ndef run(mut n: Int) raises:\n    n = 1"
    v2 = v1.replace("n = 1", "n = 2")

    async def main():
        s = Session(tmp_path, cache_limit=1)
        await s.start()
        try:
            s.load([Cell("mojo", v1)])
            await s.idle()
            s.edit(1, v2)
            s.run(1)
            await s.idle()
            assert s.sched.cells[1].previews["n"]["repr"] == "2"
        finally:
            await s.close()
    asyncio.run(main())
    assert [p.name for p in tmp_path.glob("*.so")] == [f"{mojo.cache_key(v2)}.so"]


@needs_mojo
@session_test
async def test_a_build_missing_from_the_cache_is_rebuilt_instead_of_crashing(s):
    s.load([Cell("python", "a = 3"), Cell("mojo", f"# {uuid.uuid4().hex}\ndef run(a: Int, mut b: Int) raises:\n    b = a * 2")])
    await s.idle()
    for f in s.cache_dir.iterdir():  # someone cleaned .nbcache while the server ran
        f.unlink()
    s.restart()  # the new kernel has to dlopen the build again
    await s.idle()
    assert statuses(s) == ["ok", "ok"] and s.sched.cells[2].previews["b"]["repr"] == "6"
