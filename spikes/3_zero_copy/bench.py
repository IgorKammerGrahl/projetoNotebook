import gc, os, sys, time
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "build"))
import zc

N = 100 * 1024 * 1024 // 8  # 100 MB de float64
PAGE = os.sysconf("SC_PAGE_SIZE")
def rss_mb(): return int(open("/proc/self/statm").read().split()[1]) * PAGE / 2**20

def best(f, reps=7):
    ts = []
    for _ in range(reps):
        t = time.perf_counter(); r = f(); ts.append(time.perf_counter() - t)
    return min(ts), r

def fmt(s): return f"{s*1e3:9.3f} ms" if s >= 1e-3 else f"{s*1e6:9.2f} µs"

class MojoArray:
    """Expõe um MojoBuf ao NumPy via __array_interface__; o array guarda
    esta instância como .base, que guarda o MojoBuf -> ownership correto."""
    def __init__(self, buf):
        self.buf = buf
        self.__array_interface__ = {"shape": (buf.size(),), "typestr": "<f8",
                                    "data": (buf.addr(), False), "version": 3}

a = np.arange(N, dtype=np.float64)
assert a.flags.c_contiguous and a.dtype == np.float64

print("== NumPy -> Mojo ==")
t, _ = best(lambda: a.copy());            print(f"np.copy 100MB (baseline)     {fmt(t)}")
t, _ = best(lambda: zc.copy_in(a));       print(f"Mojo copy_in (memcpy)        {fmt(t)}")
t, _ = best(lambda: zc.touch(a), 1000);   print(f"Mojo zero-copy handoff       {fmt(t)}")
t, s = best(lambda: zc.sum_view(a));      print(f"Mojo sum_view (lê 100MB)     {fmt(t)}")
t2, s2 = best(lambda: a.sum());           print(f"np.sum 100MB                 {fmt(t2)}")
assert s == s2, (s, s2)
b = np.ones(1000); zc.scale_inplace(b, 3.0); assert (b == 3.0).all(), "escrita in-place invisível"
k = 100_000
t, _ = best(lambda: zc.sum_naive(a[:k]), 3)
print(f"Mojo sum_naive (elem a elem) {fmt(t)} p/ {k} el -> extrapolado 100MB: {t*N/k:.1f} s")

print("== Mojo -> NumPy ==")
gc.collect(); r0 = rss_mb()
t, buf = best(lambda: zc.MojoBuf(N), 1); print(f"MojoBuf(100MB) alloc         {fmt(t)}")
buf.fill_arange()
t, arr = best(lambda: np.asarray(MojoArray(buf)), 1000); print(f"np.asarray zero-copy         {fmt(t)}")
t, _ = best(lambda: np.array(MojoArray(buf), copy=True)); print(f"np.array com cópia           {fmt(t)}")
assert arr.ctypes.data == buf.addr() and arr[123] == 123.0
arr[5] = -1.0; assert zc.sum_view(arr) == a.sum() - 6.0, "Mojo não vê escrita do NumPy"
import weakref
r1 = rss_mb()
w = weakref.ref(arr.base)           # MojoArray, único dono de buf
assert sys.getrefcount(buf) == 3     # buf local + MojoArray.buf + arg do getrefcount
del buf; gc.collect()
assert w() is not None and arr[N - 1] == N - 1, "array ficou pendurado"
del arr; gc.collect()
assert w() is None, "MojoArray não foi coletado -> MojoBuf não seria liberado"
r3 = rss_mb()
print(f"RSS MB: antes {r0:.0f} | com buf {r1:.0f} | após liberar tudo {r3:.0f}")
print("OK: lifetime Mojo->NumPy correto (MojoBuf morre só com o último array).")
print("   (__deinit__ confirmado por build de debug; RSS não cai: o alocador do")
print("    runtime Mojo retém blocos liberados e os reutiliza -- ver SPIKE.md)")
