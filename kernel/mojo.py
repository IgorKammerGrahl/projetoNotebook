"""Mojo cells: interface (D-009), build cache (D-004), loader (D-002),
calls with Python-owned memory (D-003)."""
import asyncio
import contextlib
import ctypes
import functools
import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

PRELUDE = (Path(__file__).parent / "prelude.mojo").read_text()
_PY_SYMBOL = re.compile(r"Python|dlsym|\bPy")
DTYPES = ("float64", "float32", "int64", "int32")


@dataclass(frozen=True)
class Type:
    mojo: str               # input spelling in run()'s signature
    np_dtype: object = None  # arrays only
    ctype: object = None     # scalars only

    @property
    def array(self):
        return self.np_dtype is not None


SCALARS = {"Int": Type("Int", ctype=ctypes.c_int64), "Float64": Type("Float64", ctype=ctypes.c_double)}
ARRAYS = {d: Type(f"ArrayIn[DType.{d}]", np_dtype=np.dtype(d)) for d in DTYPES}


class InterfaceError(Exception):
    pass


class CompileError(Exception):
    pass


class CellError(Exception):
    def __init__(self, msg: str, output: str = ""):
        super().__init__(msg)
        self.output = output


@dataclass(frozen=True)
class Interface:
    ins: tuple[tuple[str, Type], ...]
    outs: tuple[tuple[str, Type], ...]
    order: tuple[str, ...]  # run()'s parameter order


# D-010: a restricted grammar for run()'s signature, not a Mojo parser.
ACCEPTED = f"""accepted form:
    def run(<param>, ...) [raises]:
    input:   name: Int | Float64 | ArrayIn[DType.<dt>]
    output:  mut name: Int | Float64 | ArrayOut[DType.<dt>]
    <dt>: {', '.join(DTYPES)}; end-of-line `#` comments allowed;
    no defaults, no return type, no [params]"""
_RUN = re.compile(r"^def run\b", re.M)
_PARAM = re.compile(r"^(mut\s+)?([A-Za-z_]\w*)\s*:\s*(.+)$")
_TYPE = re.compile(r"^(?:(Int|Float64)|(ArrayIn|ArrayOut)\s*\[\s*(?:DType)?\.(\w+)\s*\])$")


def parse_interface(code: str) -> Interface:
    runs = list(_RUN.finditer(code))
    if len(runs) != 1:
        raise InterfaceError(("a Mojo cell must define exactly one top-level `def run(...)`; "
                              f"found {len(runs)}\n") + ACCEPTED)
    at = runs[0].end()
    line_of = lambda i: code.count("\n", 0, i) + 1  # noqa: E731

    def fail(i, msg):
        raise InterfaceError(f"line {line_of(i)}: {msg}\n{ACCEPTED}")

    rest = code[at:]
    if not rest.lstrip().startswith("("):
        fail(at, "expected `(` right after `def run`")
    open_ = at + rest.index("(")
    # End-of-line comments -> spaces of equal length (offsets, hence line numbers, survive).
    # Safe: defaults are forbidden, so no string literal can contain a `#` here.
    code = code[:open_] + re.sub(r"#[^\n]*", lambda m: " " * len(m.group()), code[open_:])
    close = code.find(")", open_)
    if close == -1:
        fail(open_, "unclosed `(` in run()'s signature")
    raw = code[open_ + 1:close]
    if "(" in raw:
        fail(open_, "parentheses are not allowed inside run()'s parameters")
    tail = re.match(r"\s*(raises\s*)?:", code[close + 1:])
    if not tail:
        fail(close, "after `)` only `raises` and `:` are accepted")

    ins, outs, order, offset = [], [], [], open_ + 1
    for chunk in raw.split(","):
        pos, param = offset + len(chunk) - len(chunk.lstrip()), " ".join(chunk.split())
        offset += len(chunk) + 1
        if not param:
            continue
        if "=" in param:
            fail(pos, f"default values are not allowed ({param!r})")
        m = _PARAM.match(param)
        if not m:
            fail(pos, f"cannot read parameter {param!r}")
        is_mut, name, tname = bool(m.group(1)), m.group(2), m.group(3)
        tm = _TYPE.match(tname)
        if not tm:
            fail(pos, f"unsupported type {tname!r} for {name!r}")
        if name.startswith("_"):
            fail(pos, f"{name!r}: names starting with `_` are private and cannot cross cells")
        if name in order:
            fail(pos, f"{name!r} appears twice")
        scalar, kind, dtype = tm.groups()
        if kind and dtype not in DTYPES:
            fail(pos, f"unsupported dtype {dtype!r}")
        if kind == "ArrayIn" and is_mut:
            fail(pos, f"`mut {name}: ArrayIn` would make {name!r} both input and output; "
                      "every name has a single owner in the graph — write a new ArrayOut instead")
        if kind == "ArrayOut" and not is_mut:
            fail(pos, f"output {name!r} must be declared `mut {name}: ArrayOut[...]`")
        t = SCALARS[scalar] if scalar else ARRAYS[dtype]
        (outs if is_mut else ins).append((name, t))
        order.append(name)
    return Interface(tuple(ins), tuple(outs), tuple(order))


