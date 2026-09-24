"""Mojo cells in the graph (D-002, D-003, D-004, D-005, D-009). Needs `mojo` (pixi run)."""
import ctypes
import shutil

import numpy as np
import pytest

from kernel import mojo
from kernel.engine import Engine
from kernel.fmt import Cell

pytestmark = pytest.mark.skipif(not shutil.which("mojo"), reason="mojo not on PATH (run through pixi)")

SCALE = """def run(xs: ArrayIn[DType.float64], k: Float64,
        mut ys: ArrayOut[DType.float64], mut total: Float64) raises:
    ys.alloc(len(xs))
    for i in range(len(xs)):
        ys[i] = xs[i] * k
        total += ys[i]
    print("scaled", len(xs))"""


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    return tmp_path_factory.mktemp("nbcache")  # shared: each distinct cell compiles once


def nb(cache, *cells):
    e = Engine(cache_dir=cache)
    e.load([Cell(k, c) for k, c in cells])
    return e, list(e.cells)


def flow(cache):
    return nb(cache,
              ("python", "import numpy as np\nxs = np.arange(4, dtype=np.float64)"),
              ("python", "k = 2.0"),
              ("mojo", SCALE),
              ("python", "doubled_total = total * 2\nlast = ys[-1]"))


# --- interface (D-010: derived from run()'s signature) ---

def test_interface_from_signature():
    i = mojo.parse_interface(SCALE)
    assert [n for n, _ in i.ins] == ["xs", "k"] and [n for n, _ in i.outs] == ["ys", "total"]
    assert i.order == ("xs", "k", "ys", "total")


def test_interface_allows_helpers_structs_interleaved_params_and_shorthand():
    code = """struct Acc:
    var v: Float64
    def __init__(out self):
        self.v = 0
    def run(self) -> Int:  # indented: a method, not the cell entry
        return 1

def helper(x: Float64) -> Float64:
    return x * 2

def run(
    mut n: Int,
    xs: ArrayIn[.int32],
    mut ys: ArrayOut[DType.int32],
):
    n = len(xs)
    ys.alloc(0)"""
    i = mojo.parse_interface(code)
    assert i.order == ("n", "xs", "ys") and [n for n, _ in i.ins] == ["xs"]
    assert [n for n, _ in i.outs] == ["n", "ys"]


@pytest.mark.parametrize("code, msg", [
    ("def helper(): pass", "exactly one top-level `def run(...)`"),
    ("def run(): pass\ndef run(): pass", "exactly one"),
    ("def run(x: Float16): pass", "unsupported type 'Float16'"),
    ("def run(x: ArrayIn[DType.bool]): pass", "unsupported dtype 'bool'"),
    ("def run(mut xs: ArrayIn[DType.float64]): pass", "both input and output"),
    ("def run(ys: ArrayOut[DType.float64]): pass", "must be declared `mut ys"),
    ("def run(x: Int, x: Int): pass", "appears twice"),
    ("def run(_x: Int): pass", "private"),
    ("def run(x: Int = 3): pass", "default values are not allowed"),
    ("def run(Int): pass", "cannot read parameter"),
    ("def run(x: Int) -> Int:\n    return x", "only `raises` and `:`"),
    ("def run[T: AnyType](x: Int): pass", "expected `(`"),
    ("def run(x: Tuple(Int)): pass", "parentheses are not allowed"),
    ("def run(  # a comment does not hide an error\n    x: Int = 3,\n): pass", "default values"),
])
def test_signature_outside_grammar_is_clear_error(cache, code, msg):
    e, (c,) = nb(cache, ("mojo", code))
    err = e.cells[c].error
    assert e.cells[c].status == "syntax-error" and msg in err and "accepted form" in err
    assert "line" in err or "exactly one" in msg  # no line to point at when run is missing
    assert e.mojo.compiles == 0  # never reaches the compiler


