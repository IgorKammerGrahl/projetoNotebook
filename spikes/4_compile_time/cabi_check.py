"""Build + load de célula via ABI C / ctypes: tempo após edição, custo por chamada, zero-copy."""
import ctypes, subprocess, tempfile, time, timeit
from pathlib import Path
import numpy as np
SRC = (Path(__file__).resolve().parent / "cell_cabi.mojo").read_text()
d = Path(tempfile.mkdtemp())
def build(src, tag):
    (d / f"c{tag}.mojo").write_text(src)
    t = time.perf_counter()
    subprocess.run(["mojo", "build", "--emit", "shared-lib", f"c{tag}.mojo", "-o", f"c{tag}.so"], cwd=d, check=True, capture_output=True)
    dt = time.perf_counter() - t
    lib = ctypes.CDLL(str(d / f"c{tag}.so"))
    lib.count.argtypes = [ctypes.c_ssize_t]; lib.count.restype = ctypes.c_ssize_t
    lib.sum_f64.argtypes = [ctypes.c_ssize_t, ctypes.c_ssize_t]; lib.sum_f64.restype = ctypes.c_double
    return dt, lib
ts = []
for k in range(3):
    dt, lib = build(SRC.replace("200)", f"{400 + k})"), k); ts.append(dt)
print(f"build C-ABI após edição: min {min(ts):.2f} s max {max(ts):.2f} s")
_, v1 = build(SRC, "a"); _, v2 = build(SRC.replace("200)", "5)"), "b")
print(f"duas versões no mesmo processo: {v1.count(1000)} vs {v2.count(1000)}")
N = 1_000_000
print(f"ctypes call count(1): {min(timeit.repeat(lambda: v1.count(1), number=N, repeat=5))/N*1e9:.0f} ns/call")
a = np.arange(100 * 2**20 // 8, dtype=np.float64)
t = time.perf_counter(); s = v1.sum_f64(a.ctypes.data, a.size); dt = time.perf_counter() - t
assert s == a.sum(); print(f"sum_f64 zero-copy 100MB: {dt*1e3:.1f} ms (== np.sum)")
