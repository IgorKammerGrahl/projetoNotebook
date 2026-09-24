# Fase 0 — Spike de viabilidade

Ambiente: Linux x86_64 (CachyOS, 16 threads, 15 GB), pixi, canal stable
`conda.modular.com/max`. **Mojo 1.1.0** (o stable já está em 1.1, não 1.0),
Python 3.14.7, NumPy 2.5.3. Reproduzir: `pixi run bash spikes/<n>/run.sh`.
Todos os números são o melhor de N repetições nesta máquina, salvo indicação.

## Previsão vs. medida

| # | Item | Previsão (antes de medir) | Medido | Diferença |
|---|------|---------------------------|--------|-----------|
| 1 | Python←Mojo, chamada trivial | 100–300 ns | 80–150 ns | dentro |
| 1 | Python←Mojo, `np.sum` 10 el. | ~1 µs | 1,2 µs | dentro |
| 2 | Mojo←Python (`PythonModuleBuilder`) | 100 ns–1 µs | **~6 ns** acima de uma função Python (34 vs 28 ns) | muito melhor |
| 3 | Cópia 100 MB | 10–20 ms | 12 ms (memcpy Mojo) / 24 ms (`np.copy`) | dentro |
| 3 | Zero-copy 100 MB | < 50 µs | **< 1 µs** nos dois sentidos | melhor |
| 4a | Build a frio, célula 20 linhas | 1,5–3 s | 2,9 s (`-O3`) / 4,5 s (`-O0`) | dentro; `-O0` mais lento, inesperado |
| 4b | Com cache, mesmo fonte | < 50 ms | 3 ms (`mojo.importer`, hit) / 0,5 s (`mojo build`, cache do compilador) | dentro |
| 4c | Após editar 1 linha | igual ao frio | igual ao frio (2,9 s) | dentro |
| 4c' | Idem, célula com ABI C (sem bindings Python) | — | **1,3 s** | não previsto |
| 4d | JIT via API | não há | não há API; `mojo run` = 1,4 s por edição, processo separado | dentro |
| 5 | Host | Python | Python | — |
| 6 | Kernel oficial | existe, fechado | existe, fechado, e não sobe sem `ipykernel` | — |

## 1. Chamar Python a partir de Mojo — `spikes/1_py_from_mojo`

`from std.python import Python, PythonObject`; `Python.import_module`,
`Python.evaluate(src, file=True)`. Conversão de volta exige `Int(py=obj)`.

| Operação (200k chamadas) | ns/chamada |
|---|---|
| `noop(x)` (função Python) | 125–155 |
| `add1(int)` | 80–115 |
| `np.sum(arange(10))` | 1150–1230 |
| `Int(py=obj.sum())` (método + conversão) | 580–620 |
| `import numpy` (inclui init do CPython) | 55–630 ms (1ª vez fria) |

**Limitações observadas:**
- O binário Mojo resolve `libpython` **em tempo de execução**. Fora do pixi
  (`env -i`) ele carregou o Python 3.14 do sistema, com outro NumPy, sem
  erro nem aviso. Um host Mojo precisa fixar o interpretador (`MOJO_PYTHON_LIBRARY`).
- Funções que tocam Python têm que ser `raises`; exceções Python viram `Error`.
- Não há lambda em Mojo; callables vêm de `Python.evaluate`.

## 2. Chamar Mojo a partir de Python — `spikes/2_mojo_from_py`

Existe e funcionou de primeira no 1.1: `@export def PyInit_<mod>() abi("C")` +
`PythonModuleBuilder`, `mojo build --emit shared-lib`, `import` normal.
`mojo.importer` (no pacote `mojo`) compila automaticamente e guarda o `.so` em
`__mojocache__/`, com chave = sha256 de todos os `.mojo` do diretório.

| Medida | Valor |
|---|---|
| build `-O3` do módulo | 3,0 s |
| `import` do `.so` | 28 ms |
| chamada `noop` (via lambda, timeit) | 34 ns (baseline Python: 28 ns) |
| chamada `add(1, 2)` com conversões | 69 ns |
| laço de 10⁷ iterações: Mojo vs Python puro | 42 ms vs 458 ms (11×) |

## 3. Passagem de dados sem cópia — `spikes/3_zero_copy`

Array de 100 MB (`float64`, C-contíguo).