def test_end_of_line_comments_inside_parameters():  # review of part A, item 1
    code = """def run(  # inputs first (see D-010)
    xs: ArrayIn[DType.float64],   # the data; a ')' in a comment must not close the list
    k: Float64,                   # mut z: ArrayIn[DType.int32]  <- ignored, it's a comment
    mut ys: ArrayOut[DType.float64],  # output
    # a full-line comment
    mut total: Float64,           # trailing comma + comment
) raises:  # after the signature too
    pass"""
    i = mojo.parse_interface(code)
    assert i.order == ("xs", "k", "ys", "total")
    assert [n for n, _ in i.ins] == ["xs", "k"]


def test_comments_keep_error_line_numbers(cache):
    e, (c,) = nb(cache, ("mojo", "def run(  # (first)\n    a: Int,  # ok\n    b: Complex,  # bad\n): pass"))
    assert "line 3:" in e.cells[c].error and "Complex" in e.cells[c].error


def test_comments_in_parameters_compile_and_run(cache):
    code = """def run(
    k: Int,       # input )
    mut out: Int, # output
) raises:
    out = k * 2"""
    e, (_, c) = nb(cache, ("python", "k = 21"), ("mojo", code))
    assert e.cells[c].status == "ok" and e.ns["out"] == 42


def test_error_line_points_at_offending_parameter(cache):
    e, (c,) = nb(cache, ("mojo", "# comment\ndef run(\n    a: Int,\n    b: Complex,\n): pass"))
    assert "line 4:" in e.cells[c].error


# --- scalar outputs (D-011: start at zero on every call) ---

def test_unassigned_scalar_output_is_zero(cache):
    code = """def run(mut n: Int, mut x: Float64) raises:
    pass"""
    e, _ = nb(cache, ("mojo", code))
    assert (e.ns["n"], e.ns["x"]) == (0, 0.0)


def test_scalar_output_does_not_carry_across_reruns(cache):
    code = """def run(k: Int, mut acc: Int) raises:
    acc += k"""
    e, (p, _) = nb(cache, ("python", "k = 5"), ("mojo", code))
    assert e.ns["acc"] == 5
    e.edit(p, "k = 5")
    assert e.ns["acc"] == 5  # fresh zero each run, not 10


# --- data flow ---

def test_python_to_mojo_to_python(cache):
    e, (_, _, m, down) = flow(cache)
    assert [e.cells[c].status for c in e.cells] == ["ok"] * 4
    assert np.array_equal(e.ns["ys"], [0, 2, 4, 6]) and e.ns["total"] == 12.0
    assert e.ns["doubled_total"] == 24.0 and e.ns["last"] == 6.0
    assert e.cells[m].output == "scaled 4\n"  # Mojo print captured at fd level


def test_output_array_is_plain_python_owned_ndarray(cache):
    e, _ = flow(cache)
    ys = e.ns["ys"]
    assert type(ys) is np.ndarray and ys.dtype == np.float64 and ys.flags.writeable
    ys[0] = 99.0  # writable, no Mojo ownership involved


def test_new_upstream_data_reruns_without_recompiling(cache):  # D-004
    e, (data, _, m, down) = flow(cache)
    before = e.mojo.compiles
    ran = e.edit(data, "import numpy as np\nxs = np.arange(10, dtype=np.float64)")
    assert ran == [data, m, down] and e.mojo.compiles == before
    assert e.ns["total"] == 2 * 45


def test_parameter_from_python_cell_does_not_recompile(cache):  # D-005
    e, (_, k, m, _) = flow(cache)
    before = e.mojo.compiles
    e.edit(k, "k = 10.0")
    assert e.ns["total"] == 60.0 and e.mojo.compiles == before


def test_disk_cache_survives_new_engine(cache):
    flow(cache)
    e, _ = flow(cache)
    assert e.mojo.compiles == 0


