"""Notebook file format (D-007): Markdown with fenced code cells."""
import re
from dataclasses import dataclass

CODE_KINDS = ("python", "mojo", "html")
_FENCE = re.compile(r"^(`{3,}|~{3,})(.*)$")
_FORMAT = "<!-- notebook-format: 1 -->"
_MARKDOWN = re.compile(r"<!-- notebook:markdown (={3,}) -->")


@dataclass
class Cell:
    kind: str  # "python" | "mojo" | "html" | "markdown"
    code: str


def parse(text: str) -> list[Cell]:
    cells: list[Cell] = []
    md: list[str] = []
    text = text.replace("\r\n", "\n")
    explicit = text == _FORMAT or text.startswith(_FORMAT + "\n")
    # Explicit blocks preserve blank edge lines and all characters except CRLF
    # normalization. Unversioned files retain the original Markdown grammar.
    lines = text.split("\n")[1:] if explicit else text.splitlines()
    i = 0

    def flush_md():
        body = "\n".join(md).strip("\n")
        if body.strip():
            cells.append(Cell("markdown", body))
        md.clear()

    while i < len(lines):
        if explicit and lines[i].startswith("<!-- notebook:markdown"):
            opening = _MARKDOWN.fullmatch(lines[i])
            if not opening:
                raise ValueError(f"invalid Markdown cell delimiter on line {i + 2}")
            closing = f"<!-- {opening.group(1)} -->"
            j = i + 1
            while j < len(lines) and lines[j] != closing:
                j += 1
            if j == len(lines):
                raise ValueError(f"unclosed Markdown cell on line {i + 2}")
            flush_md()
            # The payload is opaque: even a Python fence here is documentation.
            cells.append(Cell("markdown", "\n".join(lines[i + 1:j])))
            i = j + 1
            continue
        m = _FENCE.match(lines[i])
        if not m:
            md.append(lines[i])
            i += 1
            continue
        fence, info = m.group(1), m.group(2).strip()
        # CommonMark: closes with the same char, at least as long, nothing else on the line.
        close = re.compile(rf"^{re.escape(fence[0])}{{{len(fence)},}}\s*$")
        j = i + 1
        while j < len(lines) and not close.match(lines[j]):
            j += 1
        if info in CODE_KINDS:
            flush_md()
            cells.append(Cell(info, "\n".join(lines[i + 1:j])))
        else:  # foreign fence: stays inside the Markdown cell, verbatim
            md.extend(lines[i:j + 1])
        i = j + 1  # an unclosed fence runs to EOF, as in CommonMark
    flush_md()
    return cells


def serialize(cells: list[Cell]) -> str:
    parts = []
    for c in cells:
        if c.kind == "markdown":
            parts.append(c.code.strip("\n"))
        elif c.kind in CODE_KINDS:
            longest = max((len(r) for r in re.findall(r"`+", c.code)), default=0)
            fence = "`" * max(3, longest + 1)
            parts.append(f"{fence}{c.kind}\n{c.code}\n{fence}")
        else:
            raise ValueError(f"unknown cell kind: {c.kind!r}")
    legacy = "\n\n".join(parts) + "\n"
    try:
        if parse(legacy) == cells:
            return legacy  # keep existing notebooks/diffs unchanged when unambiguous
    except ValueError:
        pass  # a literal format header/delimiter needs an opaque Markdown block

    for i, c in enumerate(cells):
        if c.kind == "markdown":
            longest = max((len(run) for run in re.findall(r"=+", c.code)), default=0)
            delimiter = "=" * max(3, longest + 1)
            parts[i] = f"<!-- notebook:markdown {delimiter} -->\n{c.code}\n<!-- {delimiter} -->"
    return _FORMAT + "\n\n" + "\n\n".join(parts) + "\n"
