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
  com `<dt>` ∈ `float64`, `float32`, `int64`, `int32`. Arrays 2-D:
  `ArrayIn[DType.<dt>, 2]` e `ArrayOut[DType.<dt>, 2]` (D-024). Arrays são
  contíguos (ordem C); o host valida dtype, número de dimensões, contiguidade e
  alinhamento antes de chamar. Um array 2-D numa entrada 1-D, ou o contrário, é
  recusado: não há achatamento implícito.
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
| `xs[i]`, `ys[i] = v` (1-D); `m[i, j]`, `o[i, j] = v` (2-D) | limites (levanta `Error`) | padrão |
| `xs.unsafe_get(i)`, `ys.unsafe_set(i, v)`; `m.unsafe_get(i, j)`, `o.unsafe_set(i, j, v)` | nenhuma | índice que o compilador não prova estar nos limites (gather, `xs[idx[i]]`): a checagem custa **3×** ali |
| `xs.unsafe_ptr()` / `ys.unsafe_ptr()` | nenhuma | SIMD explícito (abaixo). Devolve o `Pointer` do stdlib, em ordem C (linha a linha). Em `ArrayOut`, só depois de `alloc()` |

Com índice vindo de `range(len(xs))`, o custo da checagem medido foi 0–4%.
Números em `docs/PHASE2.md`. No estêncil 2-D do notebook de referência
(200×200, 500 passos), a versão checada leva 23–33 ms e a `unsafe_*`, 16–17 ms;
o NumPy vetorizado, 114 ms.

**Dimensões:** `m.dim(0)` (linhas) e `m.dim(1)` (colunas); `len(m)` é
`m.dim(0)`, como no NumPy. Saída 2-D: `o.alloc(linhas, colunas)`; 1-D:
`ys.alloc(n)`. Usar a forma de outro rank (`m[i]` num array 2-D) é erro de
compilação apontado na linha da célula.

## Buffer de trabalho: `Scratch`

Para temporários que não saem da célula (o segundo buffer de um estêncil, um
acumulador):

```mojo
var a = Scratch[DType.float64, 2](ny, nx)   # 2-D, zerado
var acc = Scratch[DType.int64](n)           # 1-D
a[i, j] = 1.0
swap(a, b)                                  # troca dois buffers sem copiar
```

Mesmo acesso dos arrays (`[...]` checado, `unsafe_get`/`unsafe_set`, `dim`,
`len`), sem `unsafe_ptr`. A memória é liberada quando a célula retorna. Não
entra no grafo. Prefira `Scratch` a `List` em laços quentes: `List[i]` checado
deixou o mesmo estêncil 6× mais lento (D-023).

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
  prelúdio (`ArrayIn`, `ArrayOut`, `Scratch`, `nb_cell_entry`, `__nb_*`) é indicada.
- **Crash do kernel** (SIGSEGV, via `unsafe_*` ou `Pointer`): no servidor, a
  célula vai para `crashed`, o kernel reinicia e o resto do notebook é
  reexecutado (D-013).
  - A mensagem diz em qual célula o processo morreu e **lista as células a
    montante que usam `unsafe_*`**. A corrupção pode ter vindo delas, e a
    quarentena pode ter culpado uma célula inocente.
  - Editar ou executar a célula tira ela da quarentena.
- **Laço infinito:** o botão parar mata o kernel, e a célula vai para
  `interrupted`.