def test_editing_mojo_code_recompiles_and_reruns_descendants(cache):
    e, (_, _, m, down) = flow(cache)
    before = e.mojo.compiles
    ran = e.edit(m, SCALE.replace("xs[i] * k", "xs[i] * k + 1.0"))
    assert ran == [m, down] and e.mojo.compiles == before + 1
    assert e.ns["total"] == 16.0


# --- loader (D-002) ---

def test_buffer_only_cell_is_loaded_with_gil_released(cache):
    e, (_, _, m, _) = flow(cache)
    lib = e.mojo.load(SCALE, mojo.parse_interface(SCALE))
    assert not isinstance(lib, ctypes.PyDLL)


def test_cell_using_python_is_loaded_with_pydll_and_works(cache):
    code = """from std.python import Python
def run(mut n: Int) raises:
    n = Int(py=Python.import_module("builtins").len(Python.list(1, 2, 3)))"""
    e, (c,) = nb(cache, ("mojo", code))
    assert e.cells[c].status == "ok" and e.ns["n"] == 3
    assert isinstance(e.mojo.load(code, mojo.parse_interface(code)), ctypes.PyDLL)


# --- errors ---

def test_compile_error_maps_to_cell_line_and_blocks_descendants(cache):
    bad = SCALE.replace("        total += ys[i]", "        total += undefined_name")
    e, (_, _, m, down) = flow_with(cache, bad)
    err = e.cells[m].error
    assert e.cells[m].status == "compile-error" and "line 6:" in err and "undefined_name" in err
    assert ".mojo:" not in err  # no generated-file paths leak
    assert e.cells[down].status == "blocked" and "ys" not in e.ns


def test_type_error_inside_run_maps_to_cell_line(cache):
    bad = SCALE.replace("        total += ys[i]", "        total += len(xs)")  # Int into Float64
    e, (_, _, m, _) = flow_with(cache, bad)
    assert e.cells[m].status == "compile-error" and "line 6:" in e.cells[m].error


def test_collision_with_prelude_gets_hint(cache):
    e, (c,) = nb(cache, ("mojo", "struct ArrayIn:\n    pass\ndef run(mut n: Int):\n    n = 1"))
    assert e.cells[c].status == "compile-error" and "collides with the kernel prelude" in e.cells[c].error


def flow_with(cache, mojo_code):
    return nb(cache,
              ("python", "import numpy as np\nxs = np.arange(4, dtype=np.float64)"),
              ("python", "k = 2.0"),
              ("mojo", mojo_code),
              ("python", "doubled_total = total * 2\nlast = ys[-1]"))


def test_runtime_error_in_mojo_is_cell_error_and_leaves_no_outputs(cache):
    code = SCALE.replace('    print("scaled", len(xs))', '    if k > 5.0:\n        raise Error("k too big")')
    e, (_, k, m, down) = flow_with(cache, code)
    assert e.cells[m].status == "ok"
    e.edit(k, "k = 6.0")
    assert e.cells[m].status == "error" and "k too big" in e.cells[m].error
    assert e.cells[down].status == "blocked" and "ys" not in e.ns and "total" not in e.ns


def test_out_of_bounds_access_raises_instead_of_corrupting(cache):
    code = SCALE.replace("    ys.alloc(len(xs))", "    ys.alloc(len(xs))\n    _ = xs[len(xs)]")
    e, (_, _, m, _) = flow_with(cache, code)
    assert e.cells[m].status == "error" and "out of bounds" in e.cells[m].error


def test_output_never_allocated_is_error(cache):
    code = """def run(mut ys: ArrayOut[DType.float64]) raises:
    pass"""
    e, (c,) = nb(cache, ("mojo", code))
    assert e.cells[c].status == "error" and "never allocated" in e.cells[c].error


def test_zero_length_output(cache):
    code = """def run(mut ys: ArrayOut[DType.int32]) raises:
    ys.alloc(0)"""
    e, _ = nb(cache, ("mojo", code))
    assert e.ns["ys"].shape == (0,) and e.ns["ys"].dtype == np.int32


