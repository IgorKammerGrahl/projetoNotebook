"""Executor: owns the namespace and runs one cell at a time (D-012).
Runs in-process (CLI, tests) or inside the kernel process (server)."""
import base64
import contextlib
import inspect
import io
import sys
import time
import traceback
import types

import numpy as np

from . import mojo

PREVIEW_ITEMS = 20
PREVIEW_REPR = 1024
PREVIEW_ROWS, PREVIEW_COLS = 6, 8   # corner of a 2-D array shown as a table
STATS_LIMIT = 50_000_000            # ponytail: min/max/mean are 3 passes; skipped above this size
OUTPUT_LIMIT = 64 * 1024
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 4 * 1024 * 1024  # per PNG: keeps a result line under session.RESULT_LINE_LIMIT


def preview(value) -> dict:
    """What leaves the executor instead of the data itself."""
    if isinstance(value, np.ndarray):
        p = {"type": "ndarray", "shape": list(value.shape), "dtype": str(value.dtype),
             "head": value.ravel()[:PREVIEW_ITEMS].tolist()}
        if 0 < value.size <= STATS_LIMIT and value.dtype.kind in "iuf":
            p.update(min=value.min().item(), max=value.max().item(), mean=float(value.mean()))
        if value.ndim == 2:
            p["rows"] = value[:PREVIEW_ROWS, :PREVIEW_COLS].tolist()
        return p
    if isinstance(value, types.FunctionType):
        with contextlib.suppress(ValueError, TypeError):
            return {"type": "function", "repr": f"{value.__name__}{inspect.signature(value)}"[:PREVIEW_REPR]}
    try:
        r = repr(value)
    except BaseException as e:
        r = f"<repr failed: {type(e).__name__}>"
    return {"type": type(value).__name__, "repr": r[:PREVIEW_REPR]}


def figures() -> tuple[list[str], str]:
    """PNG (base64) of every matplotlib figure the cell left open, then close them all.
    Matplotlib is only touched if the cell imported pyplot. Returns (images, note)."""
    plt = sys.modules.get("matplotlib.pyplot")
    if plt is None:
        return [], ""
    images, notes, nums = [], [], plt.get_fignums()
    for num in nums[:MAX_IMAGES]:
        buf = io.BytesIO()
        try:
            plt.figure(num).savefig(buf, format="png", bbox_inches="tight")
            if buf.tell() > MAX_IMAGE_BYTES:
                notes.append(f"(figure {num} not shown: {buf.tell() / 2**20:.1f} MiB PNG, "
                             f"at most {MAX_IMAGE_BYTES // 2**20} MiB; lower its dpi or size)")
                continue
            images.append(base64.b64encode(buf.getvalue()).decode())
        except Exception as e:  # a broken figure must not lose the cell's other results
            notes.append(f"(figure {num} could not be drawn: {type(e).__name__}: {e})")
    plt.close("all")  # always: a figure left open would show up again under the next cell
    if len(nums) > MAX_IMAGES:
        notes.append(f"({len(nums) - MAX_IMAGES} figure(s) not shown: at most {MAX_IMAGES} per cell)")
    return images, "".join(f"\n{n}" for n in notes)


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
        {status, error, output, previews, images, duration_ms}; on error no name of `defs` is left behind."""
        self.delete(req["delete"])
        run = self._mojo if req["kind"] == "mojo" else self._python
        t0 = time.perf_counter()
        status, error, output = run(req)
        duration_ms = (time.perf_counter() - t0) * 1000  # the run only: a Mojo build happened before
        images, note = figures()  # also after an error: whatever it drew helps to debug it
        if status != "ok":
            self.delete(req["defs"])
        # modules (`import numpy as np`) are graph definitions, but their preview is noise
        previews = {n: preview(self.ns[n]) for n in req["defs"]
                    if n in self.ns and not isinstance(self.ns[n], types.ModuleType)} if status == "ok" else {}
        return {"status": status, "error": error, "output": _truncate(output) + note, "previews": previews,
                "images": images, "duration_ms": duration_ms}

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
