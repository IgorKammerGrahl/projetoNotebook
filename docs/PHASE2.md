# Fase 2 — Células Mojo no grafo

## Previsões (escritas ANTES de implementar)

| Item | Previsão |
|------|----------|
| Edição→resultado, célula Mojo nova (compila) | 1,4–1,8 s: 1,3 s da célula C-ABI do spike + prelúdio maior + `nm` + carga |
| Reexecução por dado novo a montante (cache em memória, sem recompilar) | < 1 ms + trabalho da célula |
| Cache em disco, processo novo (sem compilar) | 20–50 ms (`dlopen` + `nm`) |
| Overhead fixo da chamada (validação + slots + callback de 1 saída) | 10–30 µs |
| `print` do Mojo capturado no nível do fd | funciona, mas pode exigir flush explícito |

## Medido (`PYTHONPATH=. pixi run python tests/bench_phase2.py`)

| Item | Previsão | Medido | Diferença |
|------|----------|--------|-----------|
| Edição→resultado, célula Mojo com código novo (compila) | 1,4–1,8 s | **1,59–1,80 s** (1,59–1,64 s na rodada com nonce, depois da revisão) | dentro |
| Parâmetro novo numa célula Python → célula Mojo reexecuta sem compilar (2 Python + 1 Mojo, 1000 elementos) | < 1 ms + trabalho | 0,15 ms | dentro |
| Processo novo, cache em disco (sem compilar) | 20–50 ms | 29–37 ms (`mojo --version` + `nm` + `dlopen` + chamada) | dentro |
| Overhead fixo de uma chamada (validação + slots + 1 alloc + captura de fd) | 10–30 µs | 25 µs | dentro |
| `print` do Mojo capturado no fd | pode exigir flush | funciona sem flush explícito | melhor |

**Armadilha na medição:** a primeira rodada deu 0,67 s numa edição. Era um hit do
cache do compilador Mojo, que é global e endereçado por conteúdo: o benchmark
reusou exatamente o fonte (`+ 1.0`) que um teste já tinha compilado. É o mesmo
efeito de "mesmo fonte, cache quente" do spike 4 (0,5 s). O número real de uma
edição nova é 1,65–1,8 s: ~1,3 s da célula C-ABI mínima, mais o prelúdio e o
wrapper.

## O que existe

- `kernel/mojo.py`:
  - interface derivada da assinatura de `run` (D-010, antes cabeçalho D-009) e geração do arquivo (código do usuário
    primeiro, prelúdio e wrapper `@export` depois);
  - build com cache em disco (chave = código + prelúdio + versão do `mojo`, D-004)
    e escrita atômica;
  - carregador escolhido por `nm` (D-002), carga por caminho versionado (D-001);
  - validação de entradas na fronteira (paga DEBT-002);
  - saídas via callback com posse do Python e guarda de exceção (D-003);
  - captura de `print` no fd, mapeamento de erros de compilação para linhas da
    célula, e assinatura esperada quando `run` diverge.
- `kernel/prelude.mojo`: `ArrayIn` e `ArrayOut` com checagem de limites, e o
  relato de erro do Mojo para o host.
- `kernel/engine.py`: células Mojo no mesmo grafo (os `in` são referências, os
  `out` são definições), com o novo estado `compile-error`.
- CLI: o mesmo `pixi run python -m kernel run arquivo.nb.md` agora executa células
  Mojo, com cache em `.nbcache/` ao lado do notebook.
- Testes (`pixi run test`, 80 no total, 26 de células Mojo):
  - fluxo Python→Mojo→Python;
  - dados novos e parâmetros novos sem recompilar (D-004, D-005);
  - cache em disco entre engines;
  - edição de código recompilando;
  - carregador `CDLL` vs `PyDLL`, e célula que usa Python;
  - erro de compilação na linha certa;
  - assinatura divergente;
  - erro de runtime sem saídas parciais;
  - acesso fora dos limites;
  - saída não alocada, saída vazia;
  - validação de arrays e escalares;
  - entrada inexistente.

---

# Ajustes da revisão da Fase 2

## Previsões (escritas ANTES de medir/implementar)

| Item | Previsão |
|------|----------|
| Custo do bounds check, soma de 10⁷ `float64` com `xs[i]` vs sem checagem | 1,5–3× mais lento: o ramo que levanta `Error` dentro do laço deve impedir a vetorização |
| Idem, escrita `ys[i] = ...` | parecido com a leitura |
| Nonce só em comentário escapa do cache global do compilador | **medido antes de prever**: escapa (1376 ms vs 629 ms num hit) — o cache usa o texto bruto |

## Medido

**Regra para benchmarks de compilação (A4):** todo fonte compilado num benchmark
leva um nonce único (`# nonce <uuid4>`). O cache global do compilador Mojo é
endereçado pelo **texto bruto**, então um comentário basta:

| Variante | Tempo |
|---|---|
| fonte novo | 1496 ms |
| mesmo fonte (hit) | 629 ms |
| só um comentário de nonce | 1376 ms (miss) |
| `comptime` não usado | 1456 ms (miss) |
| nonce semântico | 1471 ms (miss) |

`tests/bench_phase2.py` e `tests/bench_bounds.py` seguem a regra. Com nonce, a
edição→resultado deu 1,59–1,64 s (6 edições).