def generate(code: str, iface: Interface) -> str:
    """User code first (so compiler line N == cell line N), then prelude + wrapper."""
    body, slot = [], 0
    for name, t in iface.ins:
        if t.array:
            body.append(f"var {name} = {t.mojo}(__nb_slot(slots, {slot}), __nb_slot(slots, {slot + 1}))")
            slot += 2
        else:
            body.append(f"var {name} = Pointer[{t.mojo}, MutAnyOrigin]"
                        f"(unsafe_from_address=__nb_slot(slots, {slot})).unsafe_load()")
            slot += 1
    for i, (name, t) in enumerate(iface.outs):
        body.append(f"var {name} = {t.mojo.replace('ArrayIn', 'ArrayOut')}({i}, alloc_fn)" if t.array
                    else f"var {name}: {t.mojo} = 0")  # D-011: scalar outputs start at zero
    body.append(f"run({', '.join(iface.order)})")
    for name, t in iface.outs:
        if t.array:
            body += [f"if {name}._size < 0:", f"    raise Error(\"output '{name}' was never allocated\")"]
        else:
            body.append(f"Pointer[{t.mojo}, MutAnyOrigin](unsafe_from_address=__nb_slot(slots, {slot}))"
                        f".unsafe_store({name})")
            slot += 1
    inner = "\n".join("        " + b for b in body)
    wrapper = (f"\n\n@export\ndef nb_cell_entry(slots: Int, alloc_fn: __NbAllocFn) abi(\"C\") -> Int:\n"
               f"    try:\n{inner}\n        return 0\n    except e:\n        return __nb_report(e, alloc_fn)\n")
    return f"{code.rstrip()}\n\n{PRELUDE}{wrapper}"


def _map_errors(output: str, src: Path, user_lines: int) -> str:
    """Compiler diagnostics -> cell lines; errors in generated code get a hint."""
    diag = re.compile(rf"^{re.escape(str(src))}:(\d+):(\d+): error: (.*)$")
    lines, out, generated = output.splitlines(), [], False
    for i, line in enumerate(lines):
        m = diag.match(line)
        if not m:
            continue
        ln, col, msg = int(m.group(1)), m.group(2), m.group(3)
        if ln <= user_lines:
            out.append(f"line {ln}:{col}: error: {msg}")
            out += [f"    {l}" for l in lines[i + 1:i + 3] if not diag.match(l)]
        else:
            generated = True
            out.append(f"(generated wrapper): error: {msg}")
    if generated:
        out.append("hint: a name in the cell probably collides with the kernel prelude "
                   "(ArrayIn, ArrayOut, nb_cell_entry, __nb_*)")
    return "\n".join(out) or output.strip()


@contextlib.contextmanager
def _capture_fds(sink: list):
    """Capture fds 1 and 2 (Mojo prints bypass sys.stdout). Process-global (DEBT-007)."""
    sys.stdout.flush()
    sys.stderr.flush()
    with tempfile.TemporaryFile() as f:
        saved = [os.dup(1), os.dup(2)]
        os.dup2(f.fileno(), 1)
        os.dup2(f.fileno(), 2)
        try:
            yield
        finally:
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            for fd in saved:
                os.close(fd)
            f.seek(0)
            sink.append(f.read().decode(errors="replace"))


_AllocFn = ctypes.CFUNCTYPE(ctypes.c_ssize_t, ctypes.c_ssize_t, ctypes.c_ssize_t)


def _check_input(name: str, t: Type, v):
    if t.array:
        if not isinstance(v, np.ndarray):
            raise CellError(f"input {name!r}: expected numpy array of {t.np_dtype}, got {type(v).__name__}")
        problems = [p for bad, p in [(v.dtype != t.np_dtype, f"dtype {v.dtype} (expected {t.np_dtype})"),
                                     (v.ndim != 1, f"{v.ndim}-D (expected 1-D)"),
                                     (not v.flags.c_contiguous, "not contiguous"),
                                     (not v.flags.aligned, "not aligned")] if bad]
        if problems:
            raise CellError(f"input {name!r}: {', '.join(problems)}; "
                            f"use np.ascontiguousarray(x, dtype=np.{t.np_dtype}) in a Python cell")
        return v
    ok = (isinstance(v, (int, np.integer)) and not isinstance(v, (bool, np.bool_))) if t.mojo == "Int" \
        else isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_))
    if not ok:
        raise CellError(f"input {name!r}: expected {t.mojo}, got {type(v).__name__}")
    if t.mojo == "Int" and not -2**63 <= int(v) < 2**63:
        raise CellError(f"input {name!r}: {v} does not fit in Int (64-bit)")
    return t.ctype(v)