| Sentido | Mecanismo | Tempo |
|---|---|---|
| NumPy→Mojo, zero-copy | `arr.ctypes.data.unsafe_get_as_pointer[DType.float64]()` | 0,93 µs (O(1)) |
| NumPy→Mojo, cópia | `unsafe_memcpy` para buffer Mojo | 12,4 ms |
| NumPy→Mojo, ingênuo | `Float64(py=arr[i])` elemento a elemento | 0,9 s (extrapolado) |
| Mojo→NumPy, zero-copy | struct Mojo com `Allocation` + wrapper Python com `__array_interface__` | 0,76 µs |
| Mojo→NumPy, cópia | `np.array(..., copy=True)` | 14,6 ms |
| baseline | `np.copy` | 23,8 ms (inclui page faults de memória nova) |
| leitura dos 100 MB em Mojo (laço escalar) | `sum_view` | 7,5 ms (`np.sum`: 4,9 ms) |

Validado: escrita in-place de Mojo é visível no NumPy e vice-versa; resultado
de `sum_view` idêntico a `np.sum`.

**Ownership (Mojo→NumPy):** o array NumPy guarda o wrapper em `.base`; o
wrapper guarda o objeto Mojo. O `MojoBuf` só morre com o último array, o que foi
verificado com `weakref`. Numa build de debug com `print` no `__deinit__`, a
liberação aconteceu em todos os casos.

**Achado:** o RSS não cai depois de liberar. O alocador do runtime Mojo retém os
blocos liberados, mas os reusa: ciclos repetidos de alloc/free de 100 MB
estabilizam em +100 MB e não crescem mais. `malloc_trim` não afeta, então não é
o glibc. Para o kernel, significa que memória liberada por células Mojo não volta
ao sistema operacional.

**Não medido:** arrays não contíguos ou com strides, outros dtypes, e acesso de
múltiplas threads com o GIL liberado. O lado Python precisa validar dtype e
contiguidade antes de passar o ponteiro, e esse é o limite de confiança.

## 4. Tempo de compilação — `spikes/4_compile_time`

Célula de ~20 linhas (função numérica + export). Cache do compilador em
`mojo --print-cache-location` (endereçado por conteúdo).

| Cenário | `-O0` | `-O3` |
|---|---|---|
| frio (cache do compilador limpo) | 4,5 s | 2,9 s |
| mesmo fonte, cache quente | 0,73 s | 0,53 s |
| após editar 1 linha | 4,6 s | 2,9 s |

| Outras variantes (após edição) | Tempo |
|---|---|
| mesma célula, **ABI C pura** (`@export ... abi("C")`, carregada com `ctypes`) | **1,3 s** |
| célula sem Python, `mojo build` executável | 1,5 s |
| `mojo run` (JIT) sem Python | 1,4 s |
| `mojo run` (JIT) com `PythonObject`/bindings | 3,0 s |
| `mojo.importer`, hash igual em processo novo | 3 ms |

**Conclusões:**
- Os ~1,5 s extras vêm da camada de interop Python (`PythonObject` +
  `PythonModuleBuilder`), não de linkagem nem do formato `.so`.
- Não há compilação incremental: qualquer edição recompila o arquivo inteiro. O
  cache do compilador só ajuda com fonte idêntico.
- `-O0` é **mais lento** que `-O3` nesta versão. Não investiguei a causa; basta
  usar `-O3`.
- Não há API de JIT pública. O `mojo run` faz JIT, mas em processo próprio, sem
  estado compartilhado com o host.
- Custo da ABI C por chamada via `ctypes`: 239 ns, contra 34 ns com
  `PythonModuleBuilder`. Irrelevante para células, que são chamadas poucas vezes.

**Recarregar célula editada no mesmo processo** (crítico para o notebook):
- `importlib.reload()` com `mojo.importer` **devolve silenciosamente o código
  antigo**, e ainda gasta 2,9 s recompilando. Não serve.
- Carregar o `.so` de outro caminho (`spec_from_file_location` ou
  `ctypes.CDLL`) funciona, inclusive com o mesmo nome de módulo: as duas versões
  coexistem no processo.
- O `.so` antigo nunca é descarregado. Cada edição de célula Mojo vaza a imagem
  do `.so` (~250 KB) até o kernel reiniciar.

## 5. Qual processo hospeda o kernel

| Critério | A: host Python carrega `.so` Mojo | B: host Mojo embute Python |
|---|---|---|
| Células Python | nativas (`exec`), custo zero | cada operação via `PythonObject`, ~100 ns + `raises` |
| Células Mojo | compila em subprocesso, carrega `.so` versionado (medido) | também precisa compilar `.so` e carregar dinamicamente; não há JIT embutível |
| Dados | zero-copy medido nos dois sentidos (< 1 µs) | idem, mesmo mecanismo |
| Latência de edição Mojo | 1,3 s (ABI C) / 2,9 s (bindings) | igual, compilador é o mesmo |
| Motor reativo (Fase 1, `ast`) | Python puro | teria que chamar `ast` via interop |
| Riscos | `.so` antigos não descarregam; RSS retido | resolução de `libpython` em runtime (pegou o Python errado); tudo acima também |

