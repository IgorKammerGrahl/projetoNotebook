# Demonstração dos estados

Ao abrir, o notebook roda tudo uma vez. Células saudáveis ficam **silenciosas**
(sem chip nem trilho). O que aparece com chip ao abrir é proposital: erro,
erro de compilação com diagnóstico, bloqueio, definição duplicada e ciclo.

**Roteiro (tudo com Shift+Enter numa célula, ou pelos botões do topo):**

1. **running, queued, stale, compiling:** clique em *rodar tudo*. A célula
   [3] dorme 2 s: ela fica *executando*, as independentes ficam *na fila* e as
   que dependem dela ficam *vai reexecutar · aguardando [3]*.
2. **modified e upstream_modified:** mude `scale = 3.0` para `4.0` na célula
   [3] **sem** executar. Ela fica *modificada*, e os descendentes mostram
   *código de [3] mudou sem executar*, com os valores antigos visíveis.
3. **diagnóstico e build em 2º plano:** na célula Mojo [4], escreva
   `total += nope` e pare de digitar. Aparece *build em 2º plano*, depois
   *1 problema*, com o sublinhado em `nope` (não na linha em que o cursor
   estiver enquanto você digita). Desfaça com Ctrl+Z.
4. **crash:** na célula [17], troque `crash = 0` por `crash = 1` e dê
   Shift+Enter. A célula Mojo [18] derruba o kernel: ela fica *derrubou o
   kernel · quarentena*, lista a célula [4] (que usa `unsafe_get`) como
   suspeita, o kernel reinicia e o resto volta.
5. **interrupção:** na célula [20], troque `forever = False` por `True` e dê
   Shift+Enter. Com a célula [21] *executando*, clique em **parar**.
6. **idle:** o botão *+ python* abaixo de qualquer célula cria uma célula nova,
   silenciosa até ser executada.

```python
import numpy as np
n = 200_000
xs = np.linspace(0.0, 1.0, n)
```

```python
import time as _t
_t.sleep(2)  # dá tempo de ver running / queued / stale
scale = 3.0
```

```mojo
def run(xs: ArrayIn[DType.float64], scale: Float64,
        mut ys: ArrayOut[DType.float64], mut total: Float64) raises:
    ys.alloc(len(xs))
    for i in range(len(xs)):
        ys[i] = xs.unsafe_get(i) * scale   # unsafe_get: aparece como suspeito no crash
        total += ys[i]
```

```python
summary = f"total = {total:,.1f} (média {ys.mean():.3f})"
print(summary)
```

```python
independent = 42  # não depende de ninguém: fica "na fila" enquanto [3] dorme
```

Uma célula Mojo com erro de compilação, e uma leitora dela, que fica bloqueada:

```mojo
def run(xs: ArrayIn[DType.float64], mut bad: Float64) raises:
    bad = undefined_name + 1.0
```

```python
uses_bad = bad * 2
```

Erro de execução, definição duplicada e ciclo:

```python
ratio = 1 / 0
```

```python
dup = 1
```

```python
dup = 2
```

```python
cyc_a = cyc_b + 1
```

```python
cyc_b = cyc_a + 1
```

Crash e interrupção, controlados por flags:

```python
crash = 0
```

```mojo
def run(crash: Int, ys: ArrayIn[DType.float64], mut first: Float64) raises:
    if crash == 1:
        Pointer[Int, MutAnyOrigin](unsafe_from_address=8).unsafe_store(1)  # SIGSEGV
    first = ys[0]
```

```html
<div style="font-family: sans-serif; padding: 8px">
  <b>Célula HTML</b>, renderizada num <code>iframe sandbox</code> sem scripts.
</div>
```

```python
forever = False
```

```python
import time as _t2
while forever:
    _t2.sleep(0.05)
loop_done = True
```
