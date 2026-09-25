"""The pure state machine (D-012): events in, actions out. No processes, no compiler."""
from kernel.scheduler import Cancel, Compile, Delete, Exec, Kill, Restart, Scheduler

OK = {"status": "ok", "error": "", "output": "", "previews": {}}
MOJO = "def run(xs: ArrayIn[DType.float64], mut total: Float64) raises:\n    pass"


def kinds(actions):
    return [type(a).__name__ for a in actions]


def execs(actions):
    return [a.cid for a in actions if isinstance(a, Exec)]


def finish(s, actions):
    """Complete every Exec as ok, synchronously; returns the run order."""
    order = []
    pending = [a for a in actions if isinstance(a, Exec)]
    while pending:
        cid = pending.pop(0).cid
        order.append(cid)
        pending += [a for a in s.ran(cid, OK) if isinstance(a, Exec)]
    return order


def sched(*cells):
    s = Scheduler()
    return s, [s.add(code, kind) for kind, code in cells]


def test_edit_does_not_run_and_marks_modified():
    s, (a, b) = sched(("python", "a = 1"), ("python", "b = a"))
    finish(s, s.run_all())
    assert s.edit(a, "a = 2") == []
    assert s.cells[a].status == "modified" and s.cells[b].status == "ok"


def test_explicit_run_propagates_to_descendants():
    s, (a, b, c) = sched(("python", "a = 1"), ("python", "b = a"), ("python", "c = 3"))
    finish(s, s.run_all())
    s.edit(a, "a = 2")
    assert finish(s, s.run(a)) == [a, b]


def test_only_one_exec_at_a_time():
    s, (a, b) = sched(("python", "a = 1"), ("python", "z = 2"))
    acts = s.run_all()
    assert execs(acts) == [a]
    assert s.cells[b].status == "stale"
    assert execs(s.ran(a, OK)) == [b]


def test_running_parent_does_not_block_child():
    s, (a, b) = sched(("python", "a = 1"), ("python", "b = a"))
    acts = s.run_all()
    assert execs(acts) == [a] and s.cells[b].status == "stale"  # waits, not blocked


def test_independent_mojo_cells_compile_in_parallel():
    s, (d, m1, m2) = sched(("python", "import numpy as np\nxs = np.ones(3)"),
                           ("mojo", MOJO), ("mojo", MOJO.replace("total", "other")))
    acts = s.run_all()
    assert [a.cid for a in acts if isinstance(a, Compile)] == [m1, m2]  # both before any result
    assert s.cells[m1].status == s.cells[m2].status == "compiling"


def test_python_cell_runs_while_mojo_compiles_and_readers_wait():
    s, (d, m, r, free) = sched(("python", "xs = 1"), ("mojo", MOJO), ("python", "t = total"), ("python", "q = 1"))
    acts = s.run_all()
    assert execs(acts) == [d]
    assert execs(s.ran(d, OK)) == [free]      # m still compiling: independent q goes first
    assert s.cells[r].status == "stale"       # reader of m's output waits
    assert s.ran(free, OK) == []              # nothing runnable until the build lands
    assert execs(s.compiled(m, s.cells[m].code, artifact={"so": "x", "loader": "cdll"})) == [m]
    assert execs(s.ran(m, OK)) == [r]


def test_edit_cancels_build_and_obsolete_result_is_ignored():
    s, (d, m) = sched(("python", "xs = 1"), ("mojo", MOJO))
    s.run_all()
    old = s.cells[m].code
    acts = s.edit(m, MOJO + "\n    # v2")
    assert kinds(acts) == ["Cancel", "Compile"]   # still queued: rebuild the new code
    assert s.compiled(m, old, artifact={"so": "old", "loader": "cdll"}) == []  # obsolete: dropped
    assert s.cells[m].compiling == MOJO + "\n    # v2"


