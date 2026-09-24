"""Tempo de build após edição: célula C-ABI só com buffers vs C-ABI usando Python."""
import re, subprocess, tempfile, time
from pathlib import Path
src = (Path(__file__).resolve().parent / "cell7.mojo").read_text()
# Variante só-buffer: remove as funções que tocam Python e o import.
blocks = re.split(r"\n(?=@export|def _gil|comptime )", src)
buf_only = "\n".join(b for b in blocks if "Python" not in b and "_gil" not in b and "external_call" not in b)
buf_only = buf_only.replace("from std.python import Python, PythonObject\n", "").replace("from std.ffi import external_call\n", "")
d = Path(tempfile.mkdtemp())
def build(code, tag):
    (d / f"{tag}.mojo").write_text(code)
    t = time.perf_counter()
    subprocess.run(["mojo", "build", "-O3", "--emit", "shared-lib", f"{tag}.mojo", "-o", f"{tag}.so"], cwd=d, check=True, capture_output=True)
    return time.perf_counter() - t
for name, code in (("C-ABI só buffers/escalares", buf_only), ("C-ABI usando Python (PyDLL)", src)):
    ts = [build(code.replace("steps * 8", f"steps * 8 + {k} - {k}"), f"{name[:5]}{k}".replace(" ", "")) for k in range(3)]
    print(f"{name:30s} após edição: min {min(ts):.2f} s  max {max(ts):.2f} s")
