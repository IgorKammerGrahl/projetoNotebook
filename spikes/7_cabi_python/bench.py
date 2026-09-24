"""Spike 7: célula C-ABI usando Python; CDLL vs PyDLL; callback de alocação."""
import ctypes, gc, json, os, signal, subprocess, sys, time, timeit, weakref
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
SO = os.path.join(HERE, "build", "cell7.so")
AllocFn = ctypes.CFUNCTYPE(ctypes.c_ssize_t, ctypes.c_ssize_t)

def load(Lib):
    lib = Lib(SO)
    for name, args, res in [("gil_state", [], ctypes.c_int32), ("gil_after_python", [], ctypes.c_int32),
                            ("probe_marker", [], ctypes.c_ssize_t), ("linalg_norm", [ctypes.c_ssize_t], ctypes.c_double),
                            ("collatz", [ctypes.c_ssize_t, AllocFn], ctypes.c_ssize_t),
                            ("many_allocs", [ctypes.c_ssize_t, AllocFn], ctypes.c_ssize_t),
                            ("fill_prealloc", [ctypes.c_ssize_t, ctypes.c_ssize_t], ctypes.c_ssize_t),
                            ("fill_alloc", [ctypes.c_ssize_t, AllocFn], ctypes.c_ssize_t)]:
        f = getattr(lib, name); f.argtypes = args; f.restype = res
    return lib

print("== 2a. Interpretador visto pela célula ==")
sys._host_marker = 4242
pyd = load(ctypes.PyDLL)
print(f"PyDLL  GIL na entrada={pyd.gil_state()}  após usar Python={pyd.gil_after_python()}  "
      f"marcador={pyd.probe_marker()}  norm(10)={pyd.linalg_norm(10):.6f} (np: {np.linalg.norm(np.arange(10.)):.6f})")
assert pyd.probe_marker() == 4242, "célula não vê o interpretador do host"
# CDLL solta o GIL: cada sonda em subprocesso, porque pode derrubar o processo.
for fn in ("gil_state", "probe_marker", "linalg_norm"):
    r = subprocess.run([sys.executable, "-c", f"""
import ctypes, sys; sys._host_marker = 4242
lib = ctypes.CDLL({SO!r}); f = lib.{fn}
f.restype = ctypes.c_double if {fn!r} == 'linalg_norm' else ctypes.c_ssize_t
print(f(10) if {fn!r} == 'linalg_norm' else f())
"""], capture_output=True, text=True, timeout=60)
    res = r.stdout.strip() if r.returncode == 0 else f"CRASH {signal.Signals(-r.returncode).name if r.returncode < 0 else r.returncode}"
    print(f"CDLL   {fn}: {res}")

print("== 2b. Célula tocando objetos Python: CDLL vs PyDLL (subprocessos) ==")
def child(mode, contend, iters=20_000, reps=50, runs=5):
    out = []
    for _ in range(runs):
        try:
            r = subprocess.run([sys.executable, os.path.join(HERE, "gil_child.py"), mode, str(int(contend)), str(iters), str(reps)],
                               capture_output=True, text=True, timeout=120)
            if r.returncode == 0: out.append(json.loads(r.stdout.strip().splitlines()[-1]))
            else:
                sig = -r.returncode if r.returncode < 0 else None
                out.append({"crash": signal.Signals(sig).name if sig else f"exit {r.returncode}",
                            "stderr": r.stderr.strip().splitlines()[:1]})
        except subprocess.TimeoutExpired:
            out.append({"crash": "DEADLOCK (timeout 120 s)"})
    return out
for mode in ("pydll", "cdll"):
    for contend in (False, True):
        res = child(mode, contend)
        summ = [r.get("crash") or f"lost={r['lost']} bad={r['bad_returns']}" for r in res]
        print(f"{mode:5s} thread={'sim' if contend else 'não'}: {summ}")

print("== 3. Callback de alocação (memória sob posse do Python) ==")
owned = []  # o host guarda os buffers; a célula só recebe endereços
def _alloc(nbytes):
    a = np.empty(nbytes, dtype=np.uint8); owned.append(a); return a.ctypes.data
alloc_cb = AllocFn(_alloc)  # manter referência viva durante a chamada

for Lib in (ctypes.PyDLL, ctypes.CDLL):
    lib = load(Lib); owned.clear()
    steps = lib.collatz(27, alloc_cb)
    seq = owned[-1].view(np.int64)
    assert steps == 112 and len(seq) == steps and seq[0] == 27 and seq[-1] == 1, (steps, seq[:3])
    assert seq.ctypes.data % 64 == 0 or seq.ctypes.data % 16 == 0
    K = 100_000; owned.clear()
    t = time.perf_counter(); lib.many_allocs(K, alloc_cb); dt = time.perf_counter() - t
    assert [int(a.view(np.int64)[0]) for a in owned[:3]] == [0, 1, 2]
    print(f"{Lib.__name__:6s} collatz(27): {steps} passos OK; callback: {dt / K * 1e6:.2f} µs/alocação "
          f"(inclui np.empty; np.empty sozinho: {min(timeit.repeat(lambda: np.empty(8, np.uint8), number=K, repeat=5)) / K * 1e6:.2f} µs)")
    owned.clear()

lib = load(ctypes.PyDLL)
N = 100 * 2**20 // 8
def pre():
    a = np.empty(N); lib.fill_prealloc(a.ctypes.data, N); return a
def viacb():
    owned.clear(); lib.fill_alloc(N, alloc_cb); return owned[-1].view(np.float64)
tp = min(timeit.repeat(pre, number=1, repeat=5)); tc = min(timeit.repeat(viacb, number=1, repeat=5))
print(f"100 MB: pré-alocado {tp*1e3:.1f} ms, via callback {tc*1e3:.1f} ms (diferença {(tc-tp)*1e6:+.0f} µs)")

# Ownership: o buffer entregue é do Python; morre quando o host solta.
owned.clear(); lib.collatz(27, alloc_cb); w = weakref.ref(owned[-1]); out = owned.pop()
assert w() is not None and out.view(np.int64)[0] == 27
del out; gc.collect(); assert w() is None
print("ownership: buffer é um ndarray comum, liberado quando o host solta a referência (weakref)")

# Falha no callback: exceção Python não atravessa a ABI C. Se o callback
# levanta, o ctypes devolve valor INDEFINIDO (não 0) -> a célula escreve em lixo.
def fail_run(safe):
    body = ("def f(n):\n    try:\n        raise MemoryError('x')\n    except BaseException:\n        return 0\n"
            if safe else "def f(n):\n    raise MemoryError('x')\n")
    r = subprocess.run([sys.executable, "-c", f"""
import ctypes
lib = ctypes.PyDLL({SO!r}); F = ctypes.CFUNCTYPE(ctypes.c_ssize_t, ctypes.c_ssize_t)
lib.collatz.argtypes = [ctypes.c_ssize_t, F]; lib.collatz.restype = ctypes.c_ssize_t
{body}
cb = F(f)
print(lib.collatz(27, cb))
"""], capture_output=True, text=True, timeout=60)
    return r.stdout.strip() if r.returncode == 0 else f"CRASH {signal.Signals(-r.returncode).name if r.returncode < 0 else r.returncode}"
print(f"callback levanta exceção (sem guarda):       {fail_run(False)}")
print(f"callback captura e devolve 0 (com guarda):   retorno da célula = {fail_run(True)}")
