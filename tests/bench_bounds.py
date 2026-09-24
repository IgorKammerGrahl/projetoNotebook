"""Custo do bounds check (revisão A3). `PYTHONPATH=. pixi run python tests/bench_bounds.py`
Todo fonte leva um nonce em comentário (A4): o cache global do compilador usa o texto bruto."""
import tempfile, timeit, uuid
import numpy as np
from kernel import mojo

N = 10**7
CASES = {
    "soma float64": ("float64", "mut s: Float64",
        "for i in range(len(xs)):\n        s += xs[i]",
        "var p = xs.unsafe_ptr()\n    for i in range(len(xs)):\n        s += p.unsafe_offset(i).unsafe_load()"),
    "soma int64": ("int64", "mut s: Int",
        "for i in range(len(xs)):\n        s += Int(xs[i])",
        "var p = xs.unsafe_ptr()\n    for i in range(len(xs)):\n        s += Int(p.unsafe_offset(i).unsafe_load())"),
    "escala e escreve float64": ("float64", "mut ys: ArrayOut[DType.float64]",
        "ys.alloc(len(xs))\n    for i in range(len(xs)):\n        ys[i] = xs[i] * 2.0",
        "ys.alloc(len(xs))\n    var p = xs.unsafe_ptr()\n    var q = ys.unsafe_ptr()\n"
        "    for i in range(len(xs)):\n        q.unsafe_offset(i).unsafe_store(p.unsafe_offset(i).unsafe_load() * 2.0)"),
    # compute-bound: 8192 elementos (64 KB, cabe em L2) x 1221 passadas ~ 10^7 acessos
    "soma int64, em cache (L2)": ("int64", "mut s: Int",
        "for _ in range(1221):\n        for i in range(len(xs)):\n            s += Int(xs[i])",
        "var p = xs.unsafe_ptr()\n    for _ in range(1221):\n        for i in range(len(xs)):\n"
        "            s += Int(p.unsafe_offset(i).unsafe_load())"),
    # índice que o compilador não prova estar nos limites: gather xs[xs[i] % n]
    "gather int64, em cache (L2)": ("int64", "mut s: Int",
        "var n = len(xs)\n    for _ in range(1221):\n        for i in range(n):\n            s += Int(xs[Int(xs[i]) % n])",
        "var n = len(xs)\n    var p = xs.unsafe_ptr()\n    for _ in range(1221):\n        for i in range(n):\n"
        "            s += Int(p.unsafe_offset(Int(p.unsafe_offset(i).unsafe_load()) % n).unsafe_load())"),
    "gather int64 com unsafe_get": ("int64", "mut s: Int",
        "var n = len(xs)\n    for _ in range(1221):\n        for i in range(n):\n            s += Int(xs[Int(xs[i]) % n])",
        "var n = len(xs)\n    for _ in range(1221):\n        for i in range(n):\n"
        "            s += Int(xs.unsafe_get(Int(xs.unsafe_get(i)) % n))"),
}
SIZES = {"gather int64 com unsafe_get": 8192, "soma int64, em cache (L2)": 8192, "gather int64, em cache (L2)": 8192}
runner = mojo.MojoRunner(tempfile.mkdtemp())
for name, (dt, out, checked, unchecked) in CASES.items():
    xs = np.arange(SIZES.get(name, N), dtype=dt)
    times = {}
    for label, body in (("checked", checked), ("unchecked", unchecked)):
        code = f"# nonce {uuid.uuid4().hex}\ndef run(xs: ArrayIn[DType.{dt}], {out}) raises:\n    {body}"
        iface = mojo.parse_interface(code)
        lib = runner.load(code, iface)
        res = runner.call(lib, iface, {"xs": xs})[0]
        times[label] = min(timeit.repeat(lambda: runner.call(lib, iface, {"xs": xs}), number=1, repeat=7))
        times[label + "_res"] = res
    r = times["checked"] / times["unchecked"]
    a, b = times["checked_res"], times["unchecked_res"]
    same = all(np.array_equal(a[k], b[k]) if isinstance(a[k], np.ndarray) else a[k] == b[k] for k in a)
    print(f"{name:26s} checked {times['checked']*1e3:7.2f} ms | unchecked {times['unchecked']*1e3:7.2f} ms | "
          f"{r:5.2f}x ({(r-1)*100:+.0f}%) | mesmo resultado: {same}")

