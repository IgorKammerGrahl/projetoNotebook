"""Tempo de compilação de uma célula Mojo de ~20 linhas.
Rodar via pixi (precisa de `mojo` no PATH)."""
import importlib, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = (HERE / "cell.mojo").read_text()
CACHE = Path(subprocess.check_output(["mojo", "--print-cache-location"], text=True).strip())
REPS = 3

def sh(*cmd, cwd=None):
    t = time.perf_counter()
    subprocess.run(cmd, check=True, cwd=cwd, capture_output=True)
    return time.perf_counter() - t

def clear_cache(): shutil.rmtree(CACHE, ignore_errors=True)

def build(src: str, opt: str, clear: bool):
    d = Path(tempfile.mkdtemp())
    (d / "cell.mojo").write_text(src)
    if clear: clear_cache()
    return sh("mojo", "build", opt, "--emit", "shared-lib", "cell.mojo", "-o", "cell.so", cwd=d)

def edited(i): return SRC.replace("# EDIT-MARKER", f"# edit {i}").replace("200)", f"{200 + i})")

def row(name, xs): print(f"{name:52s} min {min(xs):6.2f} s  max {max(xs):6.2f} s")

for opt in ("-O0", "-O3"):
    row(f"build {opt} frio (cache do compilador limpo)", [build(SRC, opt, True) for _ in range(REPS)])
    build(SRC, opt, False)
    row(f"build {opt} mesmo fonte, cache quente", [build(SRC, opt, False) for _ in range(REPS)])
    row(f"build {opt} após editar 1 linha", [build(edited(i), opt, False) for i in range(1, REPS + 1)])

# mojo.importer: cache por hash do diretório em __mojocache__/
d = Path(tempfile.mkdtemp()); (d / "cell.mojo").write_text(SRC); sys.path.insert(0, str(d))
import mojo.importer  # noqa: F401
t = time.perf_counter(); import cell; t_cold = time.perf_counter() - t
assert cell.count(1000) > 0
t = time.perf_counter(); r = subprocess.run([sys.executable, "-c", "import mojo.importer, cell"], cwd=d, check=True)
t_hit = time.perf_counter() - t
t = time.perf_counter(); subprocess.run([sys.executable, "-c", "import mojo.importer"], check=True)
t_base = time.perf_counter() - t
print(f"{'mojo.importer: import a frio (compila)':52s} {t_cold:6.2f} s")
print(f"{'mojo.importer: novo processo, hash igual (hit)':52s} {t_hit - t_base:6.3f} s (descontado startup {t_base:.2f} s)")
(d / "cell.mojo").write_text(edited(9))
t = time.perf_counter(); importlib.reload(cell); t_reload = time.perf_counter() - t
print(f"{'mojo.importer: reload() após editar, mesmo processo':52s} {t_reload:6.2f} s  -> count(1000)={cell.count(1000)}")
