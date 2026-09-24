"""Each test pins the CURRENT behavior of a debt in docs/DEBTS.md.
When a debt is paid, its test must change along with it."""
import shutil
import subprocess
import sys
import threading

import pytest

from kernel.engine import Engine
from kernel.fmt import Cell, parse, serialize


def build(*codes):
    e = Engine()
    e.load([Cell("python", c) for c in codes])
    return e, list(e.cells)


# --- Mojo cell debts ---

needs_mojo = pytest.mark.skipif(not shutil.which("mojo"), reason="mojo not on PATH (run through pixi)")
CELL = """def run(mut n: Int) raises:
    n = VALUE"""


@needs_mojo
def test_debt_001_old_cell_libraries_stay_loaded(tmp_path):
    e = Engine(cache_dir=tmp_path)
    e.load([Cell("mojo", CELL.replace("VALUE", "1"))])
    (cid,) = e.cells
    e.edit(cid, CELL.replace("VALUE", "2"))
    assert e.ns["n"] == 2
    maps = open("/proc/self/maps").read()
    assert sum(str(so) in maps for so in tmp_path.glob("*.so")) == 2  # v1 still mapped


@needs_mojo
def test_debt_008_paid_cli_survives_a_crashing_mojo_cell(tmp_path):
    """DEBT-008 paid everywhere: the CLI now runs through the Session (review item 5)."""
    nbfile = tmp_path / "crash.nb.md"
    nbfile.write_text("```python\na = 1\n```\n\n```mojo\ndef run(mut n: Int) raises:\n"
                      "    Pointer[Int, MutAnyOrigin](unsafe_from_address=8).unsafe_store(1)\n```\n")
    r = subprocess.run([sys.executable, "-m", "kernel", "run", str(nbfile)], capture_output=True, text=True)
    assert r.returncode == 1 and "[1] ok" in r.stdout
    assert "[2] crashed" in r.stdout and "SIGSEGV" in r.stdout


def test_debt_009_interface_types_are_limited(tmp_path):
    e = Engine(cache_dir=tmp_path)
    e.load([Cell("mojo", "def run(m: ArrayIn2D[DType.float64]): pass"),
            Cell("mojo", "def run(s: String): pass")])
    assert [c.status for c in e.cells.values()] == ["syntax-error", "syntax-error"]


@needs_mojo
def test_debt_010_compile_blocks_the_caller(tmp_path):
    e = Engine(cache_dir=tmp_path)
    e.load([Cell("mojo", CELL.replace("VALUE", "7"))])
    (cid,) = e.cells
    assert e.compiles == 1 and e.cells[cid].status == "ok"  # load() returned only after mojo build


@needs_mojo
def test_debt_011_disk_cache_is_never_evicted(tmp_path):
    e = Engine(cache_dir=tmp_path)
    e.load([Cell("mojo", CELL.replace("VALUE", "1"))])
    (cid,) = e.cells
    for v in (2, 3):
        e.edit(cid, CELL.replace("VALUE", str(v)))
    assert len(list(tmp_path.glob("*.so"))) == 3


# --- engine debts ---

def test_debt_003_in_place_mutation_is_not_tracked():
    e, (a, m, r) = build("xs = [1]", "xs.append(2)", "n = len(xs)")
    assert e.ns["n"] == 2
    ran = e.edit(m, "xs.append(3)")
    assert ran == [m]                 # reader of xs does not rerun...
    assert e.ns["n"] == 2             # ...so n is stale
    assert e.ns["xs"] == [1, 2, 3]    # and the mutation accumulated across runs


def test_debt_004_dynamic_names_are_invisible_to_the_graph():
    e, (a, r) = build("globals()['x'] = 1", "y = x + 1")
    assert e.ns["y"] == 2
    e.delete(a)
    assert e.ns["x"] == 1             # never deleted: engine did not know a defined x
    assert e.cells[r].status == "ok"  # and r was not invalidated


def test_debt_005_global_assignment_inside_function_is_not_a_definition():
    e, (f, r) = build("def f():\n    global g\n    g = 1\nf()", "h = g + 1")
    assert e.ns["h"] == 2                       # works only by file-order luck
    e2, _ = build("h = g + 1", "def f():\n    global g\n    g = 1\nf()")
    assert e2.cells[1].status == "error"        # no edge -> runs before f() and fails


def test_debt_006_adjacent_markdown_cells_merge_on_save():
    cells = [Cell("markdown", "a"), Cell("markdown", "b")]
    assert parse(serialize(cells)) == [Cell("markdown", "a\n\nb")]


def test_debt_007_thread_print_after_cell_escapes_capture(capsys):
    e = Engine()
    e.ns["GO"], e.ns["DONE"] = threading.Event(), threading.Event()
    e.load([Cell("python", "import threading\n"
            "def late():\n    GO.wait()\n    print('late')\n    DONE.set()\n"
            "threading.Thread(target=late).start()\nprint('now')")])
    cell = next(iter(e.cells.values()))
    e.ns["GO"].set()
    e.ns["DONE"].wait(5)
    assert cell.output == "now\n"                    # 'late' is not attributed to the cell
    assert "late" in capsys.readouterr().out         # it leaks to the process stdout
