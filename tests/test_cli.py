"""Integration: CLI on a real file, plus the D-006 environment check."""
import os
import subprocess
import sys

from kernel.__main__ import env_problems

NB = """# Demo

```python
total = sum(xs)
print(total)
```

```python
import numpy as np
xs = [1, 2, 3]
arr = np.array(xs, dtype=np.float64)
```

```mojo
def run(arr: ArrayIn[DType.float64], mut s: Float64) raises:
    for i in range(len(arr)):
        s += arr[i]
    print("mojo sum", s)
```
"""


def test_cli_runs_notebook_in_dependency_order(tmp_path):
    p = tmp_path / "demo.nb.md"
    p.write_text(NB)
    r = subprocess.run([sys.executable, "-m", "kernel", "run", str(p)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["[1] ok", "    6", "[2] ok", "[3] ok", "    mojo sum 6.0"]


def test_cli_exit_code_on_error(tmp_path):
    p = tmp_path / "bad.nb.md"
    p.write_text("```python\nx = 1 / 0\n```\n")
    r = subprocess.run([sys.executable, "-m", "kernel", "run", str(p)], capture_output=True, text=True)
    assert r.returncode == 1 and "ZeroDivisionError" in r.stdout


def test_cli_refuses_wrong_environment(tmp_path):
    p = tmp_path / "x.nb.md"
    p.write_text("```python\nx = 1\n```\n")
    env = {**os.environ, "CONDA_PREFIX": str(tmp_path)}
    r = subprocess.run([sys.executable, "-m", "kernel", "run", str(p)], capture_output=True, text=True, env=env)
    assert r.returncode != 0 and "wrong environment" in r.stderr and "expected" in r.stderr


def test_env_problems():
    assert env_problems("/env", None, "/env/np.py")[0].startswith("CONDA_PREFIX is not set")
    assert env_problems("/env", "/env", "/env/lib/numpy/__init__.py") == []
    probs = env_problems("/usr", "/env", "/usr/lib/numpy/__init__.py")
    assert len(probs) == 2 and "Python loaded from /usr" in probs[0] and "NumPy" in probs[1]
