"""Kernel process (D-012): runs the Executor, one request at a time.

Speaks line-delimited JSON over a socket inherited from the server, never over
stdout/stderr: the executor redirects fds 1 and 2 to capture Mojo prints.
    {"op": "exec", ...Exec fields}  -> {"status", "error", "output", "previews", "images", "duration_ms"}
    {"op": "delete", "names": [...]} -> (no reply)
"""
import json
import os
import resource
import socket
import sys
import warnings

from .executor import Executor


def main(fd: int, core_dumps: bool = False):
    # Crashes are an expected, recovered event here (D-013): no core dump by
    # default. With systemd-coredump a dump delayed death detection by ~0.5 s.
    # `serve --core-dumps` keeps the inherited limit, to debug a SIGSEGV in a Mojo cell.
    if not core_dumps:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Figures are drawn off-screen and captured after each cell (executor.figures, D-025);
    # plt.show() then has nothing to do and must not warn into the cell's output.
    os.environ["MPLBACKEND"] = "Agg"
    warnings.filterwarnings("ignore", message=".*non-interactive.*cannot be shown")
    sock = socket.socket(fileno=fd)
    rf, wf = sock.makefile("rb"), sock.makefile("wb")
    ex = Executor()
    for line in rf:
        req = json.loads(line)
        op = req.pop("op")
        if op == "delete":
            ex.delete(req["names"])
            continue
        res = ex.execute(req) if op == "exec" else {"error": f"unknown op {op!r}"}
        # default=str: previews of complex/datetime arrays (tolist() -> complex, date) are not
        # JSON; without it the kernel died on a valid cell and blamed it as `crashed`.
        wf.write(json.dumps(res, default=str).encode() + b"\n")
        wf.flush()


if __name__ == "__main__":
    main(int(sys.argv[1]), core_dumps="--core-dumps" in sys.argv[2:])
