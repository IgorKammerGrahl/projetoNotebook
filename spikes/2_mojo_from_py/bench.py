import sys, time, timeit
sys.path.insert(0, "build")
t = time.perf_counter(); import mojo_ext; print(f"import .so: {(time.perf_counter()-t)*1e3:.2f} ms")

def noop(x): return x
N = 1_000_000
for name, stmt in [("py noop (baseline)", lambda: noop(1)),
                   ("mojo noop", lambda: mojo_ext.noop(1)),
                   ("mojo add ", lambda: mojo_ext.add(1, 2))]:
    best = min(timeit.repeat(stmt, number=N, repeat=5)) / N * 1e9
    print(f"{name}: {best:.0f} ns/call (inclui ~lambda)")

def py_sum_to(n):
    acc = 0
    for i in range(n): acc = (acc + i * i) % 1_000_003
    return acc
n = 10_000_000
t = time.perf_counter(); a = mojo_ext.sum_to(n); tm = time.perf_counter() - t
t = time.perf_counter(); b = py_sum_to(n); tp = time.perf_counter() - t
assert a == b, (a, b)
print(f"sum_to(1e7): mojo {tm*1e3:.1f} ms, python {tp*1e3:.0f} ms ({tp/tm:.0f}x)")
