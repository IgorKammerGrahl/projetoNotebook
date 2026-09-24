"""Kernel process (D-012): runs the Executor, one request at a time.

Speaks line-delimited JSON over a socket inherited from the server, never over
stdout/stderr: the executor redirects fds 1 and 2 to capture Mojo prints.
    {"op": "exec", ...Exec fields}  -> {"status", "error", "output", "previews"}
    {"op": "delete", "names": [...]} -> (no reply)
"""
import json
import resource
import socket
import sys

from .executor import Executor


def main(fd: int):
    # Crashes are an expected, recovered event here (D-013): no core dump. With
    # systemd-coredump a dump delayed death detection by ~0.5 s per crash.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
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
        wf.write(json.dumps(res).encode() + b"\n")
        wf.flush()


if __name__ == "__main__":
    main(int(sys.argv[1]))