# --- input validation at the trust boundary (pays DEBT-002) ---

@pytest.mark.parametrize("xs_code, msg", [
    ("np.arange(8, dtype=np.float64)[::2]", "not contiguous"),
    ("np.arange(4, dtype=np.float32)", "dtype float32"),
    ("np.zeros((2, 2))", "2-D"),
    ("[1.0, 2.0]", "expected numpy array"),
])
def test_invalid_array_inputs_are_rejected_before_mojo_sees_a_pointer(cache, xs_code, msg):
    e, (_, m) = nb(cache, ("python", f"import numpy as np\nxs = {xs_code}\nk = 2.0"), ("mojo", SCALE))
    assert e.cells[m].status == "error" and msg in e.cells[m].error, e.cells[m].error
    assert "ys" not in e.ns


@pytest.mark.parametrize("k, ok", [("2", True), ("np.float32(2)", True), ("True", False), ("'2'", False)])
def test_scalar_inputs_are_type_checked(cache, k, ok):
    e, (_, m) = nb(cache, ("python", f"import numpy as np\nxs = np.arange(4, dtype=np.float64)\nk = {k}"),
                   ("mojo", SCALE))
    assert (e.cells[m].status == "ok") is ok, e.cells[m].error


def test_int_scalar_rejects_float_and_overflow(cache):
    code = """def run(n: Int, mut m: Int) raises:
    m = n + 1"""
    for value, ok in [("41", True), ("41.0", False), ("2**63", False)]:
        e, (_, c) = nb(cache, ("python", f"n = {value}"), ("mojo", code))
        assert (e.cells[c].status == "ok") is ok, (value, e.cells[c].error)
    assert e.ns.get("m") is None


def test_missing_input_name(cache):
    e, (c,) = nb(cache, ("mojo", SCALE))
    assert e.cells[c].status == "error" and "xs" in e.cells[c].error and "k" in e.cells[c].error


def test_unsafe_get_set_match_checked_access(cache):  # review A3: unchecked access, unsafe_ prefix
    code = """def run(xs: ArrayIn[DType.int64], mut a: ArrayOut[DType.int64], mut b: ArrayOut[DType.int64]) raises:
    var n = len(xs)
    a.alloc(n)
    b.alloc(n)
    for i in range(n):
        a[i] = xs[Int(xs[i]) % n]
        b.unsafe_set(i, xs.unsafe_get(Int(xs.unsafe_get(i)) % n))
        b.unsafe_set(i, b.unsafe_get(i) + 0)  # ArrayOut.unsafe_get reads back what was set"""
    e, _ = nb(cache, ("python", "import numpy as np\nxs = np.array([3, 0, 2, 1, 7], dtype=np.int64)"),
              ("mojo", code))
    assert np.array_equal(e.ns["a"], e.ns["b"]) and list(e.ns["a"]) == [1, 3, 2, 0, 2]


def test_unsafe_ptr_gives_stdlib_pointer_for_simd(cache):  # review of part A, item 2
    code = """def run(xs: ArrayIn[DType.float64], mut ys: ArrayOut[DType.float64]) raises:
    var n = len(xs)
    ys.alloc(n)
    var p = xs.unsafe_ptr()
    var q = ys.unsafe_ptr()
    var i = 0
    while i + 4 <= n:
        q.unsafe_store(i, p.unsafe_load[width=4](i) * 2.0)
        i += 4
    while i < n:
        q.unsafe_store(i, p.unsafe_load(i) * 2.0)
        i += 1"""
    e, _ = nb(cache, ("python", "import numpy as np\nxs = np.arange(10, dtype=np.float64)"), ("mojo", code))
    assert np.array_equal(e.ns["ys"], np.arange(10) * 2.0)  # 2 SIMD chunks + 2-element tail
