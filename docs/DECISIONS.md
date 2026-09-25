# Decisões de arquitetura

## D-001 — Host do kernel: processo Python (2026-09-23)

**Contexto:** células Python e Mojo no mesmo DAG, trocando dados. Mojo não tem
JIT embutível. Toda célula Mojo precisa virar `.so` via `mojo build`
(1,3–2,9 s por edição). Evidência em `docs/SPIKE.md`.

**Opções:**
- A. Processo Python carrega células Mojo compiladas.
- B. Processo Mojo embute CPython.

**Escolha:** A.

**Motivo:** o custo dominante (compilar Mojo) é igual nas duas opções. A deixa
Python e o motor reativo (`ast`) nativos, e o zero-copy NumPy↔Mojo foi medido
abaixo de 1 µs nos dois sentidos. B resolveu `libpython` do sistema em runtime,
sem aviso. Células Mojo são carregadas por caminho versionado, nunca por
`importlib.reload`, que devolve o código antigo. O contrato da célula está em
D-002.

## D-002 — Contrato de célula Mojo: C-ABI única, carregador escolhido pelo binário (2026-09-23)

**Contexto:** o spike 7 mostrou que o runtime Mojo não adquire o GIL. Uma célula
C-ABI que toca Python segfaulta sob `ctypes.CDLL` (100% das execuções) e é
segura sob `ctypes.PyDLL`, inclusive com uma thread concorrente. Tempos de build
por edição: 1,3 s (só buffers), 2,0–2,6 s (C-ABI + Python), 2,9 s
(`PythonModuleBuilder`).

**Opções:**
- a. C-ABI + `CDLL` sempre: inseguro.
- b. Híbrido: C-ABI para células só com buffers, binding layer para células com
  Python. Seguro, mas são dois contratos, e o mais lento (2,9 s).
- c. C-ABI + `PyDLL` sempre: seguro, mas segura o GIL até em células puramente
  numéricas.
- d. C-ABI sempre, com o carregador escolhido pelos símbolos do `.so`.

**Escolha:** d. Depois de compilar, o kernel lê os símbolos indefinidos
(`nm -D --undefined-only`, ou `pyelftools` se `nm` não estiver disponível). Se
aparecer qualquer símbolo que case com `Python`, `dlsym` ou `^Py`, usa `PyDLL`;
senão, `CDLL`. A regra é **fail-closed**: na dúvida, `PyDLL`, porque errar para
esse lado custa só concorrência, e errar para o outro derruba o processo.

**Motivo:** um contrato só; a célula numérica mantém 1,3 s e solta o GIL, e a
célula com Python fica segura sem pagar a binding layer. O critério vem do
binário, não de uma declaração do usuário. A binding layer
(`PythonModuleBuilder`) fica reservada para expor tipos Mojo como objetos Python,
fora do padrão.

## D-003 — Memória de dados: posse do Python, saída de tamanho dinâmico via callback (2026-09-23)

**Contexto:** a alocação pelo Python foi aprovada, mas algumas saídas só têm
tamanho conhecido depois do cálculo.

**Escolha:** a célula recebe `AllocFn = def(Int) thin abi("C") -> Int` (nbytes →
endereço; 0 = falha). O host cria um `np.empty`, guarda a referência e devolve o
endereço; o Mojo nunca é dono. O custo é ~1,1 µs por alocação, e não há
diferença mensurável em 100 MB. Tamanho conhecido de antemão continua sendo
passado pré-alocado.

**Regras de confiança:**
1. O callback do host captura **toda** exceção e devolve 0. Uma exceção
   atravessando o `ctypes` gera um valor indefinido e já derrubou o processo no
   spike.
2. A célula trata 0 como falha antes de escrever.
3. O objeto `CFUNCTYPE` fica vivo durante toda a chamada.

## D-004 — Chave do cache de compilação (2026-09-23)

**Escolha:** a chave é o hash do **código da célula + assinatura da interface**
(nomes e tipos importados/exportados). **Não** inclui hashes das dependências.

**Motivo:** o `.so` depende só do código e da forma dos dados, nunca dos valores.
Dados novos a montante significam reexecutar a célula, nunca recompilar. Uma
recompilação custa 1,3–2,9 s; uma reexecução custa µs a ms.

## D-005 — Parâmetros ajustáveis de células Mojo vêm de células Python (2026-09-23)

**Escolha:** constantes que o usuário quer ajustar (limiares, tamanhos,
iterações) entram na célula Mojo como inputs vindos de células Python, não como
literais no código Mojo.

**Motivo:** com D-004, mudar um input dispara só a reexecução. Mudar um literal
muda o hash e custa uma compilação.

## D-006 — Verificação do ambiente no boot do kernel (2026-09-23)

**Contexto:** no spike 1, um binário Mojo fora do pixi carregou o Python e o
NumPy do sistema sem avisar.

**Escolha:** no boot, o kernel verifica que `sys.prefix` é o ambiente pixi do
projeto e que `numpy.__file__` está dentro dele. Se não estiverem, falha com erro
claro, dizendo o que foi carregado e o que era esperado.

## D-007 — Formato do arquivo do notebook: Markdown com blocos cercados (2026-09-23)

**Contexto:** o formato precisa ser texto puro, amigável a git e diff, e comportar
células Python, Mojo, Markdown e HTML.

**Opções:**
- a. `.py` estilo marimo (`@app.cell def ...`): válido como Python, mas
  células Mojo e HTML viram strings escapadas, e o diff mostra o boilerplate.
- b. `.py` com marcadores `# %%` (jupytext percent): Mojo e HTML também
  viram comentários ou strings.
- c. Markdown (`.nb.md`): texto solto = célula Markdown; blocos cercados com info
  string `python`, `mojo` ou `html` = células de código. Renderiza no GitHub,
  e o diff de uma edição mostra só as linhas editadas.

