"""Notebook file format (D-007): Markdown with fenced code cells."""
import re
from dataclasses import dataclass

CODE_KINDS = ("python", "mojo", "html")
_FENCE = re.compile(r"^(`{3,}|~{3,})(.*)$")


@dataclass
class Cell:
    kind: str  # "python" | "mojo" | "html" | "markdown"
    code: str


def parse(text: str) -> list[Cell]:
    cells: list[Cell] = []
    md: list[str] = []
    lines = text.splitlines()
    i = 0

    def flush_md():
        body = "\n".join(md).strip("\n")
        if body.strip():
            cells.append(Cell("markdown", body))
        md.clear()

    while i < len(lines):
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
        else:
            longest = max((len(r) for r in re.findall(r"`+", c.code)), default=0)
            fence = "`" * max(3, longest + 1)
            parts.append(f"{fence}{c.kind}\n{c.code}\n{fence}")
    return "\n\n".join(parts) + "\n"
