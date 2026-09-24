"""Medições da Fase 1 (não é teste; `pixi run python tests/bench_phase1.py`)."""
import timeit
from kernel.engine import Engine, analyze
from kernel.fmt import Cell, parse, serialize

cell50 = "\n".join(f"v{i} = [j * a{i % 7} for j in range(10)] + list(map(f, b))" if i % 5 else
                   f"def g{i}(x):\n    return x + w{i}" for i in range(40))
assert 45 <= cell50.count("\n") + 1 <= 55
t = min(timeit.repeat(lambda: analyze(cell50), number=200, repeat=5)) / 200
print(f"analyze(célula de {cell50.count(chr(10)) + 1} linhas): {t * 1e3:.3f} ms")

N = 200
codes = ["c0 = 0"] + [f"c{i} = c{i - 1} + 1" for i in range(1, N)]
eng = Engine(); eng.load([Cell("python", c) for c in codes]); first = next(iter(eng.cells))
t = min(timeit.repeat(lambda: eng.edit(first, "c0 = 0"), number=20, repeat=5)) / 20
# código do usuário aqui é trivial (~1 µs/célula): o tempo é overhead do motor
print(f"edição no topo de cadeia de {N} células (reexecuta {N}): {t * 1e3:.2f} ms")
last = list(eng.cells)[-1]
t = min(timeit.repeat(lambda: eng.edit(last, f"c{N-1} = c{N-2} + 1"), number=50, repeat=5)) / 50
print(f"edição na folha da mesma cadeia (reexecuta 1): {t * 1e3:.2f} ms")

text = serialize([Cell("python" if i % 2 else "markdown", f"x{i} = {i}\n# comentário") for i in range(N)])
t = min(timeit.repeat(lambda: serialize(parse(text)), number=50, repeat=5)) / 50
print(f"parse + serialize de {N} células: {t * 1e3:.2f} ms")
