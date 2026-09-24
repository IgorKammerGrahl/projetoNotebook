# Células Mojo — guia

## Interface: a assinatura de `run` (D-010)

```mojo
def run(xs: ArrayIn[DType.float64], k: Float64,   # entradas: sem `mut`
        mut ys: ArrayOut[DType.float64],          # saídas: com `mut`
        mut total: Float64) raises:
    ys.alloc(len(xs))
    for i in range(len(xs)):
        ys[i] = xs[i] * k
        total += ys[i]
```

- **Tipos:** `Int`, `Float64`, `ArrayIn[DType.<dt>]` e `ArrayOut[DType.<dt>]`,
  com `<dt>` ∈ `float64`, `float32`, `int64`, `int32`. Arrays são 1-D e
  contíguos; o host valida dtype, contiguidade e alinhamento antes de chamar.
- **Cada nome tem um único dono no grafo:** `mut x: ArrayIn` é proibido.
- **Saídas escalares começam em 0 a cada execução** (D-011). Se `run` levanta
  erro, nenhuma saída é publicada.
- **Parâmetros ajustáveis** vêm de células Python como entradas (D-005): mudar um
  valor reexecuta a célula sem recompilar.
- São permitidos comentários `# ...` na lista de parâmetros, e funções e structs
  auxiliares na célula. Não são permitidos defaults, tipo de retorno nem
  parâmetros `[...]` em `run`.

## Acesso a arrays

| Forma | Checagem | Quando usar |
|---|---|---|
| `xs[i]`, `ys[i] = v` | limites (levanta `Error`) | padrão |
| `xs.unsafe_get(i)`, `ys.unsafe_set(i, v)` | nenhuma | índice que o compilador não prova estar nos limites (gather, `xs[idx[i]]`): a checagem custa **3×** ali |
| `xs.unsafe_ptr()` / `ys.unsafe_ptr()` | nenhuma | SIMD explícito (abaixo). Devolve o `Pointer` do stdlib. Em `ArrayOut`, só depois de `alloc()` |

Com índice vindo de `range(len(xs))`, o custo da checagem medido foi 0–4%.
Números em `docs/PHASE2.md`.

## Desempenho: o Mojo 1.1 não autovetoriza

Medido no assembly (`mojo build -O3 --emit asm`, Mojo 1.1.0): até
`q[i] = p[i] + 1` sai escalar, um elemento por iteração e sem desenrolar. **O
ganho em laços quentes vem de SIMD explícito:** 2,5–5,4× nos laços de soma com
dados em cache (`docs/PHASE2.md`). Há dois caminhos, e ambos compilam e rodam
numa célula (testados):

```mojo
# 1. load/store com largura, via unsafe_ptr()
var p = xs.unsafe_ptr()
var acc = SIMD[DType.float64, 8](0)
var i = 0
while i + 8 <= len(xs):
    acc += p.unsafe_load[width=8](i)
    i += 8
# ...cauda escalar...

# 2. vectorize da stdlib (existe na 1.1: std.algorithm.functional)
from std.algorithm.functional import vectorize
def run(xs: ArrayIn[DType.float64], mut ys: ArrayOut[DType.float64]) raises:
    var n = len(xs)
    ys.alloc(n)
    var p = xs.unsafe_ptr()
    var q = ys.unsafe_ptr()
    def body[width: Int](i: Int) {mut}:
        q.unsafe_store(i, p.unsafe_load[width=width](i) * 2.0)
    vectorize[8](n, body)   # trata a cauda sozinho
```

O ganho do `vectorize` não foi isolado nas medições. Com 8192 elementos, o
overhead fixo da chamada (~21 µs) domina. O que está medido é o SIMD explícito
do caminho 1.

## Quando algo dá errado

- **Erro de compilação:** aponta a linha da célula. Colisão com nomes do
  prelúdio (`ArrayIn`, `ArrayOut`, `nb_cell_entry`, `__nb_*`) é indicada.
- **Crash do kernel** (SIGSEGV, via `unsafe_*` ou `Pointer`): no servidor, a
  célula vai para `crashed`, o kernel reinicia e o resto do notebook é
  reexecutado (D-013).
  - A mensagem diz em qual célula o processo morreu e **lista as células a
    montante que usam `unsafe_*`**. A corrupção pode ter vindo delas, e a
    quarentena pode ter culpado uma célula inocente.
  - Editar ou executar a célula tira ela da quarentena.
- **Laço infinito:** o botão parar mata o kernel, e a célula vai para
  `interrupted`.
