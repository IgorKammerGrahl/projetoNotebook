from kernel.engine import Engine
from kernel.fmt import Cell


def build(*codes):
    e = Engine()
    e.load([Cell("python", c) for c in codes])
    return e, list(e.cells)


def status(e):
    return [c.status for c in e.cells.values()]


def test_runs_in_topological_order_not_file_order():
    e, ids = build("c = b + 1", "b = a + 1", "a = 1")
    assert e.ns["c"] == 3
    e2 = Engine()
    assert e2.load([Cell("python", c) for c in ["c = b + 1", "b = a + 1", "a = 1"]]) == [3, 2, 1]


def test_independent_cells_keep_file_order():
    e = Engine()
    assert e.load([Cell("python", c) for c in ["x = 1", "y = 2", "z = x + y"]]) == [1, 2, 3]


def test_edit_reruns_cell_and_descendants_only():
    e, (a, b, c, d) = build("a = 1", "b = a * 10", "c = b + 1", "d = 100")
    assert e.edit(a, "a = 2") == [a, b, c]
    assert (e.ns["b"], e.ns["c"], e.ns["d"]) == (20, 21, 100)


def test_edit_deletes_names_no_longer_defined():
    e, (a, b) = build("a = 1\nold = 5", "y = old + 1")
    assert e.ns["y"] == 6
    ran = e.edit(a, "a = 1")
    assert "old" not in e.ns
    assert b in ran and e.cells[b].status == "error" and "NameError" in e.cells[b].error
    assert "y" not in e.ns


def test_cycle_is_error_and_blocks_descendants():
    e, (x, y, z) = build("x = y", "y = x", "z = x")
    assert status(e) == ["cycle", "cycle", "blocked"]
    assert not {"x", "y", "z"} & set(e.ns)


def test_cycle_descendant_before_cycle_in_file_is_blocked():
    e, _ = build("z = x", "x = y", "y = x")
    assert status(e) == ["blocked", "cycle", "cycle"]


def test_breaking_cycle_recovers():
    e, (x, y, z) = build("x = y", "y = x", "z = x")
    e.edit(y, "y = 1")
    assert status(e) == ["ok", "ok", "ok"] and e.ns["z"] == 1


def test_duplicate_definition_errors_all_definers_and_blocks_readers():
    e, (a1, a2, r) = build("a = 1", "a = 2", "b = a")
    assert status(e) == ["multiple-definition", "multiple-definition", "blocked"]
    assert "a" not in e.ns and "a" in e.cells[a1].error


def test_duplicate_introduced_by_edit_clears_previous_value():
    e, (a1, a2, r) = build("a = 1", "q = 0", "b = a")
    assert e.ns["b"] == 1
    e.edit(a2, "a = 2")
    assert status(e) == ["multiple-definition", "multiple-definition", "blocked"]
    assert "a" not in e.ns and "b" not in e.ns


def test_resolving_duplicate_reruns_remaining_definer_and_readers():
    e, (a1, a2, r) = build("a = 1", "a = 2", "b = a")
    e.edit(a2, "q = 0")
    assert status(e) == ["ok", "ok", "ok"] and e.ns["b"] == 1


def test_delete_cell_removes_names_and_invalidates_descendants():
    e, (a, b, c) = build("a = 1", "b = a + 1", "c = 3")
    assert e.delete(a) == [b]
    assert "a" not in e.ns and "b" not in e.ns
    assert e.cells[b].status == "error" and e.ns["c"] == 3


def test_delete_one_of_duplicate_definers_recovers_other():
    e, (a1, a2, r) = build("a = 1", "a = 2", "b = a")
    e.delete(a1)
    assert e.ns["b"] == 2 and status(e) == ["ok", "ok"]


def test_runtime_error_blocks_descendants_and_leaves_no_partial_defs():
    e, (a, b) = build("a = 1\nraise ValueError('x')", "b = a")
    assert status(e) == ["error", "blocked"] and "a" not in e.ns
    e.edit(a, "a = 1")
    assert status(e) == ["ok", "ok"] and e.ns["b"] == 1


def test_syntax_error_cell_defines_nothing():
    e, (a, b) = build("a = 1", "b = a")
    e.edit(a, "a = = 1")
    assert e.cells[a].status == "syntax-error"
    assert "a" not in e.ns and e.cells[b].status == "error"


def test_add_cell_runs_it_and_readers_of_its_names():
    e, (r,) = build("b = a * 2")
    assert e.cells[r].status == "error"
    cid, ran = e.add("a = 21")
    assert ran == [cid, r] and e.ns["b"] == 42


def test_output_is_captured_per_cell():
    e, (a,) = build("print('oi')")
    assert e.cells[a].output == "oi\n"


def test_private_names_do_not_create_edges_or_conflicts():
    e, _ = build("_i = 1\nx = _i", "_i = 2\ny = _i")
    assert status(e) == ["ok", "ok"] and (e.ns["x"], e.ns["y"]) == (1, 2)


def test_functions_see_later_redefinitions_via_graph():
    e, (f, g, use) = build("def f():\n    return k * 2", "k = 3", "r = f()")
    assert e.ns["r"] == 6
    e.edit(g, "k = 5")
    assert e.ns["r"] == 10  # f -> reads k, so f and r rerun
