"""Executor: owns the namespace and runs one cell at a time (D-012).
Runs in-process (CLI, tests) or inside the kernel process (server)."""
import contextlib
import io
import traceback
import types

import numpy as np

from . import mojo

PREVIEW_ITEMS = 20
PREVIEW_REPR = 1024
OUTPUT_LIMIT = 64 * 1024


def preview(value) -> dict:
    """What leaves the executor instead of the data itself."""
    if isinstance(value, np.ndarray):
        return {"type": "ndarray", "shape": list(value.shape), "dtype": str(value.dtype),
                "head": value.ravel()[:PREVIEW_ITEMS].tolist()}
    try:
        r = repr(value)
    except BaseException as e:
        r = f"<repr failed: {type(e).__name__}>"
    return {"type": type(value).__name__, "repr": r[:PREVIEW_REPR]}


def _truncate(text: str) -> str:
    return text if len(text) <= OUTPUT_LIMIT else text[:OUTPUT_LIMIT] + "\n… (output truncated)"


class Executor:
    def __init__(self):
        self.ns: dict = {"__name__": "__notebook__"}

    def delete(self, names):
        for name in names:
            self.ns.pop(name, None)

    def execute(self, req: dict) -> dict:
        """req: {cid, kind, code, defs, delete, artifact}. Returns
        {status, error, output, previews}; on error no name of `defs` is left behind."""
        self.delete(req["delete"])
        run = self._mojo if req["kind"] == "mojo" else self._python
        status, error, output = run(req)
        if status != "ok":
            self.delete(req["defs"])
        # modules (`import numpy as np`) are graph definitions, but their preview is noise
        previews = {n: preview(self.ns[n]) for n in req["defs"]
                    if n in self.ns and not isinstance(self.ns[n], types.ModuleType)} if status == "ok" else {}
        return {"status": status, "error": error, "output": _truncate(output), "previews": previews}

    def _python(self, req):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                exec(compile(req["code"], f"<cell {req['cid']}>", "exec"), self.ns)
            return "ok", "", out.getvalue()
        except BaseException:
            return "error", traceback.format_exc(), out.getvalue()

    def _mojo(self, req):
        iface = mojo.parse_interface(req["code"])  # validated by the scheduler already
        missing = [n for n, _ in iface.ins if n not in self.ns]
        if missing:
            return "error", f"name(s) not defined by any cell: {', '.join(missing)}", ""
        try:
            outs, output = mojo.call(mojo.load(req["artifact"]), iface, {n: self.ns[n] for n, _ in iface.ins})
        except mojo.CellError as e:
            return "error", str(e), e.output
        self.ns.update(outs)
        return "ok", "", output
