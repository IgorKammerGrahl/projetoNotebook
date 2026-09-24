"""CLI: `pixi run python -m kernel run|serve NOTEBOOK.nb.md`."""
import argparse
import asyncio
import contextlib
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


async def serve(path: str, port: int, dev_origins: list[str], core_dumps: bool = False):
    from .web import NotebookServer
    static = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    srv = NotebookServer(Path(path), port=port, static_dir=static, extra_origins=tuple(dev_origins),
                         core_dumps=core_dumps)
    await srv.start()
    print(f"notebook: {srv.url}", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await srv.close()


def main():
    ap = argparse.ArgumentParser(prog="kernel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="run every cell of a notebook once, in-process").add_argument("path")
    sp = sub.add_parser("serve", help="serve a notebook (HTTP + WebSocket on 127.0.0.1)")
    sp.add_argument("path")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--dev-origin", action="append", default=[],
                    help="extra allowed WebSocket Origin, e.g. http://localhost:5173 for the Vite dev server")
    sp.add_argument("--core-dumps", action="store_true",
                    help="debug: let a crashing kernel write a core dump (off by default: slows recovery ~0.5 s)")
    args = ap.parse_args()
    check_env()
    if args.cmd == "serve":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(serve(args.path, args.port, args.dev_origin, args.core_dumps))
        return
    sys.exit(run(args.path))


if __name__ == "__main__":
    main()
