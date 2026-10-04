"""CLI: `pixi run python -m kernel run|serve NOTEBOOK.nb.md`."""
import argparse
import asyncio
import contextlib
import os
import sys
from pathlib import Path

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
    """Run every cell once through the real architecture (Session + kernel process)."""
    from .session import Session

    async def main():
        s = Session(Path(path).parent / ".nbcache")
        await s.start()
        try:
            s.load(parse(Path(path).read_text()))
            await s.idle(timeout=3600)
            return [(cid, c) for cid, c in s.sched.cells.items() if c.kind in ("python", "mojo")]
        finally:
            await s.close()

    failed = 0
    for cid, c in asyncio.run(main()):
        print(f"[{cid}] {c.status}")
        if c.output:
            print("    " + c.output.rstrip().replace("\n", "\n    "))
        if c.error:
            print("    " + c.error.rstrip().replace("\n", "\n    "))
        failed += c.status != "ok"
    return 1 if failed else 0


async def serve(path: str, port: int, dev_origins: list[str], core_dumps: bool = False,
                speculate_debounce: float = 0.3, cache_limit_mb: int | None = None):
    from .web import NotebookServer
    static = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    srv = NotebookServer(Path(path), port=port, static_dir=static, extra_origins=tuple(dev_origins),
                         core_dumps=core_dumps, speculate_debounce=speculate_debounce,
                         cache_limit=cache_limit_mb * 2**20 if cache_limit_mb is not None else None)
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
    sp.add_argument("--verbose", action="store_true",
                    help="log connections and every message received from clients (debugging)")
    sp.add_argument("--speculate-debounce", type=float, default=0.3, metavar="SECONDS",
                    help="idle time after an edit before a Mojo cell builds in the background (default 0.3)")
    sp.add_argument("--cache-limit", type=int, metavar="MB",
                    help="size of the Mojo build cache (.nbcache) before the least recently used builds go "
                         "(default 512)")
    args = ap.parse_args()
    check_env()
    if args.cmd == "serve":
        import logging
        logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                            format="%(asctime)s %(name)s %(levelname)s %(message)s")
        with contextlib.suppress(KeyboardInterrupt):
            try:
                asyncio.run(serve(args.path, args.port, args.dev_origin, args.core_dumps, args.speculate_debounce,
                                  args.cache_limit))
            except (OSError, ValueError) as exc:
                ap.exit(1, f"kernel: {exc}\n")
        return
    sys.exit(run(args.path))


if __name__ == "__main__":
    main()
