"""Notebook file format (D-007): Markdown with fenced code cells."""
import re
import hashlib
from dataclasses import dataclass

CODE_KINDS = ("python", "mojo", "html")
_FENCE = re.compile(r"^(`{3,}|~{3,})(.*)$")
_FORMAT = "<!-- notebook-format: 1 -->"
_MARKDOWN = re.compile(r"<!-- notebook:markdown (={3,}) -->")
_IDENTIFIED = "<!-- notebook-format: 2 -->"
_CELL_ID = re.compile(r"<!-- notebook-cell: ([0-9a-f]{32}) -->")


@dataclass
class Cell:
    kind: str  # "python" | "mojo" | "html" | "markdown"
    code: str


@dataclass
class Document:
    cells: list[Cell]
    cell_ids: list[str]


def parse_document(text: str) -> Document:
    text = text.replace("\r\n", "\n")
    if text != _IDENTIFIED and not text.startswith(_IDENTIFIED + "\n"):
        cells = parse(text)
        # Until the first save writes IDs, only this exact legacy document may
        # reuse these identities. An external edit makes old drafts orphans,
        # never candidates for a different cell merely occupying the same slot.
        digest = hashlib.sha256(text.encode()).hexdigest()
        ids = [hashlib.sha256(f"{digest}:{i}".encode()).hexdigest()[:32] for i in range(len(cells))]
        return Document(cells, ids)
    lines = text.split("\n")
    cells, ids = [], []
    i = 1
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        marker = _CELL_ID.fullmatch(lines[i])
        if not marker or marker[1] in ids:
            raise ValueError(f"invalid or duplicate cell identity on line {i + 1}")
        opening = _FENCE.fullmatch(lines[i + 1]) if i + 1 < len(lines) else None
        if not opening or opening[2] not in (*CODE_KINDS, "markdown"):
            raise ValueError(f"invalid cell fence on line {i + 2}")
        close = re.compile(rf"^{re.escape(opening[1][0])}{{{len(opening[1])},}}\s*$")
        j = i + 2
        while j < len(lines) and not close.fullmatch(lines[j]):
            j += 1
        if j == len(lines):
            raise ValueError(f"unclosed cell on line {i + 2}")
        ids.append(marker[1])
        cells.append(Cell(opening[2], "\n".join(lines[i + 2:j])))
        i = j + 1
    return Document(cells, ids)


def serialize_document(document: Document) -> str:
    if len(document.cells) != len(document.cell_ids) or len(set(document.cell_ids)) != len(document.cell_ids):
        raise ValueError("cell identities must be unique and match the cells")
    parts = [_IDENTIFIED]
    for cell, uid in zip(document.cells, document.cell_ids):
        if not re.fullmatch(r"[0-9a-f]{32}", uid) or cell.kind not in (*CODE_KINDS, "markdown"):
            raise ValueError("invalid cell identity or kind")
        longest = max((len(r) for r in re.findall(r"`+", cell.code)), default=0)
        fence = "`" * max(3, longest + 1)
        parts.append(f"<!-- notebook-cell: {uid} -->\n{fence}{cell.kind}\n{cell.code}\n{fence}")
    return "\n\n".join(parts) + "\n"


def parse(text: str) -> list[Cell]:
    if text.replace("\r\n", "\n").split("\n", 1)[0] == _IDENTIFIED:
        return parse_document(text).cells
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
