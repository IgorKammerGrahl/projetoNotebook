"""CLI: `pixi run python -m kernel run NOTEBOOK.nb.md`."""
import argparse
import os
import sys
from pathlib import Path

from .engine import Engine
from .fmt import parse


def env_problems(prefix: str, expected: str | None, numpy_file: str) -> list[str]:
    """D-006: Python and NumPy must come from the pixi environment."""
    if not expected:
        return ["CONDA_PREFIX is not set: start the kernel through `pixi run`."]
    exp = Path(expected).resolve()
    problems = []
    if Path(prefix).resolve() != exp:
        problems.append(f"Python loaded from {prefix}, expected {exp}.")
    if not Path(numpy_file).resolve().is_relative_to(exp):
        problems.append(f"NumPy loaded from {numpy_file}, expected under {exp}.")
    return problems


def check_env():
    import numpy
    problems = env_problems(sys.prefix, os.environ.get("CONDA_PREFIX"), numpy.__file__)
    if problems:
        sys.exit("kernel: wrong environment\n  " + "\n  ".join(problems))


def run(path: str) -> int:
    eng = Engine(cache_dir=Path(path).parent / ".nbcache")
    eng.load(parse(Path(path).read_text()))
    failed = 0
    for cid, c in eng.cells.items():
        print(f"[{cid}] {c.status}")
        if c.output:
            print("    " + c.output.rstrip().replace("\n", "\n    "))
        if c.error:
            print("    " + c.error.rstrip().replace("\n", "\n    "))
            failed += 1
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(prog="kernel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run every Python cell of a notebook").add_argument("path")
    args = ap.parse_args()
    check_env()
    sys.exit(run(args.path))


if __name__ == "__main__":
    main()