def test_compile_error_blocks_readers():
    s, (d, m, r) = sched(("python", "xs = 1"), ("mojo", MOJO), ("python", "t = total"))
    finish(s, s.run_all())
    acts = s.compiled(m, s.cells[m].code, error="line 2: boom")
    assert s.cells[m].status == "compile-error" and s.cells[r].status == "blocked"
    assert "[2] compile-error" in s.cells[r].error


def test_crash_quarantines_restarts_and_reruns_the_rest():
    s, (a, b, c, d, e) = sched(("python", "a = 1"), ("python", "b = a"), ("python", "c = a"),
                               ("python", "d = c"), ("python", "e = 5"))
    finish(s, s.run_all())
    s.edit(c, "c = a  # crashes")
    acts = s.run(c)
    assert execs(acts) == [c]
    acts = s.kernel_died("SIGSEGV")
    assert isinstance(acts[0], Restart)
    assert s.cells[c].status == "crashed" and "cell [3]" in s.cells[c].error and "SIGSEGV" in s.cells[c].error
    assert finish(s, acts) == [a, b, e]       # everything that had run, minus c and its reader
    assert s.cells[d].status == "blocked"
    assert all(s.cells[x].status == "ok" for x in (a, b, e))


def test_crash_with_two_bad_cells_restarts_twice_then_settles():
    s, (a, x, y) = sched(("python", "a = 1"), ("python", "x = a"), ("python", "y = a"))
    acts = s.run_all()
    acts = s.ran(a, OK)                 # x starts
    acts = s.kernel_died("SIGSEGV")     # x crashes -> restart, rerun a then y
    assert kinds(acts)[0] == "Restart" and execs(acts) == [a]
    acts = s.ran(a, OK)
    assert execs(acts) == [y]
    acts = s.kernel_died("SIGSEGV")     # y crashes too -> second restart, rerun a only
    assert kinds(acts)[0] == "Restart"
    assert finish(s, acts) == [a]
    assert [s.cells[c].status for c in (a, x, y)] == ["ok", "crashed", "crashed"]


def test_crash_message_lists_upstream_unsafe_cells():  # review item 1
    s, (a, u, safe, victim) = sched(
        ("python", "import numpy as np\nxs = np.ones(4)"),
        ("mojo", "def run(xs: ArrayIn[DType.float64], mut ys: ArrayOut[DType.float64]) raises:\n"
                 "    ys.alloc(1)\n    ys.unsafe_set(9999, 1.0)  # corrupts\n    _ = xs.unsafe_ptr()"),
        ("python", "z = xs.sum()"),
        ("python", "w = ys[0] + z"))
    s.cells[u].artifact_code = s.cells[u].code  # pretend built
    finish(s, s.run_all())
    s.edit(victim, "w = ys[0] + z + 0")
    s.run(victim)
    s.kernel_died("SIGSEGV")
    msg = s.cells[victim].error
    assert "while running cell [4]" in msg
    assert "[2] (unsafe_ptr, unsafe_set)" in msg
    assert "[1]" not in msg and "[3]" not in msg  # upstream but no unsafe_*: not listed


def test_crash_message_says_when_no_upstream_unsafe():
    s, (a, b) = sched(("python", "a = 1"), ("python", "b = a"))
    finish(s, s.run_all())
    s.run(b)
    s.kernel_died("SIGSEGV")
    assert "No upstream cell uses unsafe_*" in s.cells[b].error


def test_stop_interrupts_with_distinct_message():
    s, (a, loop, r) = sched(("python", "a = 1"), ("python", "while True: pass\nb = a"), ("python", "c = b"))
    acts = s.run_all()
    s.ran(a, OK)
    assert s.running.cid == loop
    assert kinds(s.stop()) == ["Kill"]
    acts = s.kernel_died("SIGKILL")
    assert s.cells[loop].status == "interrupted" and "stop button" in s.cells[loop].error
    assert "died" not in s.cells[loop].error
    assert finish(s, acts) == [a] and s.cells[r].status == "blocked"