@dataclass(frozen=True)
class Artifact:
    so: str      # published path in the cache (never a partial file)
    loader: str  # "cdll" (GIL released) | "pydll" (GIL held), chosen by D-002


@functools.cache
def mojo_version() -> str:
    return subprocess.run(["mojo", "--version"], capture_output=True, text=True, check=True).stdout.strip()


def cache_key(code: str) -> str:
    return hashlib.sha256("\0".join([code, PRELUDE, mojo_version()]).encode()).hexdigest()[:24]


def _loader_for(so: Path) -> str:
    """D-002: the binary decides; fail-closed to PyDLL when unsure."""
    nm = shutil.which("nm")
    if not nm:
        return "pydll"
    r = subprocess.run([nm, "-D", "--undefined-only", str(so)], capture_output=True, text=True)
    return "cdll" if r.returncode == 0 and not _PY_SYMBOL.search(r.stdout) else "pydll"


async def build_async(code: str, cache_dir: Path) -> tuple[Artifact, bool]:
    """Compile a cell (or hit the disk cache). Returns (artifact, compiled_now).
    Cancellation kills the build's process group; only a successful build is
    published, via atomic rename (D-014)."""
    if not shutil.which("mojo"):
        raise CompileError("`mojo` not found on PATH: start the kernel through `pixi run`")
    cache_dir = Path(cache_dir).resolve()
    key = cache_key(code)
    so = cache_dir / f"{key}.so"
    if so.exists():
        return Artifact(str(so), _loader_for(so)), False
    cache_dir.mkdir(parents=True, exist_ok=True)
    src = cache_dir / f"{key}.mojo"
    src.write_text(generate(code, parse_interface(code)))
    tmp = cache_dir / f"{key}.{uuid.uuid4().hex}.tmp"
    proc = await asyncio.create_subprocess_exec(
        "mojo", "build", "-O3", "--emit", "shared-lib", str(src), "-o", str(tmp),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = await proc.communicate()
    except BaseException:  # cancelled (new edit, stop) or the caller is going away
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        await asyncio.shield(proc.wait())
        tmp.unlink(missing_ok=True)
        raise
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise CompileError(_map_errors(out.decode(errors="replace"), src, len(code.rstrip().splitlines())))
    os.replace(tmp, so)  # atomic publish; concurrent builds of the same key are harmless
    return Artifact(str(so), _loader_for(so)), True


def build(code: str, cache_dir: Path) -> tuple[Artifact, bool]:
    """Synchronous build for the in-process driver (CLI, tests)."""
    return asyncio.run(build_async(code, cache_dir))


_libs: dict[str, ctypes.CDLL] = {}


def load(artifact: dict) -> ctypes.CDLL:
    """dlopen by path (a new path per version; never importlib.reload, D-001)."""
    so = artifact["so"]
    if so not in _libs:
        lib = (ctypes.PyDLL if artifact["loader"] == "pydll" else ctypes.CDLL)(so)
        lib.nb_cell_entry.argtypes = [ctypes.c_void_p, _AllocFn]
        lib.nb_cell_entry.restype = ctypes.c_ssize_t
        _libs[so] = lib
    return _libs[so]


def call(lib: ctypes.CDLL, iface: Interface, values: dict) -> tuple[dict, str]:
    """Run the cell. Returns (outputs, captured stdout+stderr). Raises CellError."""
    keep, slots = [], []  # keep: every object whose address Mojo sees stays alive
    for name, t in iface.ins:
        v = _check_input(name, t, values[name])
        keep.append(v)
        slots += [v.ctypes.data, v.size] if t.array else [ctypes.addressof(v)]
    scalars = {}
    for name, t in iface.outs:
        if not t.array:
            scalars[name] = t.ctype(0)
            slots.append(ctypes.addressof(scalars[name]))
    arrays, err = {}, []

    def alloc(index, n):
        try:  # D-003: an exception escaping here would hand Mojo garbage
            if index == -1:
                buf = np.empty(max(n, 1), np.uint8)
                err.append(buf[:n])
                return buf.ctypes.data
            name, t = iface.outs[index]
            if not t.array or name in arrays or n < 0:
                return 0
            buf = np.empty(max(n, 1), t.np_dtype)  # never hand out address 0 for n == 0
            arrays[name] = buf[:n]
            return buf.ctypes.data
        except BaseException:
            return 0

    cb = _AllocFn(alloc)
    c_slots = (ctypes.c_ssize_t * max(len(slots), 1))(*slots)
    captured = []
    with _capture_fds(captured):
        rc = lib.nb_cell_entry(ctypes.addressof(c_slots), cb)
    del keep, cb
    if rc != 0:
        msg = bytes(err[0]).decode(errors="replace") if err else "Mojo cell failed without a message"
        raise CellError(msg, captured[0])
    return {**arrays, **{n: c.value for n, c in scalars.items()}}, captured[0]
