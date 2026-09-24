"""Phase 3 backend measurements (not a test): `PYTHONPATH=. pixi run python tests/bench_phase3.py`.
Every compiled source carries a unique nonce (the compiler's global cache keys on raw text)."""
import asyncio
import statistics
import tempfile
import time
import uuid
from pathlib import Path

import aiohttp

from kernel.fmt import Cell
from kernel.session import Session
from kernel.web import NotebookServer


def nonce_cell(out="t"):
    return f"# nonce {uuid.uuid4().hex}\ndef run(a: Int, mut {out}: Int) raises:\n    {out} = a * 2"


async def timed_run(s, cid):
    t = time.perf_counter()
    s.run(cid)
    await s.idle()
    return time.perf_counter() - t


async def main():
    cache = Path(tempfile.mkdtemp())
    s = Session(cache)
    t = time.perf_counter()
    await s.start()
    s.load([Cell("python", "a = 1")])
    await s.idle()
    print(f"kernel boot + first exec:                       {(time.perf_counter() - t) * 1e3:7.1f} ms")
    (a,) = s.sched.cells
    ts = [await timed_run(s, a) for _ in range(50)]
    print(f"Python run -> result (Session, kernel process): median {statistics.median(ts) * 1e3:6.2f} ms, "
          f"p95 {sorted(ts)[47] * 1e3:6.2f} ms")

    m = s.add(nonce_cell(), "mojo")
    ts = []
    for _ in range(3):
        s.edit(m, nonce_cell())
        ts.append(await timed_run(s, m))
    print(f"Mojo edit+run -> result (compiles):             min {min(ts):.2f} s, max {max(ts):.2f} s")
    ts = [await timed_run(s, m) for _ in range(20)]
    print(f"Mojo re-run, already built:                     median {statistics.median(ts) * 1e3:6.2f} ms")

    # crash -> recovered: numpy cell + 10 trivial cells + one Mojo cell (cached) + the segfaulting cell
    s2 = Session(cache)
    await s2.start()
    s2.load([Cell("python", "import numpy as np\nxs = np.ones(1000)")]
            + [Cell("python", f"v{i} = {i} + len(xs)") for i in range(10)]
            + [Cell("mojo", s.sched.cells[m].code)])
    await s2.idle()
    bad = s2.add("import ctypes as _ct\n_ct.string_at(0)")
    t = time.perf_counter()
    s2.run(bad)
    await s2.idle()
    print(f"crash -> recovered (12 cells re-run, no rebuild): {(time.perf_counter() - t) * 1e3:7.1f} ms "
          f"(restarts={s2.restarts}, compiles in recovery={s2.compiles})")

    loop = s2.add("while True:\n    pass")
    s2.run(loop)
    while not (s2.sched.running and s2.sched.running.cid == loop):
        await asyncio.sleep(0.01)
    t = time.perf_counter()
    s2.stop()
    await s2.idle()
    print(f"stop -> recovered (same notebook):              {(time.perf_counter() - t) * 1e3:7.1f} ms")
    await s2.close()

    # cancel latency: Cancel -> the new build starts
    first = s.add(nonce_cell("c1"), "mojo")
    s.run(first)
    await asyncio.sleep(0.5)
    s.edit(first, nonce_cell("c1"))
    s.run(first)
    await s.idle()
    ev = [(t, e) for t, e, cid in s.log if cid == first]
    t_cancel = next(t for t, e in ev if e == "cancel")
    t_restart = next(t for t, e in ev if e == "build-start" and t > t_cancel)
    print(f"cancel -> new build started:                    {(t_restart - t_cancel) * 1e3:7.2f} ms")
    await s.close()

    # parallel builds: 4 independent cells, max_builds=4 vs 1
    for n in (1, 4):
        p = Session(Path(tempfile.mkdtemp()), max_builds=n)
        await p.start()
        p.load([Cell("python", "a = 1")] + [Cell("mojo", nonce_cell(f"o{i}")) for i in range(4)])
        t = time.perf_counter()
        await p.idle()
        print(f"4 independent Mojo cells, max_builds={n}:          {time.perf_counter() - t:5.2f} s")
        await p.close()

    # WebSocket round trip: run -> update with the result
    d = Path(tempfile.mkdtemp())
    (d / "n.nb.md").write_text("```python\na = 1\n```\n")
    srv = NotebookServer(d / "n.nb.md")
    await srv.start()
    await srv.session.idle()
    async with aiohttp.ClientSession() as http:
        ws = await http.ws_connect(f"http://127.0.0.1:{srv.port}/ws?token={srv.token}",
                                   headers={"Origin": f"http://127.0.0.1:{srv.port}"})
        cid = (await ws.receive_json())["cells"][0]["id"]
        ts = []
        for _ in range(50):
            t = time.perf_counter()
            await ws.send_json({"type": "run", "cid": cid})
            while True:
                msg = await ws.receive_json()
                if any(c["id"] == cid and c["status"] == "ok" for c in msg.get("cells", [])):
                    break
            ts.append(time.perf_counter() - t)
        await ws.close()
    print(f"WebSocket run -> 'ok' update (browser path):    median {statistics.median(ts) * 1e3:6.2f} ms, "
          f"p95 {sorted(ts)[47] * 1e3:6.2f} ms")
    await srv.close()


asyncio.run(main())
