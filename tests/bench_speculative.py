"""Speculative Mojo builds (review item 7): Shift+Enter -> result latency as a function
of the pause after the last keystroke. Real 800 ms debounce, nonce per source.
`PYTHONPATH=. pixi run python tests/bench_speculative.py`"""
import asyncio
import statistics
import tempfile
import time
import uuid
from pathlib import Path

from kernel.fmt import Cell
from kernel.session import Session


def code():
    return f"# nonce {uuid.uuid4().hex}\ndef run(a: Int, mut t: Int) raises:\n    t = a * 2"


async def main():
    s = Session(Path(tempfile.mkdtemp()))
    await s.start()
    s.load([Cell("python", "a = 21")])
    await s.idle()
    m = s.add(code(), "mojo")
    for pause in (0.0, 1.0, 2.0, 3.0):
        ts = []
        for _ in range(3):
            s.edit(m, code())                  # last keystroke
            await asyncio.sleep(pause)
            t = time.perf_counter()
            s.run(m)                           # Shift+Enter
            while s.sched.cells[m].status != "ok":
                await asyncio.sleep(0.002)
            ts.append(time.perf_counter() - t)
            await s.idle()
        print(f"pause {pause:.0f} s -> Shift+Enter to result: median {statistics.median(ts):.3f} s "
              f"(min {min(ts):.3f}, max {max(ts):.3f})")
    await s.close()


asyncio.run(main())
