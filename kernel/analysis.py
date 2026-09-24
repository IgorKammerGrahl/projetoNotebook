"""Static analysis of Python cells: which globals a cell defines and reads (D-008)."""
import ast
import symtable


def _private(name: str) -> bool:
    return name.startswith("_")


def _top_level_nodes(tree: ast.AST):
    """Nodes in the module scope, without descending into nested scopes."""
    scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda,
              ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, scopes))


def _global_refs(table: symtable.SymbolTable) -> set[str]:
    """Names referenced in nested scopes that resolve to globals."""
    refs = set()
    for child in table.get_children():
        refs |= {s.get_name() for s in child.get_symbols() if s.is_global() and s.is_referenced()}
        refs |= _global_refs(child)
    return refs


def analyze(code: str) -> tuple[set[str], set[str]]:
    """(defs, refs) for a cell's globals. Raises SyntaxError."""
    tree = ast.parse(code)
    top = symtable.symtable(code, "<cell>", "exec")
    defs, refs = set(), _global_refs(top)
    for s in top.get_symbols():
        if s.is_comp_iter():
            continue
        if s.is_assigned() or s.is_imported():
            defs.add(s.get_name())
        if s.is_referenced():
            refs.add(s.get_name())
    for node in _top_level_nodes(tree):
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            refs.add(node.target.id)
        elif isinstance(node, ast.Delete):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    refs.add(t.id)
                    defs.discard(t.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            # bound only inside the handler: neither exported nor read from other cells
            defs.discard(node.name)
            refs.discard(node.name)
    defs = {n for n in defs if not _private(n)}
    refs = {n for n in refs if not _private(n)} - defs
    return defs, refs