B não tem vantagem medida: o gargalo (compilar Mojo) é idêntico e B piora as
células Python e o motor.

### Recomendação

**Host Python (opção A).** Para as células Mojo:

1. Compilar cada célula com `mojo build -O3 --emit shared-lib`, em subprocesso,
   num arquivo com nome derivado do hash (conteúdo + dependências). Carregar pelo
   caminho, nunca por `importlib.reload`.
2. Contrato padrão da célula: **ABI C** (`@export ... abi("C")`, com
   ponteiro + tamanho), carregado com `ctypes`. É o caminho de menor latência de
   edição (1,3 s).
3. Memória dos dados: por padrão fica **do lado Python**. O Python aloca os
   arrays NumPy, inclusive os de saída, e passa endereços ao Mojo. Isso evita
   ownership cruzado. O padrão `MojoBuf` + `__array_interface__` funciona e está
   validado, mas exige bindings (+1,5 s por edição); fica para quando for
   necessário.
4. Usar `-O3`, que é mais rápido de compilar que `-O0`.

Decisão registrada em `docs/DECISIONS.md` (D-001).

## 6. Kernel Jupyter / REPL oficial

- **Kernel Jupyter:** vem no pacote `mojo`
  (`share/jupyter/kernels/mojo/mojokernel.py`). É um wrapper `ipykernel` fino
  sobre `libMojoJupyter.so`, que é **fechado** e dirige o `mojo-repl-entry-point`
  (REPL baseado em LLDB). Só executa Mojo; não compartilha estado com células
  Python. Nesta instalação ele nem sobe, porque `ipykernel` não é dependência
  declarada. **Não é reaproveitável** para o nosso grafo.
- **`mojo repl`:** pode ser dirigido por stdin e mantém estado entre entradas
  (`var x = 40` → `print(x + 2)` imprime `42`), com saída no formato LLDB
  (`(Int) x = 40`). Leva ~2 s para subir. Mesmo problema: processo à parte.
- **Referência útil:** `mojo/notebook.py`, a magic `%%mojo` para IPython. Grava a
  célula num tempdir e chama `mojo build --emit shared-lib`. É exatamente a
  abordagem recomendada acima, sem o grafo e sem a troca de dados.

## Riscos e pendências para a Fase 2

1. `.so` de células editadas não são descarregados (vazamento por edição).
   Reinício do kernel é o único limpador. Candidato a DEBTS quando houver código
   de produto.
2. RSS retido pelo alocador Mojo depois de liberar buffers grandes.
3. Latência mínima de edição→resultado para Mojo ≈ 1,3 s (compilador). `-g0`
   não ajudou (2,96 s vs 2,9 s com bindings). Nada medido aqui baixou de 1,3 s.
4. Não medido: dtypes além de `float64`, arrays não contíguos, GPU (fora de
   escopo). GIL/threads: ver spike 7.
5. O stable já é 1.1.0, então as APIs podem mudar em minors. O `memcpy`, por
   exemplo, já foi renomeado para `unsafe_memcpy`, e `UnsafePointer` foi
   depreciado em favor de `Pointer`.
6. A registrar em DEBTS.md quando ele existir (revisão, item 6): `.so` antigos
   acumulando até reiniciar o kernel; dtypes além de `float64` e arrays não
   contíguos não medidos.

---

# Spike 7 — Célula C-ABI usando Python + callback de alocação

`spikes/7_cabi_python`. Responde aos itens 2 e 3 da revisão da Fase 0.

## Previsões (escritas ANTES de medir)

| Item | Previsão |
|------|----------|
| Interpretador visto pela célula C-ABI | O do host (`Py_IsInitialized` já é verdadeiro; o runtime Mojo não inicializa outro). Marcador `sys._host_marker` visível. |
| `numpy.linalg` dentro da célula | Funciona via `PyDLL` |
| `CDLL` + célula tocando Python | O runtime Mojo **não** pega o GIL sozinho. Sozinho pode parecer funcionar; com thread Python concorrente: crash ou corrupção de refcount |
| `PyDLL` | Seguro (GIL mantido durante a chamada) |
| Custo por chamada do callback de alocação (`CFUNCTYPE` + `np.empty`) | 1–3 µs |
| Callback chamado com o GIL solto (`CDLL`) | Funciona: callbacks `ctypes` fazem `PyGILState_Ensure` |

## Previsão vs. medida