def test_explicit_run_lifts_quarantine_propagation_does_not():
    s, (a, x) = sched(("python", "a = 1"), ("python", "x = a"))
    s.run_all()
    s.ran(a, OK)
    finish(s, s.kernel_died("SIGSEGV"))          # x crashed
    finish(s, s.run(a))                           # upstream re-run: x stays quarantined
    assert s.cells[x].status == "crashed"
    assert execs(s.run(x)) == [x]                 # explicit run: it goes again


def test_stop_with_nothing_running_is_noop():
    s, _ = sched(("python", "a = 1"))
    finish(s, s.run_all())
    assert s.stop() == []


def test_orphan_deaths_give_up_after_three():
    s, (a,) = sched(("python", "a = 1"))
    finish(s, s.run_all())
    s.queue.clear()
    results = []
    for _ in range(4):  # deaths with nothing running, before any cell completes
        results.append(s.kernel_died("SIGSEGV"))
        s.running = None
    assert all(isinstance(r[0], Restart) for r in results[:3])
    assert results[3] == [] and s.kernel_dead


def test_delete_cell_drops_its_names_and_reruns_readers():
    s, (a, b) = sched(("python", "a = 1"), ("python", "b = a"))
    finish(s, s.run_all())
    acts = s.delete(a)
    assert isinstance(acts[0], Delete) and acts[0].names == ["a"]
    assert execs(acts) == [b]


def test_delete_cancels_build():
    s, (d, m) = sched(("python", "xs = 1"), ("mojo", MOJO))
    s.run_all()
    assert isinstance(s.delete(m)[0], Cancel)


# ---------------- `modified` (review item 6) ----------------

def chain():
    s, ids = sched(("python", "a = 1"), ("python", "b = a"), ("python", "c = b"), ("python", "z = 9"))
    finish(s, s.run_all())
    for cid in ids:
        s.cells[cid].previews = {"v": cid}
    s.changed.clear()
    return s, ids


def test_edit_marks_modified_and_flags_all_descendants_without_touching_them():
    s, (a, b, c, z) = chain()
    s.edit(a, "a = 2")
    assert s.cells[a].status == "modified"
    assert [s.cells[x].status for x in (b, c, z)] == ["ok", "ok", "ok"]      # values stay visible
    assert s.cells[b].previews == {"v": b}
    assert s.upstream_modified(b) == [a] and s.upstream_modified(c) == [a]   # transitive
    assert s.upstream_modified(z) == [] and s.upstream_modified(a) == []
    assert {a, b, c} <= s.changed and z not in s.changed                      # broadcast the flags


def test_editing_back_to_the_executed_code_restores_status_and_clears_flags():
    s, (a, b, c, z) = chain()
    s.edit(a, "a = 2")
    s.changed.clear()
    s.edit(a, "a = 1")
    assert s.cells[a].status == "ok" and s.upstream_modified(c) == []
    assert {a, b, c} <= s.changed


def test_editing_back_restores_an_error_status_too():
    s, (a,) = sched(("python", "a = 1 / 0"))
    acts = s.run_all()
    s.ran(a, {"status": "error", "error": "ZeroDivisionError", "output": "", "previews": {}})
    s.edit(a, "a = 1")
    assert s.cells[a].status == "modified"
    s.edit(a, "a = 1 / 0")
    assert (s.cells[a].status, s.cells[a].error) == ("error", "ZeroDivisionError")


def test_running_the_modified_cell_clears_the_flags():
    s, (a, b, c, z) = chain()
    s.edit(a, "a = 2")
    assert finish(s, s.run(a)) == [a, b, c]
    assert s.cells[a].status == "ok" and s.upstream_modified(c) == []


def test_modified_parent_does_not_block_an_explicit_run_of_its_child():
    s, (a, b, c, z) = chain()
    s.edit(a, "a = 2")
    assert finish(s, s.run(b)) == [b, c]          # uses a's old values, as before
    assert s.cells[a].status == "modified" and s.upstream_modified(b) == [a]


