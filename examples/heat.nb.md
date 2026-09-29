# Difusão de calor 2-D

Uma placa começa fria, com um disco quente no centro. As bordas ficam presas a
0 °C. A cada passo, cada ponto se aproxima da média dos quatro vizinhos
(esquema explícito de diferenças finitas):

T'[i, j] = T[i, j] + r · (T[i-1, j] + T[i+1, j] + T[i, j-1] + T[i, j+1] − 4 · T[i, j])

O passo de tempo roda em Mojo; o NumPy prepara a grade, confere o resultado e
mede a mesma conta para comparar. Mude `steps` ou `r` e dê Shift+Enter: só o
que depende deles reexecuta, sem recompilar o Mojo.

Este notebook é o teste de aceitação da 1.0 (D-023). Os trechos marcados com
**Lacuna** contornam limites de hoje e devem sumir até lá.

```python
import numpy as np

nx, ny = 200, 200   # pontos da grade
r = 0.2             # α·Δt/Δx²: o esquema explícito só é estável com r ≤ 0,25
steps = 500         # passos de tempo
assert r <= 0.25, f"r = {r} torna o esquema instável (máximo 0,25)"
```

```python
_yy, _xx = np.mgrid[0:ny, 0:nx]
_raio = min(nx, ny) / 8
T0 = np.where((_xx - nx / 2) ** 2 + (_yy - ny / 2) ** 2 < _raio**2, 100.0, 0.0)
# Lacuna (DEBT-009): células Mojo só recebem arrays 1-D, então a grade vai achatada.
u0 = T0.ravel()
```

```mojo
from std.time import perf_counter_ns


def run(u0: ArrayIn[DType.float64], nx: Int, ny: Int, r: Float64, steps: Int,
        mut u: ArrayOut[DType.float64], mut heat: Float64, mut mojo_ms: Float64) raises:
    var n = nx * ny
    if nx < 3 or ny < 3 or len(u0) != n:
        raise Error("u0 precisa ter nx * ny elementos, com nx, ny >= 3")
    var buf = List[Float64](length=2 * n, fill=0.0)   # dois buffers: [0, n) e [n, 2n)
    for k in range(n):
        buf[k] = u0[k]
        buf[n + k] = u0[k]                            # as bordas ficam iguais nos dois
    var src = 0
    var dst = n
    var t0 = perf_counter_ns()
    for _ in range(steps):
        for i in range(1, ny - 1):
            for j in range(1, nx - 1):
                # Lacuna: `buf[k]` checado deixa este laço 6× mais lento (111 vs 17 ms).
                # Os índices ficam em [0, 2n) porque 1 <= i <= ny-2 e 1 <= j <= nx-2.
                var k = i * nx + j
                var c = buf.unsafe_get(src + k)
                buf.unsafe_set(dst + k, c + r * (buf.unsafe_get(src + k - nx) + buf.unsafe_get(src + k + nx)
                                                 + buf.unsafe_get(src + k - 1) + buf.unsafe_get(src + k + 1) - 4.0 * c))
        src, dst = dst, src
    mojo_ms = Float64(perf_counter_ns() - t0) / 1e6
    u.alloc(n)
    for k in range(n):
        u[k] = buf[src + k]
        heat += u[k]
```

```python
# Lacuna (DEBT-009): a saída também volta achatada.
T = u.reshape(ny, nx)
print(f"máx {T.max():.2f} °C · média {T.mean():.3f} °C")
print(f"calor total: {T0.sum():,.0f} → {heat:,.0f} ({heat / T0.sum():.1%}; cai quando chega às bordas)")
```

Conferência: a mesma conta vetorizada em NumPy tem de dar o mesmo resultado.

```python
import time


def diffuse_numpy(T, r, steps):
    v = T.copy()
    for _ in range(steps):
        v[1:-1, 1:-1] += r * (v[:-2, 1:-1] + v[2:, 1:-1] + v[1:-1, :-2] + v[1:-1, 2:] - 4 * v[1:-1, 1:-1])
    return v


_t = time.perf_counter()
T_ref = diffuse_numpy(T0, r, steps)
numpy_ms = (time.perf_counter() - _t) * 1000
assert np.allclose(T, T_ref), "Mojo e NumPy divergem"
print(f"Mojo confere com NumPy · Mojo {mojo_ms:.0f} ms · NumPy {numpy_ms:.0f} ms")
```

```python
# Lacuna (saídas ricas): sem imagem, um mapa de calor em texto.
_ramp = " .:-=+*#%@"
_small = T[:: max(ny // 20, 1), :: max(nx // 40, 1)]
print("\n".join("".join(_ramp[min(int(v / T.max() * len(_ramp)), len(_ramp) - 1)] for v in row) for row in _small))
```