**Escolha:** c.

**Regras:**
- Uma célula de código é uma cerca de 3 ou mais crases ou tis **na coluna 0**,
  com info string exatamente `python`, `mojo` ou `html`. O fechamento usa o
  mesmo caractere, com pelo menos o mesmo comprimento (CommonMark). Ao salvar,
  o writer escolhe uma cerca maior que a maior sequência de crases do conteúdo.
- Cercas com outra info string (ex.: ```` ```bash ````) fazem parte da célula
  Markdown ao redor, e o que está dentro delas nunca abre célula.
- O texto entre duas células de código é **uma** célula Markdown, sem linhas em
  branco nas bordas. Por isso, duas células Markdown adjacentes se fundem ao
  salvar (DEBT-006).
- Não há ids de célula no arquivo; a identidade das células existe só na memória
  do kernel. Isso mantém o diff limpo, mas identificar uma célula entre versões do
  arquivo fica para quando existir `watch` ou frontend.

## D-008 — Regras do grafo Python (2026-09-23)

**Escolha:**
- As definições e referências de cada célula vêm de `symtable` (stdlib), com três
  correções pelo `ast` do nível de topo:
  - `x += 1` também conta como referência;
  - `del x` é referência, não definição;
  - o nome de `except ... as e` não é definição, porque o Python o apaga no fim
    do bloco.
  - Variáveis de compreensão ficam de fora (`is_comp_iter`). Em escopos
    aninhados, contam só os nomes que resolvem para global (`is_global`).
- Nomes que começam com `_` são privados da célula: ficam fora do grafo e não
  entram na regra de definição única (convenção do marimo).
- Cada nome global tem uma única célula definidora. Todas as células que definem
  o mesmo nome ficam em erro (`multiple-definition`); ciclo = erro (`cycle`); os
  descendentes de uma célula com erro ficam `blocked`.
- Ao editar uma célula, as raízes são a própria célula + as células que definem ou
  referenciam algum nome que ela definia antes ou define agora. Reexecutam as
  raízes e seus descendentes, em ordem topológica com desempate pela posição no
  arquivo. Antes de rodar, os nomes que cada uma dessas células definiu na última
  execução são apagados do namespace.
- Mutação in-place não é rastreada (DEBT-003).

## D-009 — Convenção da célula Mojo (2026-09-23) — **substituída por D-010**

**Contexto:** a célula Mojo precisa declarar o que importa e exporta do grafo sem
que o kernel precise de um parser de Mojo. A execução segue D-002 (C-ABI,
carregador pelos símbolos) e D-003 (memória do Python, alocação por callback).

**Opções:**
- a. O kernel lê a assinatura de `def run(...)`: exige parsear Mojo.
- b. Cabeçalho em comentários (`# in:` / `# out:`) como fonte única, e o usuário
  escreve `run` com os parâmetros na ordem declarada; o compilador confere.
- c. Cabeçalho + só o corpo de `run` (o kernel gera a assinatura): impede o
  usuário de definir funções e structs auxiliares.

**Escolha:** b.

```mojo
# in: xs: Float64[]
# in: k: Float64
# out: ys: Float64[]
# out: total: Float64
def run(xs: ArrayIn[DType.float64], k: Float64,
        mut ys: ArrayOut[DType.float64], mut total: Float64) raises:
    ys.alloc(len(xs))
    for i in range(len(xs)):
        ys[i] = xs[i] * k
        total += ys[i]
```

**Regras:**
- As linhas `# in: nome: Tipo` e `# out: nome: Tipo` ficam na coluna 0, em
  qualquer ponto da célula. Os nomes são identificadores Python sem `_` inicial e
  únicos na célula. Os `in` são as referências da célula no grafo, e os `out` são
  as definições.
- Tipos: `Int`, `Float64` (escalares) e `Float64[]`, `Float32[]`, `Int64[]`,
  `Int32[]` (arrays 1-D). Em `run`, o array de entrada é
  `ArrayIn[DType.x]` (somente leitura) e o de saída é `mut ArrayOut[DType.x]`;
  o escalar de saída é `mut` e começa em 0.
- `run` recebe os `in` e depois os `out`, na ordem do cabeçalho.
- Um array de saída tem de ser alocado uma vez com `.alloc(n)`, quando o tamanho
  for conhecido; isso aciona o callback de D-003. Os acessos por `[]` têm checagem
  de limites e levantam `Error`. `unsafe_ptr()` existe para laços quentes.
- O código do usuário vem **primeiro** no arquivo gerado, seguido do prelúdio e
  do wrapper `@export`. Assim, a linha N do compilador é a linha N da célula.
  Erros em linhas depois da célula vêm do código gerado, quase sempre por
  assinatura de `run` divergente, e são mostrados com a assinatura esperada.
- O prelúdio não importa `std.python`. Assim, o carregador (D-002) reflete só o
  código do usuário.
- A chave do cache é o sha256 do código da célula + prelúdio + versão do `mojo`.
  O cabeçalho faz parte do código, então a assinatura da interface já entra
  (D-004). Arquivo: `<cache>/<chave>.so`, escrito via rename atômico e
  carregado por caminho (D-001).
- Validação no host, antes de qualquer ponteiro sair do Python: `ndarray`, dtype
  exato, 1-D, C-contíguo e alinhado. Escalares com tipo checado (`bool` não é
  `Int`). Nada é convertido em silêncio.

## D-010 — Interface da célula Mojo derivada da assinatura de `run` (2026-09-23, substitui D-009)

**Contexto:** na revisão da Fase 2, o cabeçalho `# in:`/`# out:` foi
considerado redundante com a assinatura de `run`. A decisão continua sendo não
escrever um parser de Mojo.

**Escolha:** uma gramática restrita, aplicada só à assinatura de `run`, extrai a
interface:

```
def run(<param>, ...) [raises]:          # exatamente um, na coluna 0; pode quebrar linhas
<param> := name: Int | Float64 | ArrayIn[DType.<dt>]          # entrada
         | mut name: Int | Float64 | ArrayOut[DType.<dt>]     # saída
<dt>    := float64 | float32 | int64 | int32                  # `.float64` também aceito
```

**Regras:**
- Sem `mut` = entrada; com `mut` = saída.
- `mut x: ArrayIn` é proibido (entrada-e-saída: cada nome tem um único dono no
  grafo). `ArrayOut` sem `mut` também é proibido.
- Fora da gramática é erro de sintaxe, apontando a linha do parâmetro e o formato
  aceito: valor default, parênteses dentro dos parâmetros, tipo de
  retorno, parâmetros `[...]`, nome privado (`_x`), nome repetido, zero ou dois
  `def run` na coluna 0. Esses erros nunca chegam ao compilador.
- Helpers, structs e métodos chamados `run` (indentados) continuam permitidos.
- O wrapper chama `run` na ordem da assinatura. Um erro em código gerado agora só
  pode vir de colisão com nomes do prelúdio, e a dica diz isso.
- O resto de D-009 continua valendo: código do usuário primeiro (a linha N do
  compilador é a linha N da célula); prelúdio sem `std.python`; chave de cache =
  código + prelúdio + versão do `mojo`; validação das entradas no host.
- Acesso sem checagem de limites: `unsafe_get(i)` e `unsafe_set(i, v)`, seguindo
  a convenção `unsafe_` do Mojo 1.x (como `List.unsafe_get`). É a API principal. O
  `[]` continua com checagem. Medição em `docs/PHASE2.md`.
- `unsafe_ptr()` devolve o `Pointer` do stdlib, para SIMD
  (`unsafe_load[width=W](i)`, `unsafe_store(i, vec)`) ou `vectorize`. Não há API
  SIMD própria. Isso importa porque o Mojo 1.1 não autovetoriza laços, e o SIMD
  explícito rende 2,5–5,4× (medido). Em `ArrayOut`, `unsafe_ptr()` levanta erro
  antes de `alloc()`: o `Pointer` do stdlib é não-nulo por projeto, e criá-lo a
  partir do endereço 0 seria comportamento indefinido.
- Comentários de fim de linha (`# ...`) são permitidos dentro da lista de
  parâmetros. Eles viram espaços **antes** de procurar o `)` de fechamento (um
  `)` dentro do comentário não fecha a lista), e os offsets se preservam, então
  as linhas dos erros continuam certas. É seguro porque defaults são proibidos:
  não existe string na assinatura.

## D-011 — Saídas escalares começam em zero (2026-09-23)

**Contexto:** uma saída escalar (`mut n: Int`) que `run` não atribui precisava de
um contrato.

**Opções:**
- a. O host inicializa com zero (`0` / `0.0`) a cada chamada.
- b. Toda saída escalar precisa ser atribuída explicitamente.

**Escolha:** a.

**Motivo:** b não é aplicável de forma confiável.
- O Mojo só obriga a inicializar parâmetros `out`, e **rejeita mais de um `out`**
  por função (medido: "function may not have multiple 'out' arguments"). Com duas
  saídas escalares, o compilador não ajuda.
- Detectar em runtime exigiria um valor sentinela, e `Int` não tem nenhum livre.
  Para `Float64`, o NaN colide com um resultado legítimo.
- Com zero, o padrão acumulador (`total += ...`) funciona direto.

**Garantias testadas:**
- Zero a cada chamada: nada acumula entre reexecuções.
- Tudo ou nada: se `run` levanta, nenhuma saída é publicada.

---

# Fase 3 (B) — proposta aprovada com ajustes; notas de implementação em cada decisão

## D-012 — Dois processos: servidor e kernel (proposta)

**Contexto:** revisão B1. Hoje o `Engine` mistura três coisas: grafo e estado
(o que rodar e quando), namespace (os dados) e execução. Um crash numa célula
Mojo leva tudo junto (DEBT-008), e uma compilação trava tudo (DEBT-010).

**Proposta:**
- **Servidor** (Python, `asyncio`, um processo):
  - é dono do arquivo `.nb.md`, da análise e do grafo, do estado de cada célula
    e das compilações Mojo;
  - fala WebSocket com o frontend;
  - não guarda dado de usuário, só previews.
- **Kernel** (processo filho do servidor):
  - é dono do namespace; executa células Python e chama células Mojo já
    compiladas;
  - executa uma requisição por vez;
  - não sabe nada do grafo: recebe ordens do tipo "apague estes nomes, rode
    esta célula" e devolve status, saída e previews.
- **Divisão do código atual:**
  - `kernel/engine.py` se divide em lógica de grafo (sem execução) e executor;
  - a lógica de grafo vira uma máquina de estados pura, testável sem
    processos: recebe eventos (edição, compilação terminou, execução terminou,
    kernel morreu) e emite ações;
  - `analyze`, o formato e a gramática Mojo não mudam.
- **CLI:** usa o mesmo servidor sem WebSocket (servidor + kernel filho). Fica uma
  implementação de cada peça, não duas.

**Protocolo servidor↔kernel:**
- JSON delimitado por linha sobre um `socketpair` passado ao filho
  (`pass_fds`), **não** sobre stdin/stdout. O kernel redireciona os fds 1 e 2
  para capturar o `print` do Mojo (D-009), e isso corromperia um canal em
  stdout.
- O stdout e o stderr do kernel fora de células vão para o log do servidor.
- Mensagens:
  - `run {cid, kind, code | so_path+loader+iface, delete: [names]}` →
    `{status, error, output, previews: {name: preview}}`;
  - `delete {names}`;
  - `ping`.

**Previews:**
- Para cada nome definido, o kernel devolve:
  - o tipo;
  - para `ndarray`: `shape`, `dtype` e os 20 primeiros elementos;
  - para os demais: um `repr` truncado em 1 KB.
- A saída da célula é truncada em 64 KB. O dado inteiro nunca sai do kernel.

**Efeito nas dívidas:** a DEBT-007 fica quase paga. Não há mais thread do
servidor imprimindo dentro do processo que captura; restam só threads criadas
pelo próprio usuário.

**Implementação (etapa 1) — o que mudou em relação à proposta:**
- `kernel/scheduler.py`: a máquina de estados pura. `kernel/executor.py`: o
  namespace e a execução. `kernel/analysis.py`: a análise de nomes.
  `kernel/engine.py` vira um driver síncrono in-process.
- **A CLI e os testes semânticos usam o driver síncrono** (scheduler + executor
  no mesmo processo), e não o servidor. Motivo: os 100 testes existentes
  continuam valendo sem subir processos. O servidor usa o **mesmo** scheduler e o
  **mesmo** executor, este dentro do processo kernel. Consequência: a CLI
  continua morrendo num SIGSEGV (DEBT-008 segue fixada nela).
- **Execução explícita (revisão, ajuste 3):** editar não executa; a célula vai
  para `edited`. Só `run` executa, propagando aos descendentes. O `Engine.edit`
  do driver síncrono é "editar e executar", o equivalente ao Shift+Enter.
- **Um pai editado e ainda não reexecutado não bloqueia os filhos:** os valores
  antigos dele continuam no executor, como no Jupyter.
- A edição acumula os nomes que a célula deixou de definir até a próxima
  execução. Sem isso, ao desfazer uma definição duplicada, o outro definidor não
  era reavaliado (bug pego pelos testes antigos).
- Um ciclo é resolvido de uma vez só. Tirar um membro da fila "quebrava" o ciclo
  no meio, e o segundo membro virava `blocked` em vez de `cycle` (bug pego pelos
  testes antigos).

## D-013 — Recuperação quando o kernel morre (proposta, paga DEBT-008)

**Contexto:** revisão B2. Um SIGSEGV numa célula Mojo não pode virar um laço de
crashes, e o resto do notebook tem de voltar ao estado anterior.

**Proposta:**
1. O servidor sabe qual célula está em execução: é a única requisição pendente
   no kernel. A morte é detectada por EOF no socket ou pela saída do processo,
   seja por sinal, `os._exit` ou OOM-kill.
2. A célula em execução vai para `crashed`, com a mensagem "kernel morreu
   (SIGSEGV) executando esta célula", e fica **em quarentena**: não roda
   automaticamente até o usuário editá-la. Os descendentes vão para `blocked`.
3. O servidor reinicia o kernel e reexecuta todas as células em ordem
   topológica, **exceto** as que estão em quarentena e seus descendentes.
   - As células Mojo não recompilam: o `.so` está no cache em disco.
4. **O laço de crashes é impossível por construção:** cada reinício com uma
   célula em execução põe pelo menos mais uma célula em quarentena. O número de
   reinícios fica então limitado pelo número de células.
   - Se o kernel morrer **sem** célula em execução (ex.: thread do usuário), o
     servidor reinicia e reexecuta.
   - Depois de 3 mortes seguidas desse tipo, desiste: marca o kernel como
     `dead` e informa o frontend.
5. **"Voltar ao estado anterior" significa reexecutar, não restaurar um
   snapshot.** Uma célula não determinística (`random`, relógio) terá valor
   novo. Isso fica documentado e não é resolvido.

**Teste obrigatório:** notebook com
- A (dados Python);
- B (Python, lê A);
- C (Mojo, lê A e dá SIGSEGV);
- D (lê C);
- E (independente).

Ele verifica que:
- houve **exatamente um** reinício;
- C está em `crashed` e D em `blocked`;
- A, B e E estão `ok`, com **os mesmos previews** de antes do crash;
- editar C para uma versão correta roda C e D, sem novo reinício.

Um segundo teste usa duas células que crasham: são dois reinícios, e nenhum
terceiro.

**Implementação (etapa 2):**
- `kernel/kernelproc.py` (o processo kernel) e `kernel/session.py` (o driver
  assíncrono).
- Todas as ações do scheduler passam por **um único worker, em ordem**, o que
  garante que o `Restart` termine antes do próximo `Exec` ser escrito.
- Há um leitor por geração do kernel, e só a geração atual reporta morte. Assim
  a mesma morte não é contada duas vezes.
- **Desvio da proposta:** uma célula `crashed` também sai da quarentena por
  **execução explícita**, não só por edição. É a regra que a revisão pediu para
  `interrupted`, estendida a `crashed`. O laço de crashes continua impossível,
  porque cada nova tentativa exige uma ação do usuário, e é isso que permite
  reexecutar uma célula inocente depois de corrigir a célula `unsafe_*` a
  montante que o próprio relato de crash aponta. A propagação automática nunca
  tira uma célula da quarentena.
- Ajuste 1 da revisão: a mensagem de crash diz em qual célula o processo morreu e
  lista as células a montante que usam `unsafe_*`, por detecção textual
  (`\bunsafe_\w+`), com as APIs encontradas.

## D-014 — Compilação assíncrona, cancelável e paralela (proposta, paga DEBT-010)

**Contexto:** revisão B3.

**Proposta:**
- **Quem compila:** o **servidor** roda `mojo build` como subprocesso
  (`asyncio.create_subprocess_exec`, em grupo de processos próprio), fora do
  kernel. Assim a compilação não bloqueia a execução, e um crash do kernel não
  mata compilações. O servidor também decide o carregador (`nm`, D-002) e
  entrega ao kernel `{so_path, loader, iface}`.
- **Estados novos:**
  - `compiling`: célula Mojo com build em andamento;
  - `stale`: descendente cujo valor atual é de antes da edição. O valor antigo
    continua visível, marcado como obsoleto, até a célula reexecutar.
  - `queued` e `running`.
  - `crashed` (D-013).
- **Cancelamento:**
  - uma nova edição numa célula em `compiling` mata o grupo do `mojo build`
    (SIGKILL) e começa outro;
  - cada job leva `(cid, sha do código)`, e um resultado que chegue de um job
    obsoleto é descartado. Isso cobre a corrida entre "terminou" e "foi morto".
- **Paralelismo:**
  - a compilação não depende de dados, então começa na hora, mesmo com os
    ancestrais obsoletos;
  - ao abrir um notebook, todas as células Mojo sem cache compilam em paralelo;
  - o limite de builds simultâneos é configurável; o padrão é
    `max(1, nproc // 4)`, porque cada `mojo build` já usa várias threads
    (5 s de CPU para 3 s de relógio, medido no spike 2).
- **Agendamento:**
  - o kernel executa em série;
  - uma célula entra na fila de execução quando todos os ancestrais estão `ok`
    e, se for Mojo, quando o `.so` da versão atual está pronto;
  - a fila anda em ordem topológica, com desempate pela posição no arquivo.
- **Apagar nomes:** acontece no momento em que cada célula vai rodar, não no
  momento da edição (o que permite o estado `stale`). A ordem topológica
  garante que os ancestrais apagam e redefinem antes dos descendentes.
- **Fora do escopo desta fase:** interromper uma célula em execução (SIGINT no
  kernel). Uma edição feita enquanto uma célula roda espera ela terminar.

**Implementação (etapa 3):**
- `mojo.build_async` compila para `<chave>.<uuid>.tmp` e só publica com
  `os.replace` quando o build dá certo. O build roda em sessão própria
  (`start_new_session`); ao ser cancelado, o grupo de processos inteiro leva
  `SIGKILL`, e o `.tmp` é apagado.
- Teste: matar o build no meio não publica nem `.so` nem `.tmp`, não deixa
  processo `mojo build` vivo, e a mesma chave compila normalmente depois.
- A compilação começa quando a célula é **executada** (ajuste 3: execução
  explícita), não quando é editada. Editar uma célula que está compilando mata o
  build. Se a célula ainda estiver na fila, o build recomeça com o código novo;
  um resultado que chegue atrasado de um build antigo é descartado.

## D-015 — Frontend (proposta)

- React + TypeScript + Vite.
- **Editor:** CodeMirror 6. É leve, tem modo Python e o destaque de Python serve
  de aproximação para Mojo. Monaco é pesado demais para N editores numa página.
- **Debounce (revisão B4):**
  - o texto vai ao servidor depois de 400 ms sem digitação nas células Python;
  - nas células Mojo, 1000 ms, porque cada envio custa ~1,6 s de compilação e
    mata a anterior;
  - `Shift+Enter` envia na hora;
  - os dois valores são configuráveis;
  - o servidor também agrega: de várias edições pendentes para a mesma célula,
    só a última conta (o mesmo mecanismo de D-014).
- **Estados visuais:** `stale`, `queued`, `compiling`, `running`, `error`,
  `compile-error`, `crashed` e `blocked`, cada um com cor e ícone próprios.
- **Grafo:** SVG simples com camadas pela ordem topológica, calculadas no
  cliente a partir das arestas enviadas pelo servidor. Sem biblioteca de
  layout; se o grafo ficar ilegível, `dagre` é o próximo passo.
- **Células HTML:** `<iframe sandbox srcdoc=...>` **sem** `allow-same-origin` e
  sem `allow-scripts`. Scripts ficam habilitados só se for pedido depois, e
  mesmo assim sem `allow-same-origin`.
- **Protocolo WebSocket (JSON):**
  - cliente → servidor: `edit {cid, code}`, `add {after, kind}`,
    `delete {cid}`, `run {cid}`;
  - servidor → cliente: `snapshot` na conexão (células, estados, previews e
    arestas), depois `cell {…}` e `graph {edges}` incrementais;
  - o servidor grava o arquivo com debounce de 1 s depois de cada mudança.

## D-016 — Segurança local mínima (proposta — precisa de decisão sua)

**Contexto:** o brief põe autenticação fora de escopo, mas um servidor que
executa código recebido por WebSocket em `localhost` sem nenhuma proteção
permite que **qualquer página aberta no navegador** se conecte e execute código
(cross-site WebSocket hijacking). O navegador não aplica CORS a WebSocket.

**Proposta** (não é autenticação de usuário; é o mínimo que o Jupyter também
faz):
- bind só em `127.0.0.1`;
- checar o header `Origin` contra a origem do próprio frontend;
- um token aleatório (`secrets.token_urlsafe`) gerado no boot, impresso na URL
  e exigido no handshake.

**Implementação (etapa 4) — aceita na revisão, com a validação de Host:**
- `kernel/web.py`: bind só em `127.0.0.1`, com o endereço efetivo exposto e
  testado.
- **Middleware de `Host`** em todas as rotas HTTP, inclusive o handshake do
  WebSocket: só `127.0.0.1:<porta>` e `localhost:<porta>`. Protege contra DNS
  rebinding.
- **WebSocket:** `Origin` obrigatório e contido na lista (a própria origem do
  servidor, mais `--dev-origin` para o Vite em desenvolvimento). Um `Origin`
  ausente também é rejeitado, porque todo navegador o envia.
- **Token:** `secrets.token_urlsafe(32)`, comparado com
  `secrets.compare_digest` e exigido no handshake.
- Arquivos estáticos não exigem token: não executam nada, e a página precisa
  carregar para ler o token da URL.
- Testes (`tests/test_web.py`), um por rejeição:
  - bind em loopback;
  - `Host` estranho e porta errada no HTTP;
  - `Host` estranho no WebSocket;
  - `Origin` estranho, `Origin` ausente;
  - token ausente e token errado;
  - origem de desenvolvimento aceita só quando configurada.
- `python -m kernel serve arquivo.nb.md [--port] [--dev-origin]` imprime a URL
  com o token, como o Jupyter faz.
- O servidor grava o arquivo com debounce de 1 s e rename atômico. Os previews
  são saneados para JSON válido no navegador (NaN e Infinity viram string;
  testado).

## Dependências novas propostas

- **Escolhida: `aiohttp`** (decisão da revisão). O servidor também serve o build
  do frontend (`frontend/dist`), e o `aiohttp` cobre HTTP e WebSocket numa
  dependência só. `websockets` exigiria um segundo servidor HTTP para os
  arquivos estáticos. Instalado: aiohttp 3.14.3 (conda-forge).
- No frontend: `react`, `typescript`, `vite`, `@codemirror/*`.

## Previsões para a Fase 3 (a medir na implementação)

| Item | Previsão |
|------|----------|
| Edição Python → resultado no navegador (localhost, sem contar o debounce) | < 30 ms + execução |
| Edição Mojo → resultado | ~1,65 s (o compilador domina; o protocolo soma < 30 ms) |
| Crash → notebook recuperado (10 células Python triviais, sem recompilar Mojo) | 0,5–1,5 s (dominado pelo boot do kernel com `numpy` + reexecução) |
| Cancelar uma compilação por nova edição | < 50 ms entre o SIGKILL e o novo build começar |

## D-017 — Estado `modified` e dependentes de código desatualizado (2026-09-24, revisão item 6)

- `modified` = o código atual difere do **último código executado**
  (`ran_code`, gravado no `Exec`). É um estado explícito, e substitui o
  `edited` da etapa 1.
- Editar de volta para o código executado restaura o status anterior (`ok` ou
  `error`, com a mensagem). Uma célula que nunca executou fica `idle`.
- Uma célula `modified` não bloqueia os filhos: seus valores antigos continuam no
  executor. Os descendentes **mantêm status e valores**, e o protocolo expõe, por
  célula, `upstream_modified: [ids]`, os ancestrais (transitivos) que estão
  `modified`. Toda entrada ou saída de `modified` inclui os descendentes no
  broadcast, para a flag chegar ao frontend.
- Testes: 7 na máquina de estados e 1 pelo WebSocket.

## D-018 — Builds especulativos com prioridade reduzida e preemptíveis (2026-09-25, revisão)

- O debounce especulativo é de **300 ms**, configurável
  (`serve --speculate-debounce`).
- **Build especulativo** roda sob `nice -n 10` (a árvore de processos do
  `mojo build` herda). **Build explícito** (Shift+Enter) roda na prioridade do
  servidor.
- **Slots:** `max_builds = max(1, nproc // 4)`. Um build explícito que encontra
  todos os slots ocupados por builds especulativos **mata um deles** e fica com a
  vaga. Um especulativo nunca preempta outro e, na fila, cede a vez a qualquer
  explícito que esteja esperando.
- **Promoção:** um run que encontra um build especulativo do mesmo código em
  andamento emite `Promote`, e o build deixa de ser preemptível.
  - Se a preempção vencer a corrida contra a promoção, `build_dropped()` refaz o
    build na hora, como explícito. Sem isso, a célula ficaria esperando um build
    que ninguém mais faz.
  - O build especulativo preemptado é reagendado com o mesmo debounce, e o
    binário acaba pronto.
- **Limite:** um build promovido continua com `nice 10`, porque baixar o nice
  exige `CAP_SYS_NICE`. Sob contenção, ele roda um pouco mais devagar.
- **Diagnósticos:** os de compilação só são substituídos quando um build do
  código atual termina. Os de interface seguem cada edição. No protocolo, cada
  diagnóstico leva um `source`.
- **Nota de ambiente:** nesta máquina, o `ananicy-cpp` põe a sessão em nice −4.
  Os testes afirmam prioridades **relativas** (explícito = servidor,
  especulativo > servidor), não valores absolutos.
- Testes com 1 slot:
  - o especulativo de A não atrasa o Shift+Enter de B além do build de B, e A é
    reconstruído depois;
  - prioridade de explícito e de especulativo lida em `/proc`;
  - um build promovido não é preemptado;
  - um especulativo não preempta outro especulativo.

## D-019 — Linguagem visual dos estados (proposta, aguardando revisão)

**Princípio:** nenhum estado é comunicado só por cor. Cada célula tem:
- uma **faixa lateral** de 4 px, na cor do estado e, para alguns estados, com
  um padrão (tracejada ou hachurada);
- um **chip de estado** no cabeçalho, sempre com **ícone + texto**.

Flags que não são estado (`upstream_modified`, diagnósticos, build em segundo
plano) aparecem como **chips secundários** ao lado do chip principal. Ícones do
`lucide-react` (MIT, tree-shaken), a única dependência de UI além do CodeMirror.

```
┃ [3] mojo   ✎ modificada — Shift+Enter executa    ⚠ 2 problemas   ⚙ build em 2º plano
┃ def run(xs: ArrayIn[DType.float64], mut t: Float64) raises:
┃     t += xs[i]            ← sublinhado ondulado vermelho (diagnóstico)
┃ ─ saída ─────────────────────────────────────────────────────────────
┃ t = 12.0
```

### Estados principais (um por célula)

| Estado | Significado | Cor (claro / escuro) | Faixa | Ícone | Texto do chip |
|---|---|---|---|---|---|
| `idle` | nunca executou | cinza `#6e7781` / `#8b949e` | contorno vazado | `Circle` | "não executada" |
| `queued` | no plano, entradas prontas, espera só o kernel | ardósia `#57606a` / `#8b949e` | sólida | `ListOrdered` | "na fila · 2º" |
| `stale` | no plano, esperando ancestral; valor desatualizado | ardósia | **hachurada** | `Hourglass` | "aguardando [2]" |
| `compiling` | build explícito de Mojo em andamento | violeta `#8250df` / `#a371f7` | sólida | `Hammer` | "compilando… 1,2 s" (cronômetro) |
| `running` | executando no kernel | azul `#0969da` / `#58a6ff` | sólida, pulso lento | `LoaderCircle` (gira) | "executando 3,4 s" |
| `ok` | executou sem erro | verde `#1a7f37` / `#3fb950` | sólida | `CircleCheck` | "ok" |
| `modified` | código ≠ último executado | âmbar `#9a6700` / `#d29922` | **tracejada** | `PencilLine` | "modificada — Shift+Enter executa" |
| `error` / `syntax-error` / `compile-error` | falhou | vermelho `#cf222e` / `#f85149` | sólida | `CircleX` / `Braces` / `Hammer` riscado | "erro" / "erro de sintaxe" / "erro de compilação" |
| `multiple-definition` / `cycle` | erro do grafo | vermelho | sólida | `Copy` / `RefreshCw` | "`x` definido em [2] e [5]" / "ciclo com [4]" |
| `blocked` | um ancestral não está ok | laranja `#bc4c00` / `#f0883e` | sólida | `Ban` | "bloqueada por [2]" |
| `crashed` | o kernel morreu nela; em quarentena | magenta `#bf3989` / `#db61a2` (distinto do vermelho) | sólida, grossa (6 px) | `Zap` | "derrubou o kernel · quarentena" |
| `interrupted` | parada pelo botão | marrom `#7d4e00` / `#bb8009` | sólida | `Square` | "interrompida por você" |

**Todo `[n]` é um link** que rola até a célula `n` e a destaca por 1 s. Isso
vale para `stale`, `blocked`, `multiple-definition`, `cycle` e a lista de
células `unsafe_*` suspeitas no painel de `crashed`.

**Painéis abaixo do código:**
- **Erros** (`error` e similares): traceback ou mensagem do compilador.
- **`crashed`:** a mensagem do D-013, com a lista de células a montante que
  usam `unsafe_*`, cada uma como link.
- **`interrupted`:** uma linha explicando que o kernel foi reiniciado.

### Flags (somam-se ao estado principal)

| Flag | Visual |
|---|---|
| `upstream_modified: [2]` | Chip âmbar **contornado** (não preenchido, para não parecer o próprio `modified`): ícone `GitBranch` + "entrada de código modificado em [2]". A área de saída ganha uma borda superior âmbar tracejada. **Os valores continuam visíveis e sem opacidade reduzida:** estão certos para o código que rodou. |
| `compiling: true` sem estado `compiling` (build especulativo) | Chip discreto no rodapé do editor: `Cog` girando devagar + "build em 2º plano", em cinza. Não muda a faixa nem o chip principal: o estado da célula não mudou. |
| `diagnostics` | No editor: sublinhado ondulado vermelho no intervalo (linha e coluna; sem fim conhecido, vai até o fim do token) e um marcador na calha com `CircleAlert`. No cabeçalho: chip vermelho "⚠ 2 problemas". Diagnósticos de interface e de compilação com o mesmo visual; o `source` aparece no tooltip. |
| `stale` (valor visível e desatualizado) | Além da faixa hachurada, a saída fica com opacidade de 60%. É o único caso de saída atenuada: o valor vai ser substituído. |

### Regras de diagnóstico no editor (pedido da revisão)

- **Linha do cursor durante a digitação:** um diagnóstico na linha em que o
  cursor está **não é sublinhado** enquanto o usuário digita. Ele aparece
  quando o cursor sai da linha ou depois de **800 ms sem teclas**, o que chegar
  primeiro. O marcador na calha e o contador do cabeçalho aparecem sempre, para
  que o problema não fique escondido.
- **Posições acompanham a edição:** os diagnósticos vêm do último build
  concluído e só são substituídos quando o próximo termina (D-018). Para que o
  sublinhado continue sobre o texto certo enquanto se edita, as decorações são
  mapeadas pelas mudanças do documento (o `Decoration.map` do CodeMirror 6).

### Nível do notebook

- **Barra do topo:**
  - estado do kernel: `ok`, `reiniciando…` (com `LoaderCircle`) ou
    `morto` (`Skull`, vermelho);
  - número de reinícios;
  - **botão Parar** (`Square`), habilitado só com uma célula em `running`.
- **Grafo:** os nós usam a mesma cor, o mesmo ícone e o mesmo texto curto dos
  chips. Um nó `modified` tem borda tracejada; um nó com `upstream_modified`
  tem um ponto âmbar. Clicar num nó rola até a célula.

### Acessibilidade

- Texto dos chips com contraste de pelo menos 4,5:1 nos dois temas; as cores
  acima são da paleta Primer, validada para isso.
- Uma região `aria-live="polite"` anuncia só transições para estados terminais
  depois de uma execução pedida pelo usuário (`ok`, erros, `crashed`,
  `interrupted`), para não tagarelar a cada mudança.
- `prefers-reduced-motion` desliga o pulso da faixa e os giros; os ícones ficam
  estáticos e os cronômetros continuam.
- Todos os links `[n]` e botões são alcançáveis por teclado.

### Mudança de backend necessária

O backend hoje usa `stale` para qualquer célula no plano. Proposta: em
`_dispatch`, uma célula do plano cujos ancestrais já estão resolvidos, e que só
espera o kernel, passa a `queued`; as que esperam ancestral continuam `stale`.
O protocolo ganha a posição na fila, para o texto "na fila · 2º". A mudança é
pequena e vai junto com o frontend, com testes na máquina de estados.

### D-019 — ajustes da revisão (2026-09-25)

**1. Glossário: três noções de "desatualizado".** Os chips usam estes textos, e
eles precisam deixar a diferença óbvia:

| Noção | Frase | Texto do chip |
|---|---|---|
| `stale` | A célula **vai reexecutar**, porque um ancestral dela vai rodar; o valor mostrado é o anterior e vai ser substituído. | "vai reexecutar · aguardando [2]" |
| `upstream_modified` | O **código** de um ancestral mudou e **ainda não foi executado**; o valor mostrado está certo para o código que rodou, e nada vai reexecutar sozinho. | "código de [2] mudou sem executar" |
| Valor antigo durante compilação (D-014) | A própria célula está **compilando uma nova versão**; o valor mostrado é o da versão anterior até o build terminar e a célula rodar. | "compilando nova versão · valor anterior" |

**2. Silêncio por padrão.** `ok` e `idle` **não exibem chip nem trilho**. Chips
só aparecem para estados que pedem atenção (`modified`, `upstream_modified`,
erros, `blocked`, `crashed`, `interrupted`, diagnósticos) ou que indicam
atividade (`queued`, `stale`, `compiling`, `running`, build em 2º plano). Num
notebook saudável, a tela não tem nenhum chip. `idle` e `ok` se distinguem só
pela presença de saída.

**3. Contraste** (`frontend/contrast_check.py`, fundo `#ffffff`/`#0d1117`, chip
`#f6f8fa`/`#161b22`):
- todas as cores de trilho ficam entre **4,9:1 e 7,5:1**, bem acima dos 3:1
  exigidos para componente de UI (WCAG 1.4.11);
- o texto dos chips sobre o fundo do chip fica entre **4,6:1 e 6,9:1**, acima
  de 4,5:1;
- trilhos tracejados e listrados usam a cor cheia em cada marca, então cada
  marca passa sozinha;
- o cinza `#6e7781` dava 4,27:1 como texto de chip no tema claro; o cinza
  "discreto" passou a ser `#57606a` (6,0:1).

Os padrões foram ajustados para a largura de 4 px:
- `stale`: **listras curtas** (3 px cor / 3 px vazio); hachura diagonal quase
  não aparece nessa largura;
- `modified`: **traços longos** (8 / 5).
- Ícones: `lucide-react` com import individual por ícone. O sublinhado na linha
  do cursor aparece após 800 ms sem teclas, independente do debounce.

### D-019 — implementação: onde divergiu (2026-09-25)

Checado no navegador contra `examples/demo.nb.md`: os 10 estados, o crash, a
interrupção, os diagnósticos em 2º plano e a regra da linha do cursor.

**Divergências da proposta:**
1. **Marcador de diagnóstico na calha:** `●` com `aria-label` e `title`, e não o
   ícone `CircleAlert`. Os marcadores de calha do CodeMirror são DOM puro, fora
   do React.
2. **Grafo:** o nó mostra `[id] tipo` + o ícone do estado. O texto curto do
   estado fica no tooltip (`<title>`), não no nó, que em 132 px não comporta
   "vai reexecutar · aguardando [3]".
3. **Texto de `multiple-definition`:** "`x` também definido em [13]", do ponto
   de vista de cada célula, em vez de "`x` definido em [12] e [13]".
4. **Sem estado "reiniciando…" no topo:** o servidor não expõe esse momento (um
   reinício leva ~100 ms). A barra mostra o número de reinícios, e a célula
   culpada mostra `crashed` ou `interrupted`.
5. **Botão "rodar tudo"** no topo: não estava na D-019. É útil para ver
   `queued` e `stale`.
6. **Markdown** renderizado com `marked` + `DOMPurify`: duas dependências a mais,
   não previstas.
7. **HTML** num iframe de altura fixa (160 px), redimensionável. Com
   `sandbox=""` (sem `allow-same-origin`), a página não consegue medir o
   conteúdo para ajustar a altura.
8. **`aria-live`** anuncia só o resultado de execuções pedidas por este cliente
   (Shift+Enter ou o botão ▶), e não as que vêm de "rodar tudo" ou da
   recuperação de um crash.
9. **Editor com foco nunca é sobrescrito** pelo código que vem do servidor
   (suposição de um único editor por célula). Sem foco, o texto é sincronizado.
10. **Previews de módulos** (`import numpy as np`) não aparecem: eram só ruído.
    Mudança no backend, com teste pelo caminho real.

**Bugs achados na checagem no navegador (corrigidos, com teste quando cabia):**
- A coluna de células era espremida pelo painel do grafo: faltava largura
  máxima no painel e largura mínima na coluna.
- Células em ciclo iam para a última camada do grafo, por causa do cálculo de
  camadas por passes. Trocado por Kahn; teste em vitest.
- O cronômetro mostrava "-83,7 s" no primeiro quadro: o tempo era calculado com
  um `now` guardado na última atividade. Agora é calculado no render.
- **Previews de valores que não existem mais:** a célula em quarentena e os
  leitores bloqueados continuavam mostrando valores de antes do crash. Toda
  célula resolvida sem rodar perde previews e saída; teste na máquina de estados.
- **Ordem das mensagens ao cliente:** o servidor mandava cada atualização numa
  task própria. Frames grandes são comprimidos num executor e os pequenos não, o
  que podia inverter a ordem (um `running` chegando depois do `ok`). Agora há
  uma fila e uma task escritora por cliente.
- **Mensagens enviadas desconectado eram descartadas em silêncio** (um
  Shift+Enter durante uma reconexão sumia). Agora ficam numa fila e são enviadas
  na reconexão; das edições, só a última por célula.

**Observado e não reproduzido:** uma vez, um clique em "parar" (e, antes, uma
edição digitada) saiu do navegador com o socket `OPEN` e não teve efeito no
servidor. A mesma sequência, repetida no navegador, numa `Session` e num
`NotebookServer` com cliente WebSocket, funcionou. `serve --verbose` agora
registra cada mensagem recebida, para pegar o caso se voltar.