def test_never_executed_cell_edited_stays_idle():
    s, (a,) = sched(("python", "a = 1"))
    s.edit(a, "a = 2")
    assert s.cells[a].status == "idle"


def test_edit_while_running_ends_modified():
    s, (a,) = sched(("python", "a = 1"))
    s.run_all()                                   # a is running
    s.edit(a, "a = 2")
    assert s.cells[a].status == "running"
    s.ran(a, OK)
    assert s.cells[a].status == "modified"        # it ran the old code


# ---------------- speculative builds (review item 7) ----------------

def test_speculate_builds_without_touching_status():
    s, (d, m) = sched(("python", "xs = 1"), ("mojo", MOJO))
    acts = s.speculate(m)
    assert kinds(acts) == ["Compile"] and s.cells[m].status == "idle" and s.cells[m].compiling == MOJO
    assert s.speculate(m) == []                                       # same code already building
    s.compiled(m, MOJO, artifact={"so": "x", "loader": "cdll"})
    assert s.cells[m].artifact_code == MOJO and s.cells[m].status == "idle"
    assert s.speculate(m) == []                                       # already built


def test_speculative_error_is_a_diagnostic_not_a_result():
    s, (d, m, r) = sched(("python", "xs = 1"), ("mojo", MOJO), ("python", "t = total"))
    s.speculate(m)
    s.compiled(m, MOJO, error="line 2:5: error: boom",
               diagnostics=[{"line": 2, "col": 5, "message": "boom"}])
    assert s.cells[m].status == "idle" and s.cells[m].error == ""
    assert s.cells[m].diagnostics == [{"line": 2, "col": 5, "message": "boom", "source": "compile"}]
    assert s.cells[r].status == "idle"                                # nothing blocked


def test_run_during_speculative_build_waits_for_it():
    s, (d, m) = sched(("python", "xs = 1"), ("mojo", MOJO))
    finish(s, s.run(d))  # m's input must exist, or m is (correctly) blocked
    s.speculate(m)
    acts = s.run(m)
    assert [a for a in acts if isinstance(a, Compile)] == []          # no second build
    assert s.cells[m].status == "compiling"
    assert execs(s.compiled(m, MOJO, artifact={"so": "x", "loader": "cdll"})) == [m]


def test_interface_diagnostics_follow_every_edit_at_once():
    s, (m,) = sched(("mojo", MOJO))
    s.edit(m, MOJO.replace("mut total: Float64", "mut total: Complex"))
    (d,) = s.cells[m].diagnostics
    assert d["source"] == "interface" and d["line"] == 1 and "Complex" in d["message"]
    s.edit(m, MOJO)
    assert s.cells[m].diagnostics == []


def test_compile_diagnostics_are_replaced_only_when_a_build_finishes():  # review, item 3
    s, (m,) = sched(("mojo", MOJO))
    s.speculate(m)
    s.compiled(m, MOJO, error="e", diagnostics=[{"line": 2, "col": 3, "message": "old"}])
    v2 = MOJO + "\n    # v2"
    s.edit(m, v2)                                     # an edit does not clear them
    assert [d["message"] for d in s.cells[m].diagnostics] == ["old"]
    s.speculate(m)                                    # nor does the start of a build
    assert s.cells[m].compiling == v2 and [d["message"] for d in s.cells[m].diagnostics] == ["old"]
    s.compiled(m, MOJO, error="obsolete", diagnostics=[{"line": 9, "col": 1, "message": "stale"}])
    assert [d["message"] for d in s.cells[m].diagnostics] == ["old"]   # obsolete build: ignored
    s.compiled(m, v2, artifact={"so": "x", "loader": "cdll"})          # this build finishing replaces them
    assert s.cells[m].diagnostics == []
