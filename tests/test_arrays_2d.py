"""2-D arrays and Scratch buffers in Mojo cells (D-024), through the real Session."""
import pytest

from kernel import mojo
from kernel.fmt import Cell
from tests.test_session import cache, needs_mojo, session_test, statuses  # noqa: F401 (fixture)

GRID = "import numpy as np\ngrid = np.arange(12, dtype=np.float64).reshape(3, 4)"


def test_interface_accepts_rank_2_and_rejects_others():
    i = mojo.parse_interface("def run(a: ArrayIn[DType.float64, 2], mut b: ArrayOut[DType.int32, 2],\n"
                             "        c: ArrayIn[DType.float32, 1]) raises:\n    pass")
    assert [(n, t.rank) for n, t in i.ins + i.outs] == [("a", 2), ("c", 1), ("b", 2)]
    with pytest.raises(mojo.InterfaceError, match="rank"):
        mojo.parse_interface("def run(a: ArrayIn[DType.float64, 3]):\n    pass")


@needs_mojo
@session_test
async def test_2d_round_trip_keeps_shape_and_values(s):
    s.load([Cell("python", GRID),
            Cell("mojo", "def run(grid: ArrayIn[DType.float64, 2], mut t: ArrayOut[DType.float64, 2],\n"
                         "        mut rows: Int) raises:\n"
                         "    rows = len(grid)\n"
                         "    t.alloc(grid.dim(1), grid.dim(0))\n"
                         "    for i in range(grid.dim(0)):\n"
                         "        for j in range(grid.dim(1)):\n"
                         "            t[j, i] = grid[i, j]"),
            Cell("python", "ok = t.shape == (4, 3) and bool(np.array_equal(t, grid.T))")])
    await s.idle()
    assert statuses(s) == ["ok"] * 3, [c.error for c in s.sched.cells.values()]
    assert s.sched.cells[2].previews["rows"]["repr"] == "3"
    assert s.sched.cells[3].previews["ok"]["repr"] == "True"


@needs_mojo
@session_test
async def test_inputs_are_rejected_before_mojo_unless_rank_and_layout_match(s):
    s.load([Cell("python", "import numpy as np\nflat = np.zeros(6)\nsquare = np.zeros((2, 3))\n"
                           "view = np.zeros((3, 2)).T"),
            Cell("mojo", "def run(flat: ArrayIn[DType.float64, 2], mut a: Int) raises:\n    a = len(flat)"),
            Cell("mojo", "def run(square: ArrayIn[DType.float64], mut b: Int) raises:\n    b = len(square)"),
            Cell("mojo", "def run(view: ArrayIn[DType.float64, 2], mut c: Int) raises:\n    c = len(view)")])
    await s.idle()
    errors = [s.sched.cells[cid].error for cid in (2, 3, 4)]
    assert statuses(s) == ["ok", "error", "error", "error"]
    assert "1-D (expected 2-D)" in errors[0]
    assert "2-D (expected 1-D)" in errors[1]
    assert "not contiguous" in errors[2]


@needs_mojo
@session_test
async def test_out_of_bounds_2d_access_raises_instead_of_crashing(s):
    s.load([Cell("python", GRID),
            Cell("mojo", "def run(grid: ArrayIn[DType.float64, 2], mut v: Float64) raises:\n"
                         "    v = grid[0, grid.dim(1)]"),
            Cell("mojo", "def run(grid: ArrayIn[DType.float64, 2], mut w: ArrayOut[DType.float64, 2]) raises:\n"
                         "    w.alloc(2, 2)\n    w[2, 0] = 1.0"),
            Cell("mojo", "def run(grid: ArrayIn[DType.float64, 2], mut z: Float64) raises:\n"
                         "    var tmp = Scratch[DType.float64, 2](2, 2)\n    z = tmp[0, -1]")])
    await s.idle()
    assert statuses(s) == ["ok", "error", "error", "error"]
    assert all("out of bounds" in s.sched.cells[cid].error for cid in (2, 3, 4))


@needs_mojo
@session_test
async def test_rank_misuse_is_a_compile_error_on_the_cell_line(s):
    s.load([Cell("python", GRID),
            Cell("mojo", "def run(grid: ArrayIn[DType.float64, 2], mut v: Float64) raises:\n"
                         "    v = grid[1]"),
            Cell("mojo", "def check[n: Int]():\n    comptime assert n > 0, \"n must be positive\"\n\n"
                         "def run(mut w: Int) raises:\n    check[0]()")])  # the cell's own assert
    await s.idle()
    c, own = s.sched.cells[2], s.sched.cells[3]
    assert c.status == own.status == "compile-error"
    assert "line 2:" in c.error and "2-D" in c.error and "generated wrapper" not in c.error
    assert "line 2:" in own.error and "n must be positive" in own.error and "generated wrapper" not in own.error


@needs_mojo
@session_test
async def test_2d_alloc_rules_and_zeroed_scratch(s):
    s.load([Cell("mojo", "def run(mut empty: ArrayOut[DType.int64, 2], mut total: Float64) raises:\n"
                         "    empty.alloc(0, 5)\n"
                         "    var a = Scratch[DType.float64, 2](3, 4)\n"
                         "    var b = Scratch[DType.float64, 2](3, 4)\n"
                         "    a[1, 2] = 7.0\n"
                         "    swap(a, b)\n"
                         "    for i in range(b.dim(0)):\n"
                         "        for j in range(b.dim(1)):\n"
                         "            total += b[i, j] + a[i, j]"),
            Cell("mojo", "def run(mut neg: ArrayOut[DType.float64, 2]) raises:\n    neg.alloc(-1, 3)"),
            Cell("mojo", "def run(mut huge: Float64) raises:\n"
                         "    var s = Scratch[DType.float64, 2](1 << 40, 1 << 40)\n    huge = s[0, 0]"),
            Cell("python", "shape = empty.shape")])
    await s.idle()
    assert statuses(s) == ["ok", "error", "error", "ok"], [c.error for c in s.sched.cells.values()]
    assert s.sched.cells[1].previews["total"]["repr"] == "7.0"
    assert s.sched.cells[4].previews["shape"]["repr"] == "(0, 5)"
    assert "cannot allocate" in s.sched.cells[2].error
    assert "too large" in s.sched.cells[3].error
