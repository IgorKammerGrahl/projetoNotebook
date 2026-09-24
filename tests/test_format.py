from kernel.fmt import Cell, parse, serialize

DOC = """# Título

Texto.

```python
x = 1
```

```mojo
def f(): pass
```

Entre células, com um bloco estranho:

```bash
```python
echo isto não abre célula
```

```html
<b>oi</b>
```
"""


def test_parse_kinds_and_content():
    cells = parse(DOC)
    assert [c.kind for c in cells] == ["markdown", "python", "mojo", "markdown", "html"]
    assert cells[1].code == "x = 1"
    assert "echo isto não abre célula" in cells[3].code  # foreign fence stays in markdown


def test_canonical_text_round_trips():
    assert serialize(parse(DOC)) == DOC


def test_cells_round_trip_including_backticks_and_empty():
    cells = [Cell("python", 's = """\n```\n"""'), Cell("python", ""), Cell("markdown", "a\n\nb"),
             Cell("html", "````\n<i>x</i>")]
    assert parse(serialize(cells)) == cells


def test_unclosed_fence_runs_to_eof():
    assert parse("```python\nx = 1\n") == [Cell("python", "x = 1")]


def test_indented_fence_is_not_a_cell():
    assert [c.kind for c in parse("   ```python\nx\n   ```\n")] == ["markdown"]