| Item | Previsão | Medido | Diferença |
|------|----------|--------|-----------|
| Interpretador | o do host | o do host: marcador `4242` visível; `PyImport_ImportModule` resolvido dentro do próprio `bin/python` | dentro |
| `numpy.linalg` na célula (`PyDLL`) | funciona | `norm(arange(10))` = 16.881943, idêntico ao NumPy | dentro |
| `CDLL` + Python | crash só sob concorrência | **SIGSEGV em 100% das execuções, já na 1ª operação Python, sem thread concorrente** | pior e determinístico |
| `PyDLL` + thread Python mutando a mesma lista | seguro | 5/5 execuções × 1 M appends: 0 perdidos, 0 crashes | dentro |
| Custo por callback de alocação | 1–3 µs | **1,05–1,13 µs** (`np.empty` sozinho: 0,14 µs) | dentro |
| Callback com GIL solto (`CDLL`) | funciona | funciona (collatz correto, 1,05 µs) | dentro |
| Callback que levanta exceção | — | **SIGABRT/SIGSEGV**: o `ctypes` devolve valor indefinido (não 0) e a célula escreve num endereço lixo | não previsto |
| Build após edição, C-ABI usando Python | — | 2,0–2,6 s (só buffers: 1,3 s; `PythonModuleBuilder`: 2,9 s) | não previsto |

## 2. Célula C-ABI chamando Python

- O runtime Mojo **não inicializa outro interpretador**. Ele resolve a C-API
  do CPython em runtime (`dlopen`/`dlsym`) e encontra a do processo host. O único
  símbolo `Py*` estático no `.so` é o `PyGILState_Check` que chamei explicitamente.
- O runtime Mojo **não adquire o GIL**. Com `ctypes.CDLL` (GIL solto,
  `PyGILState_Check() == 0` na entrada), a primeira chamada Python dentro da
  célula (`PyImport_ImportModule`) segfaulta, determinístico e sem concorrência.
  Com `ctypes.PyDLL` (GIL mantido, `== 1`) tudo funciona, inclusive com uma
  thread Python do host disputando a mesma lista.
- **Detectar uso de Python pelo binário:** toda célula que toca Python importa
  `KGEN_CompilerRT_Python_SetPythonPath` e `dlsym`, mesmo com um único
  `Python.import_module`. A célula só com buffers não importa nenhum dos dois
  (verificado com `nm -D --undefined-only`). É um critério objetivo, que não
  depende de declaração do usuário.
- Custo do `PyDLL`: segura o GIL durante a célula inteira. Uma célula Mojo longa
  bloqueia qualquer outra thread Python do kernel (ex.: o servidor WebSocket da
  Fase 3). O `CDLL` permite que elas rodem, mas só para células sem Python.

## 3. Callback de alocação

Contrato: `comptime AllocFn = def(Int) thin abi("C") -> Int`, com nbytes →
endereço, e 0 = falha. O parâmetro é declarado direto na assinatura da função
exportada; o `ctypes` passa o `CFUNCTYPE` como ponteiro de função, sem cast.
O host cria um `np.empty(nbytes, uint8)`, guarda-o numa lista dele e devolve
`a.ctypes.data`. A célula decide o tamanho em runtime (teste: sequência de
Collatz de 27, com 112 passos).

- Overhead: ~1,1 µs por alocação, das quais 0,14 µs são o `np.empty`. Para
  100 MB, não há diferença mensurável entre pré-alocar e usar o callback
  (12,7 ms vs 12,7 ms). Só pesa em células que pedem milhares de buffers
  pequenos.
- Ownership: o buffer é um `ndarray` comum. Ele morre quando o host solta a
  referência (verificado com `weakref`). O Mojo nunca é dono.
- **O callback precisa capturar toda exceção e devolver 0.** Sem essa guarda, o
  processo morreu (SIGABRT no run, SIGSEGV isolado). Com ela, a célula recebe 0
  e retorna -1. Esse é o limite de confiança: o host valida, e a célula checa 0.
- Funciona com GIL solto (`CDLL`), porque o callback `ctypes` adquire o GIL
  sozinho.

## Conclusão do spike 7

A C-ABI é segura para células que usam Python **somente se carregada com
`PyDLL`**, e custa 2,0–2,6 s por edição (contra 2,9 s do `PythonModuleBuilder`).
A binding layer não é necessária para chamar Python. Proposta em
`docs/DECISIONS.md` (D-002): contrato único C-ABI, com o carregador escolhido
pelos símbolos do `.so` compilado.

**Não medido:** células que liberam o GIL internamente e o readquirem (não há
API exposta para isso no `std.python` que eu tenha verificado), e o Python
free-threaded (3.14t). Neste build, `sys._is_gil_enabled()` é `True`.