**Custo do bounds check (A3, `tests/bench_bounds.py`):**

| Laço (~10⁷ acessos) | Checado | Sem checagem | Custo |
|---|---|---|---|
| soma `float64`, 80 MB | 6,48 ms | 6,27 ms | +3% |
| soma `int64`, 80 MB | 4,54 ms | 4,37 ms | +4% |
| escala e escreve `float64`, 80 MB | 12,58 ms | 12,92 ms | −3% (ruído) |
| soma `int64`, 64 KB em L2 × 1221 passadas | 2,70 ms | 2,70 ms | 0% |
| **gather `xs[xs[i] % n]`**, em L2 | 47,95 ms | 15,92 ms | **+201%** |

**Previsão errada:** eu previ 1,5–3× em todo laço quente. Na verdade:
- com índice vindo de `range(len(xs))`, **o custo some** (0–4%), tanto com dado
  na memória quanto em cache. *Correção posterior:* atribuí isso à eliminação da
  checagem pelo compilador sem olhar o assembly. O que está medido é só que o
  custo some: a checagem pode ter sido eliminada ou o ramo pode ser previsto
  perfeitamente, e não verifiquei qual dos dois;
- a checagem só custa quando o índice não é demonstrável, como num gather. Aí
  custa 3×, acima do limite de 10%.

Por isso adicionei `unsafe_get(i)` e `unsafe_set(i, v)` (D-010), que rodam na
mesma velocidade do ponteiro cru (15,92 ms). O primeiro benchmark, só com laços
de 80 MB, era limitado por banda de memória e escondia o custo.

**Saídas escalares (A2):** D-011, zero a cada chamada, com testes.

## SIMD explícito vs. laço escalar (revisão da parte A, item 2)

### Previsões (escritas ANTES de medir)

Dados residentes em L2 (8192 elementos × 1221 passadas ≈ 10⁷ acessos), para
não repetir o erro da A3 de medir um laço limitado por banda de memória.

| Laço | Previsão |
|------|----------|
| soma `float64`, escalar vs `unsafe_load[width=8]` | **não** é autovetorizada (a soma em ponto flutuante não é associativa, e o LLVM não reordena sem fast-math); SIMD explícito 3–6× mais rápido |
| soma `int64`, escalar vs SIMD explícito | já autovetorizada (soma inteira é associativa); SIMD explícito 1,0–1,3× |
| os mesmos laços em 80 MB | ~1× (limitados por banda) |
| Evidência direta | o assembly do laço escalar `int64` terá instruções vetoriais (`vpaddq`/`ymm`); o de `float64` terá só `vaddsd` escalar |

### Medido (`tests/bench_bounds.py`, seção SIMD)

| Laço | Escalar | SIMD `width=8` | Razão | Previsão |
|------|---------|----------------|-------|----------|
| soma `float64`, L2 | 5,33 ms | 0,99 ms | **5,40×** | 3–6× — acertou |
| soma `int64`, L2 | 2,68 ms | 1,04 ms | **2,56×** | 1,0–1,3× — **errou** |
| soma `float64`, 80 MB | 5,77 ms | 3,93 ms | 1,47× | ~1× — errou (ver abaixo) |
| soma `int64`, 80 MB | 4,37 ms | 4,00 ms | 1,09× | ~1× — acertou |

Todos com resultado idêntico. Os dados são inteiros e a soma cabe em 2⁵³, então a
reassociação do SIMD não muda o `float64`.

**O mecanismo, verificado no assembly (`mojo build -O3 --emit asm`):**
- A previsão partia de que o LLVM autovetoriza a soma inteira. **Não
  autovetoriza nada.** Até o laço mais trivial, `q[i] = p[i] + 1` em `int64` sem
  nenhuma redução, sai como `movq`/`incq`/`movq` escalar, um elemento por
  iteração e sem desenrolar. A soma `int64` sai como `addq` escalar.
- A primeira hipótese para o `int64` foi aliasing: o acumulador `mut s` é uma
  referência que poderia apontar para `xs`. **Foi refutada:** acumular numa `var`
  local dá o mesmo tempo (2,68 vs 2,69 ms), e o assembly continua sem instruções
  vetoriais.
- O alvo tem AVX (o `float64` usa `vaddsd`, codificado em VEX); o que falta é a
  vetorização de laços. Pode ser uma escolha deliberada do pipeline do Mojo, que
  favorece SIMD explícito via `vectorize`, mas não verifiquei isso na
  documentação: está registrado como comportamento observado no Mojo 1.1.0.
- A soma `float64` em 80 MB ganhou 1,47× porque o laço escalar não é limitado
  pela banda, mas pela latência: é uma cadeia de `vaddsd` dependentes, ~0,44 ns
  por soma. O SIMD usa 8 acumuladores independentes. O `int64` escalar (`addq`,
  latência de 1 ciclo) já estava perto da banda, e por isso ganhou só 1,09×.

**Consequência para as células:**
- SIMD explícito rende 2,5–5,4× em laços que não são limitados por memória. Ele
  vem de `xs.unsafe_ptr().unsafe_load[width=W](i)` e `unsafe_store(i, vec)`, a
  API do stdlib, sem camada própria (D-010).
- A API principal continua sendo `unsafe_get`/`unsafe_set`, e `[]` continua
  checado.
