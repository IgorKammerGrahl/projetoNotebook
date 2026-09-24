import pytest
from kernel.analysis import analyze


@pytest.mark.parametrize("code, defs, refs", [
    ("x = y + 1", {"x"}, {"y"}),
    ("import os.path\nfrom m import a as b", {"os", "b"}, set()),
    ("def f(a):\n    return a + g", {"f"}, {"g"}),
    ("class C:\n    k = 1\n    def m(self): return k2", {"C"}, {"k2"}),
    ("f = lambda q: q + w", {"f"}, {"w"}),
    ("z = [i * y for i in r]", {"z"}, {"y", "r"}),       # comprehension var is not a def
    ("u += 1", {"u"}, set()),                           # own def: removed from refs
    ("del old", set(), {"old"}),
    ("try:\n    pass\nexcept E as e:\n    print(e)", set(), {"E", "print"}),
    ("for t in r:\n    pass", {"t"}, {"r"}),
    ("if (n := v):\n    pass", {"n"}, {"v"}),
    ("_tmp = a\nb = _tmp", {"b"}, {"a"}),               # private names out of the graph
    ("def f():\n    global g\n    g = 1", {"f"}, set()),  # DEBT-005
])
def test_analyze(code, defs, refs):
    d, r = analyze(code)
    assert d == defs
    assert r - {"print", "range"} == refs - {"print", "range"}


def test_augassign_counts_as_ref_before_own_def_filter():
    # u is read by `u += 1`; since the cell also defines u, it collides with any other definer
    assert analyze("u += 1") == ({"u"}, set())
