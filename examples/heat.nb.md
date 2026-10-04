# Difusão de calor 2-D

Uma placa começa fria, com um disco quente no centro. As bordas ficam presas a
0 °C. A cada passo, cada ponto se aproxima da média dos quatro vizinhos
(esquema explícito de diferenças finitas):

T'[i, j] = T[i, j] + r · (T[i-1, j] + T[i+1, j] + T[i, j-1] + T[i, j+1] − 4 · T[i, j])

O passo de tempo roda em Mojo; o NumPy prepara a grade, confere o resultado e
mede a mesma conta para comparar. Mude `steps` ou `r` e dê Shift+Enter: só o
que depende deles reexecuta, sem recompilar o Mojo.

Este notebook é o teste de aceitação da 1.0 (D-023): roda de ponta a ponta e o
Mojo tem de conferir com o NumPy.

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
```

```mojo
from std.time import perf_counter_ns


def step(src: Scratch[DType.float64, 2], mut dst: Scratch[DType.float64, 2], r: Float64) raises:
    for i in range(1, src.dim(0) - 1):
        for j in range(1, src.dim(1) - 1):
            var c = src[i, j]
            dst[i, j] = c + r * (src[i - 1, j] + src[i + 1, j] + src[i, j - 1] + src[i, j + 1] - 4.0 * c)


def run(T0: ArrayIn[DType.float64, 2], r: Float64, steps: Int,
        mut T: ArrayOut[DType.float64, 2], mut heat: Float64, mut mojo_ms: Float64) raises:
    var ny = T0.dim(0)
    var nx = T0.dim(1)
    var a = Scratch[DType.float64, 2](ny, nx)
    var b = Scratch[DType.float64, 2](ny, nx)
    for i in range(ny):
        for j in range(nx):
            a[i, j] = T0[i, j]
            b[i, j] = T0[i, j]   # as bordas ficam iguais nos dois buffers
    var t0 = perf_counter_ns()
    for _ in range(steps):
        step(a, b, r)
        swap(a, b)
    mojo_ms = Float64(perf_counter_ns() - t0) / 1e6
    T.alloc(ny, nx)
    for i in range(ny):
        for j in range(nx):
            T[i, j] = a[i, j]
            heat += a[i, j]
```

```python
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
import matplotlib.pyplot as plt

_fig, (_mapa, _perfil) = plt.subplots(1, 2, figsize=(10, 4))
_im = _mapa.imshow(T, cmap="inferno")
_fig.colorbar(_im, ax=_mapa, label="°C")
_mapa.set_title(f"Temperatura após {steps} passos")
_perfil.plot(T0[ny // 2], label="inicial")
_perfil.plot(T[ny // 2], label=f"após {steps} passos")
_perfil.set(title="Perfil na linha central", xlabel="x", ylabel="°C")
_perfil.legend()
_fig.tight_layout()
```
