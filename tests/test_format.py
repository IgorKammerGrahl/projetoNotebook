from kernel.fmt import Cell, parse, serialize
import pytest

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


@pytest.mark.parametrize("cells", [
    [Cell("markdown", "a"), Cell("markdown", "b"), Cell("markdown", "c")],
    [Cell("markdown", "")],
    [Cell("markdown", ""), Cell("markdown", ""), Cell("markdown", "fim")],
    [Cell("markdown", "começo"), Cell("markdown", ""), Cell("markdown", "")],
    [Cell("python", "x = 1"), Cell("markdown", ""), Cell("html", "<b>fim</b>")],
    [Cell("markdown", "\n  \n"), Cell("mojo", "def run(mut x: Int):\n    x = 1"), Cell("markdown", "\nfim\n\n")],
    [Cell("markdown", "Exemplo, sem executar:\n```python\nraise RuntimeError('não executar')\n```\n")],
    [Cell("markdown", "cerca aberta:\n```bash\necho oi"), Cell("python", "x = 1")],
    [Cell("markdown", "<!-- cell -->\n<!-- === -->\n<!-- notebook:markdown ==== -->"), Cell("markdown", "fim")],
    [Cell("markdown", "<!-- notebook-format: 1 -->\n<!-- notebook:markdown === -->\nliteral")],
])
def test_preserves_markdown_boundaries_and_source(cells):
    saved = serialize(cells)
    assert parse(saved) == cells
    assert serialize(parse(saved)) == saved


def test_old_notebooks_keep_literal_comments_and_foreign_fences():
    text = "<!-- notebook:markdown === -->\n<!-- cell -->\n<!-- === -->\n\n```bash\n<!-- === -->\n```\n"
    assert parse(text) == [Cell("markdown", text.rstrip("\n"))]
    assert serialize(parse(text)) == text


def test_explicit_markdown_does_not_parse_its_examples_as_executable_cells():
    text = "<!-- notebook-format: 1 -->\n\n<!-- notebook:markdown === -->\n```python\nraise RuntimeError('example')\n```\n<!-- === -->\n"
    assert parse(text) == [Cell("markdown", "```python\nraise RuntimeError('example')\n```")]


@pytest.mark.parametrize("opening", ["<!-- notebook:markdown === -->", "<!-- notebook:markdown broken -->"])
def test_broken_explicit_markdown_is_rejected_before_any_cells_can_load(opening):
    text = f"<!-- notebook-format: 1 -->\n{opening}\ntext\n```python\nx = 1\n```\n"
    with pytest.raises(ValueError, match="Markdown"):
        parse(text)


def test_explicit_format_accepts_crlf_files():
    cells = [Cell("markdown", "a"), Cell("markdown", "b")]
    assert parse(serialize(cells).replace("\n", "\r\n")) == cells