# --- SIMD explícito vs laço escalar (revisão da parte A, item 2) ---
import re, subprocess
from pathlib import Path

W = 8
def simd_sum(conv):  # conv: "" for float64, "Int" for int64
    acc = f"SIMD[DType.{{dt}}, {W}](0)"
    tail = f"s += {conv}(p.unsafe_load(i))" if conv else "s += p.unsafe_load(i)"
    red = f"s += {conv}(acc.reduce_add())" if conv else "s += acc.reduce_add()"
    return (f"var p = xs.unsafe_ptr()\n    var n = len(xs)\n    var acc = {acc}\n"
            f"    for _ in range(REPS):\n        var i = 0\n"
            f"        while i + {W} <= n:\n            acc += p.unsafe_load[width={W}](i)\n            i += {W}\n"
            f"        while i < n:\n            {tail}\n            i += 1\n    {red}")
def scalar_sum(conv):
    load = f"{conv}(p.unsafe_load(i))" if conv else "p.unsafe_load(i)"
    return f"var p = xs.unsafe_ptr()\n    for _ in range(REPS):\n        for i in range(len(xs)):\n            s += {load}"

def run_pair(name, dt, out, a, b, size, reps, la, lb):
    xs = np.arange(size, dtype=dt)
    t, res = {}, {}
    for label, body in ((la, a), (lb, b)):
        code = (f"# nonce {uuid.uuid4().hex}\ndef run(xs: ArrayIn[DType.{dt}], {out}) raises:\n    "
                + body.replace("{dt}", dt).replace("REPS", str(reps)))
        iface = mojo.parse_interface(code)
        lib = runner.load(code, iface)
        res[label] = runner.call(lib, iface, {"xs": xs})[0]["s"]
        t[label] = min(timeit.repeat(lambda: runner.call(lib, iface, {"xs": xs}), number=1, repeat=7))
    print(f"{name:34s} {la} {t[la]*1e3:7.2f} ms | {lb} {t[lb]*1e3:7.2f} ms | "
          f"{t[la]/t[lb]:5.2f}x | mesmo resultado: {res[la] == res[lb]}")
    return code

print("\n-- SIMD explícito (width=8) vs laço escalar --")
for where, size, reps in (("L2", 8192, 1221), ("80 MB", N, 1)):
    for dt, conv, out in (("float64", "", "mut s: Float64"), ("int64", "Int", "mut s: Int")):
        run_pair(f"soma {dt}, {where}", dt, out, scalar_sum(conv), simd_sum(conv), size, reps, "escalar", "SIMD")

print("\n-- assembly do laço escalar (instruções no arquivo inteiro da célula) --")
tmp = Path(tempfile.mkdtemp())
for dt, conv, out in (("float64", "", "mut s: Float64"), ("int64", "Int", "mut s: Int")):
    code = f"def run(xs: ArrayIn[DType.{dt}], {out}) raises:\n    " + scalar_sum(conv).replace("REPS", "1")
    src = tmp / f"{dt}.mojo"
    src.write_text(mojo.generate(code, mojo.parse_interface(code)))
    subprocess.run(["mojo", "build", "-O3", "--emit", "asm", str(src), "-o", str(tmp / f"{dt}.s")], check=True, capture_output=True)
    asm = (tmp / f"{dt}.s").read_text()
    counts = {k: len(re.findall(rf"\b{k}\b", asm)) for k in ("vaddsd", "vaddpd", "vpaddq", "addsd")}
    ymm = len(re.findall(r"%[yz]mm\d+", asm))
    print(f"{dt:8s} {counts}  registradores ymm/zmm: {ymm}")
