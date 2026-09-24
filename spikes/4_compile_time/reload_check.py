"""Uma célula Mojo editada pode ser recarregada no MESMO processo Python?
E o import a frio (cache do compilador limpo) via mojo.importer."""
import importlib, importlib.util, shutil, subprocess, sys, tempfile, time
from pathlib import Path

SRC = (Path(__file__).resolve().parent / "cell.mojo").read_text()
CACHE = Path(subprocess.check_output(["mojo", "--print-cache-location"], text=True).strip())
shutil.rmtree(CACHE, ignore_errors=True)

d = Path(tempfile.mkdtemp()); (d / "cell.mojo").write_text(SRC); sys.path.insert(0, str(d))
import mojo.importer  # noqa: F401
t = time.perf_counter(); import cell; print(f"importer frio real (cache limpo): {time.perf_counter()-t:.2f} s")
v1 = cell.count(1000)

(d / "cell.mojo").write_text(SRC.replace("200)", "5)"))  # muda max_iter -> resultado muda
t = time.perf_counter(); cell2 = importlib.reload(cell); dt = time.perf_counter() - t
v2 = cell2.count(1000)
print(f"reload() após edição: {dt:.2f} s, antes={v1} depois={v2} -> {'RECARREGOU' if v1 != v2 else 'CÓDIGO ANTIGO (reload não funciona)'}")

# Alternativa: .so com nome único por versão + PyInit com nome único, via spec_from_file_location.
def load_versioned(src: str, tag: str):
    src = src.replace("PyInit_cell", f"PyInit_cell_{tag}").replace('"cell"', f'"cell_{tag}"')
    (d / f"cell_{tag}.mojo").write_text(src)
    subprocess.run(["mojo", "build", "--emit", "shared-lib", f"cell_{tag}.mojo", "-o", f"cell_{tag}.so"],
                   cwd=d, check=True, capture_output=True)
    spec = importlib.util.spec_from_file_location(f"cell_{tag}", d / f"cell_{tag}.so")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
a = load_versioned(SRC, "v1"); b = load_versioned(SRC.replace("200)", "5)"), "v2")
print(f"nome versionado: v1={a.count(1000)} v2={b.count(1000)} -> {'OK' if a.count(1000) != b.count(1000) else 'FALHOU'}")

# Mesmo nome de módulo, arquivo .so diferente: o CPython reaproveita?
def load_same_name(src, fname):
    (d / f"{fname}.mojo").write_text(src.replace("PyInit_cell", "PyInit_samename").replace('"cell"', '"samename"'))
    subprocess.run(["mojo", "build", "--emit", "shared-lib", f"{fname}.mojo", "-o", f"{fname}.so"],
                   cwd=d, check=True, capture_output=True)
    spec = importlib.util.spec_from_file_location("samename", d / f"{fname}.so")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
x = load_same_name(SRC, "s1"); y = load_same_name(SRC.replace("200)", "5)"), "s2")
print(f"mesmo nome, .so diferente: {x.count(1000)} vs {y.count(1000)} -> {'OK' if x.count(1000) != y.count(1000) else 'FALHOU'}")
