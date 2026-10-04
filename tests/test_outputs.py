"""Rich outputs (D-025): figures, array statistics, function signatures and run time,
through the real Session."""
import base64

from kernel.fmt import Cell
from tests.test_session import cache, session_test, statuses  # noqa: F401 (fixture)

PNG = b"\x89PNG\r\n\x1a\n"


@session_test
async def test_figures_become_png_images_and_are_closed_after_the_cell(s):
    s.load([Cell("python", "import matplotlib.pyplot as plt\n"
                           "plt.plot([1, 3, 2])\nplt.figure()\nplt.imshow([[0, 1], [1, 0]])\nplt.show()"),
            Cell("python", "after = len(plt.get_fignums())")])
    await s.idle()
    assert statuses(s) == ["ok", "ok"]
    first = s.sched.cells[1]
    assert len(first.images) == 2 and all(base64.b64decode(i).startswith(PNG) for i in first.images)
    assert first.output == ""  # plt.show() under Agg must not warn into the cell output
    assert s.sched.cells[2].images == [] and s.sched.cells[2].previews["after"]["repr"] == "0"


@session_test
async def test_too_many_figures_are_capped_with_a_note(s):
    s.load([Cell("python", "import matplotlib.pyplot as plt\nfor _i in range(10):\n    plt.figure()")])
    await s.idle()
    c = s.sched.cells[1]
    assert c.status == "ok" and len(c.images) == 8 and "2 figure(s) not shown" in c.output


@session_test
async def test_array_previews_carry_statistics_and_a_2d_corner(s):
    s.load([Cell("python", "import numpy as np\nv = np.array([3.0, -1.0, 4.0])\n"
                           "m = np.arange(100, dtype=np.int64).reshape(10, 10)\nempty = np.zeros(0)\n"
                           "def diffuse(T, r, steps=10):\n    return T")])
    await s.idle()
    p = s.sched.cells[1].previews
    assert (p["v"]["min"], p["v"]["max"], p["v"]["mean"]) == (-1.0, 4.0, 2.0)
    assert p["m"]["rows"][0][:3] == [0, 1, 2] and len(p["m"]["rows"]) == 6 and len(p["m"]["rows"][0]) == 8
    assert "min" not in p["empty"]
    assert p["diffuse"] == {"type": "function", "repr": "diffuse(T, r, steps=10)"}


@session_test
async def test_a_result_line_larger_than_64k_does_not_hang_the_session(s):
    # 60k non-ASCII chars fit the output limit but become a ~360 KB JSON line (é escapes):
    # the kernel reader used asyncio's default 64 KiB line limit and died silently.
    s.load([Cell("python", "print('é' * 60_000)")])
    await s.idle(timeout=20)
    c = s.sched.cells[1]
    assert c.status == "ok" and len(c.output.strip()) == 60_000


@session_test
async def test_run_time_is_reported_per_cell(s):
    s.load([Cell("python", "import time as _t\n_t.sleep(0.05)\nx = 1")])
    await s.idle()
    assert s.sched.cells[1].duration_ms >= 50
