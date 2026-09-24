"""Medições da Fase 2 (não é teste): `PYTHONPATH=. pixi run python tests/bench_phase2.py`.
Toda medição de compilação usa fonte com nonce único (o cache global do compilador usa o texto bruto)."""
import subprocess, sys, tempfile, time, timeit, uuid
import numpy as np
from kernel import mojo
from kernel.engine import Engine
from kernel.fmt import Cell

SCALE = open("tests/test_mojo_cells.py").read().split('SCALE = """', 1)[1].split('"""', 1)[0]
cache = tempfile.mkdtemp()
e = Engine(cache_dir=cache)
e.load([Cell("python", "import numpy as np\nxs = np.arange(1000, dtype=np.float64)"),
        Cell("python", "k = 2.0"), Cell("mojo", f"# nonce {uuid.uuid4().hex}\n" + SCALE), Cell("python", "t2 = total * 2")])
data, k, m, _ = e.cells
assert e.cells[m].status == "ok"

ts = []
for i in range(1, 7):  # nonce -> new text -> real compile, never the global compiler cache
    t = time.perf_counter(); e.edit(m, f"# nonce {uuid.uuid4().hex}\n" + SCALE); ts.append(time.perf_counter() - t)
    assert e.cells[m].status == "ok"
print(f"edição Mojo -> resultado (compila): min {min(ts):.2f} s, max {max(ts):.2f} s")

t = min(timeit.repeat(lambda: e.edit(k, "k = 3.0"), number=50, repeat=5)) / 50
print(f"parâmetro novo em célula Python -> Mojo reexecuta (sem compilar): {t * 1e3:.3f} ms (2 células Python + 1 Mojo)")

iface = mojo.parse_interface(e.cells[m].code)
lib = e.mojo.load(e.cells[m].code, iface)
vals = {"xs": np.arange(4, dtype=np.float64), "k": 2.0}
t = min(timeit.repeat(lambda: e.mojo.call(lib, iface, vals), number=2000, repeat=5)) / 2000
print(f"overhead fixo de uma chamada (validação + slots + 1 alloc + captura de fd): {t * 1e6:.1f} µs")

code = e.cells[m].code.replace("'", "\\'")
probe = f"""
import time; t = time.perf_counter()
from kernel.engine import Engine; from kernel.fmt import Cell
import numpy as np
e = Engine(cache_dir={cache!r}); e.ns.update(xs=np.arange(4, dtype=np.float64), k=2.0)
t1 = time.perf_counter(); e.load([Cell('mojo', '''{code}''')]); t2 = time.perf_counter()
assert e.mojo.compiles == 0 and list(e.cells.values())[0].status == 'ok'
print(f'{{(t2 - t1) * 1e3:.1f}}')
"""
r = [float(subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True).stdout) for _ in range(3)]
print(f"processo novo, cache em disco (sem compilar): min {min(r):.1f} ms, max {max(r):.1f} ms (mojo --version + nm + dlopen + chamada)")
