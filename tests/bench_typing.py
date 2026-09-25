"""Speculative-build cost during a simulated typing session (review, item 2).
    PYTHONPATH=. pixi run python tests/bench_typing.py predict   # model only, no mojo
    PYTHONPATH=. pixi run python tests/bench_typing.py measure   # real Session, ~2.5 min
The same seeded trace is used for both debounce values."""
import asyncio
import math
import random
import sys
import tempfile
import time
import uuid
from pathlib import Path

BUILD_S = 1.4  # measured single-cell build time (PHASE3.md)


def trace(seed=42, total=60.0):
    """Gaps (s) between consecutive keystrokes: bursts of 3-15 keys 80-200 ms apart,
    separated by pauses log-uniform in [0.1, 2] s."""
    r, gaps, t = random.Random(seed), [], 0.0
    while t < total:
        for _ in range(r.randint(3, 15) - 1):
            g = r.uniform(0.08, 0.20)
            gaps.append(g)
            t += g
        g = math.exp(r.uniform(math.log(0.1), math.log(2.0)))
        gaps.append(g)
        t += g
    return gaps


def predict(gaps, debounce):
    started = sum(g > debounce for g in gaps)
    killed = sum(debounce < g < debounce + BUILD_S for g in gaps[:-1])  # last one completes
    wasted = sum(g - debounce for g in gaps[:-1] if debounce < g < debounce + BUILD_S)
    return started, killed, wasted


async def measure(gaps, debounce):
    from kernel.fmt import Cell
    from kernel.session import Session
    s = Session(Path(tempfile.mkdtemp()), speculate_debounce=debounce)
    await s.start()
    s.load([Cell("python", "a = 1")])
    await s.idle()
    tag = uuid.uuid4().hex
    m = s.add(f"# {tag}\ndef run(a: Int, mut t: Int) raises:\n    t = a", "mojo")
    t0 = time.perf_counter()
    for i, g in enumerate(gaps):  # every keystroke is an edit producing new (valid) source
        s.edit(m, f"# {tag} k{i}\ndef run(a: Int, mut t: Int) raises:\n    t = a + {i}")
        await asyncio.sleep(g)
    await s.idle()
    ev = [(t, e) for t, e, c in s.log if c == m]
    starts = [t for t, e in ev if e == "build-start"]
    kills = [t for t, e in ev if e in ("build-killed", "build-preempted")]
    wasted = sum(k - max(st for st in starts if st <= k) for k in kills)
    await s.close()
    return len(starts), len(kills), wasted, time.perf_counter() - t0


if __name__ == "__main__":
    gaps = trace()
    print(f"trace: {len(gaps)} keystrokes over {sum(gaps):.1f} s; "
          f"{sum(g > 0.3 for g in gaps)} gaps > 300 ms, {sum(g > 0.8 for g in gaps)} gaps > 800 ms")
    for d in (0.3, 0.8):
        if sys.argv[1:] == ["measure"]:
            st, k, w, dur = asyncio.run(measure(gaps, d))
            print(f"MEASURED  debounce {d * 1000:.0f} ms: {st} builds started, {k} killed, "
                  f"{st - k} completed, {w:.1f} s of build time thrown away (session {dur:.0f} s)")
        else:
            st, k, w = predict(gaps, d)
            print(f"PREDICTED debounce {d * 1000:.0f} ms: {st} builds started, {k} killed, "
                  f"{st - k} completed, {w:.1f} s of build time thrown away")
