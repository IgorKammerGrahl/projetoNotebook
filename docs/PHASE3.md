# Fase 3 — backend (servidor, kernel, recuperação, compilação assíncrona)

Previsões registradas em `docs/DECISIONS.md` ("Previsões para a Fase 3") antes da
implementação. Medições: `PYTHONPATH=. pixi run python tests/bench_phase3.py`.
Todo fonte compilado leva um nonce.

## Previsão vs. medida

| Item | Previsão | Medido | Diferença |
|------|----------|--------|-----------|
| Execução Python → resultado no navegador (WebSocket, localhost) | < 30 ms + execução | **0,17 ms** mediana, p95 0,24–0,29 ms | ~100× melhor |
| Idem, só a `Session` (sem WebSocket) | — | 0,08–0,12 ms mediana (50 execuções, 50 eventos `ran` conferidos) | — |
| Edição Mojo → resultado (compila) | ~1,65 s | 1,37–1,59 s | melhor (célula menor que a do benchmark da Fase 2) |
| Crash → notebook recuperado (12 células reexecutadas, sem recompilar) | 0,5–1,5 s | **0,48 s** com core dump → **0,16–0,20 s** sem ele | ver abaixo |
| Cancelar um build → o novo build começa | < 50 ms | 0,05 ms | melhor |

## Medidas sem previsão

| Item | Medido |
|------|--------|
| Boot do kernel + primeira execução | 98–136 ms |
| Reexecução Mojo já compilada | 0,12–0,30 ms |
| Botão parar → notebook recuperado | 132–155 ms |
| 4 células Mojo independentes: `max_builds=1` vs `4` | 5,77 s vs 1,80 s (**3,2×**) |

## Bugs encontrados nas medições (os testes passavam)

1. **Core dump atrasava a detecção do crash em ~0,5 s.** O `core_pattern` desta
   máquina é um pipe para o `systemd-coredump`, com `ulimit -c` ilimitado. Entre
   o SIGSEGV e o EOF no socket passavam 488 ms.
   - Correção: o processo kernel faz `setrlimit(RLIMIT_CORE, 0)`. Crash aqui é um
     evento esperado e recuperado, e um dump por crash só custa tempo e disco.
   - Depois: ~45 ms até detectar e 0,16–0,20 s até recuperar.
2. **Parar travava a sessão quando o kernel tinha mensagens não lidas no
   socket.** Um processo morto com dados pendentes no buffer de recepção responde
   com RST, não com FIN. O leitor recebia `ConnectionResetError` em vez de EOF,
   morria calado, e `kernel_died` nunca era chamado.
   - O teste do botão parar passava por sorte: não havia mensagem pendente.
   - O benchmark reproduziu o problema (um `Delete` enviado enquanto o kernel
     estava no laço).
   - Correção: reset = morte, igual a EOF.
   - Teste de regressão determinístico:
     `test_stop_with_unread_messages_in_the_kernel_socket`.

## O que existe (backend)

- `kernel/scheduler.py`: máquina de estados pura (D-012).
- `kernel/executor.py`: namespace e execução.
- `kernel/kernelproc.py`: o processo kernel.
- `kernel/session.py`: o driver assíncrono. Cuida da recuperação (D-013), do
  botão parar e da compilação assíncrona, cancelável e paralela (D-014).
- `kernel/web.py`: HTTP e WebSocket, com segurança local (D-016) e gravação do
  arquivo com debounce e rename atômico.
- `python -m kernel serve arquivo.nb.md` imprime a URL com o token.
- **Testes (139):**
  - `test_scheduler.py`: 18, puros;
  - `test_session.py`: 7, com processo real;
  - `test_async_compile.py`: 4;
  - `test_web.py`: 9, um por rejeição de segurança, mais o protocolo;
  - os 100 anteriores, pelo driver síncrono.

## Compilação especulativa (revisão, item 7)

### Previsão (escrita ANTES de implementar)

Premissas medidas: um `mojo build` de célula pequena leva ~1,4 s, e o debounce é
de 800 ms. O build especulativo começa em t = 0,8 s depois da última tecla e fica
pronto em t ≈ 2,2 s.

| Espera entre parar de digitar e o Shift+Enter | Latência prevista até o resultado |
|---|---|
| 0 s (Shift+Enter imediato; o run compila na hora) | ~1,4 s (igual a sem especulação) |
| **~1 s** | **~1,2 s**: o run pega o build em andamento e espera o resto; ganho de só ~0,2 s |
| 2 s | ~0,2 s |
| 3 s | ~ms (só a execução) |

A especulação só dá "resultado instantâneo" se a pausa passar de ~2,2 s. Com
~1 s de pausa, o ganho previsto é pequeno, porque o debounce de 800 ms consome
quase toda a espera.

### Medido (`tests/bench_speculative.py`, debounce real de 800 ms, 3 rodadas, nonce por fonte)

| Pausa | Previsto | Medido (mediana) |
|---|---|---|
| 0 s | ~1,4 s | 1,354 s |
| **1 s** | **~1,2 s** | **1,171 s** |
| 2 s | ~0,2 s | 0,162 s |
| 3 s | ~ms | 0,003 s |

A previsão acertou. Com a pausa de ~1 s que a revisão pediu, o ganho é de só
~0,18 s, porque o debounce de 800 ms consome quase toda a espera. **Ponto extra,
para decisão:** com debounce de 300 ms, a mesma pausa de 1 s dá **0,695 s**. O
custo é matar e recomeçar builds com mais frequência enquanto o usuário digita
(mais CPU). O valor é `Session.speculate_debounce`.

**Implementação:**
- `Scheduler.speculate()` emite `Compile` sem mudar o status.
- `compiled()`, para uma célula fora da fila, grava só `diagnostics`
  (`[{line, col, message}]`, em coordenadas da célula e vindas de
  `_map_errors`). Nunca produz um `compile-error`.
- Um run que encontra um build especulativo do mesmo código em andamento espera
  por ele, em vez de começar outro.
- A edição limpa os diagnósticos antigos, e erros da gramática de `run` viram
  diagnóstico na hora.
- O protocolo expõe `compiling` e `diagnostics` por célula.
- Testes: 4 puros, 4 pela `Session` com `mojo` real (debounce, erro como
  diagnóstico, edição que mata o build, run que reusa o build em andamento) e 1
  pelo WebSocket.

## Custo dos builds especulativos numa sessão de digitação (revisão, item 2)

**Traço** (`tests/bench_typing.py`, semente 42, o mesmo para os dois debounces):
317 teclas em 60,6 s. São rajadas de 3–15 teclas com 80–200 ms entre elas,
separadas por pausas log-uniformes de 100 ms a 2 s. Há 22 intervalos acima de
300 ms e 11 acima de 800 ms. Toda tecla é uma edição com fonte novo e válido.
Isso é o pior caso: digitando de verdade, estados intermediários quebram a
gramática de `run`, e aí nem há build.

### Previsão (escrita ANTES de medir, pelo modelo do script)

O modelo: um build começa a cada intervalo maior que o debounce e morre se a
próxima tecla chegar antes de ele terminar (~1,4 s).

| Debounce | Builds iniciados | Mortos | Completos | Tempo de build jogado fora |
|---|---|---|---|---|
| 300 ms | 22 | 20 | 2 | ~10,4 s |
| 800 ms | 11 | 10 | 1 | ~5,0 s |

Com pausas de até 2 s, quase nenhum build especulativo sobrevive até a próxima
tecla. O debounce de 300 ms dobra o trabalho jogado fora (≈17% de um núcleo,
contra ≈8%), em troca do ganho de latência medido antes (1 s de pausa: 0,70 s
contra 1,17 s). Esse CPU roda com `nice 10`.

### Medido (servidor real, `mojo` real, mesmo traço)

| Debounce | Iniciados | Mortos | Completos | Build jogado fora | Previsto |
|---|---|---|---|---|---|
| 300 ms | 22 | **21** | 1 | **12,3 s** | 22 / 20 / 2 / 10,4 s |
| 800 ms | 11 | 10 | 1 | 5,1 s | 11 / 10 / 1 / 5,0 s |

O modelo acertou os 800 ms exatamente. Nos 300 ms, ficou um build a mais morto e
1,9 s a mais jogado fora. A explicação provável é o `nice 10`: com prioridade
menor, o build especulativo leva mais que os 1,4 s do modelo, e um intervalo que
passava por pouco dessa conta deixou de bastar.

**Balanço dos 300 ms (aprovado na revisão):**
- custa ≈20% de um núcleo em prioridade baixa durante a digitação contínua
  (12,3 s em 62 s), contra ≈8% com 800 ms;
- corta a latência do Shift+Enter depois de 1 s de pausa de 1,17 s para 0,70 s;
- esse CPU é o primeiro a ceder: a preempção (D-018) garante que ele nunca atrasa
  uma execução explícita além do build dela.

## Bug do "parar"/edição sem efeito (revisão do frontend, item 1)

### Previsão (escrita ANTES dos testes)

- **(a) O loop de leitura espera operações longas: previsão, NÃO é a causa.**
  Em `kernel/web.py`, o único `await` do loop de leitura é `_handle`, e
  `_handle` não tem nenhum `await`: só chama métodos síncronos da `Session`, que
  enfileiram ações e retornam. A execução e o build rodam no worker e em tasks.
  O teste na mesma conexão deve passar sem mudança de código.
- **(b) Fila offline com o socket aberto: previsão, NÃO é a causa.** A
  instrumentação no navegador registrou o `{"type":"stop"}` passando por
  `WebSocket.prototype.send` com `readyState` 1. Ou seja, ele foi escrito num
  socket aberto e não passou pela fila. Além disso, `send` decide pelo
  `readyState` do socket, não pela flag `connected` da UI.
- **Previsão de causa:** nenhuma das duas. A hipótese mais provável é um
  **socket zumbi**: o cliente escreve num socket que ele acha aberto, mas cujo
  handler no servidor já terminou, ou que não é o socket atual. Evidência a
  favor: `ss` mostrou 3 conexões do Chrome na porta 8765 com uma aba só. Se os
  testes de (a) e (b) passarem, o próximo passo é instrumentar a identidade da
  conexão nos dois lados.

### Retomada no Codex: previsão antes dos novos testes

As hipóteses (a) e (b) continuam improváveis no código atual. Os logs preservados
não mostram o recebimento do `stop` da falha original; não permitem atribuir uma
causa ao incidente. Três conexões TCP, por si só, também não provam socket zumbi.
A extração incompleta de `Connection` tem dois problemas verificáveis: espera
ACK que o servidor não implementou e aceita eventos tardios depois de `close()`.
Previsão: testes de envio com socket aberto passam; o teste de abertura tardia
após desmontagem falha e demonstra uma falha de ciclo de vida, sem provar que
ela causou o incidente observado anteriormente.

### Resultado do item 1 (retomada)

- **(a) refutada nos testes atuais:** loop infinito e `stop` na mesma conexão;
  edição/execução Python enquanto compila Mojo real; edição durante um build
  especulativo promovido mantido por uma barreira até a resposta Python chegar.
  O dispatcher agora é explicitamente síncrono; execução/build continuam em tasks.
- **(b) refutada:** envio depende de OPEN, funciona antes do snapshot e depois de
  reconectar, independentemente da flag visual. Teste de transição incluído.
- **Causa original não determinada.** O log antigo também mostra `stop` tratado
  normalmente na tentativa de reprodução. Não há evidência para declarar que
  ACK/resend consertou aquele incidente.
- **Defeito reproduzido e corrigido na extração incompleta:** eventos tardios de
  uma conexão descartada ainda enviavam comandos e alteravam estado. O teste
  falhou antes da correção e passou após invalidar o socket no descarte/reconexão.
- ACK agora confirma despacho imediatamente, sem esperar execução; após 3 s sem
  confirmação o cliente reconecta mesmo sem receber `close`, ignora eventos do
  socket antigo e mostra aviso. Mensagens já escritas **não são repetidas**:
  uma confirmação perdida não distingue comando executado de comando perdido.
  Apenas mensagens ainda não escritas permanecem na fila offline.
- Falha no writer fecha a conexão; JSON inválido ou exceção no despacho retorna
  erro sem matar o leitor. Logs identificam conexão e sequência sem imprimir fonte.
- Validação: **17 testes WebSocket/backend, 18 testes frontend; build aprovado**.
  O Vite mantém o aviso preexistente de bundle >500 kB.

## Revisão final do frontend — itens 2–5

Implementação concluída após o commit separado do item 1 (`e5f1753`).

- **Edições offline versionadas:** `edit` inclui `base_version` e um `request`
  que identifica aquela edição. O servidor compara a base antes de alterar a
  célula e retorna `conflict` com o estado atual se ela estiver ausente ou
  obsoleta. A versão inclui instância do servidor, célula e revisão; voltar ao
  mesmo texto ou reiniciar o servidor não torna uma versão antiga válida.
- **Digitação e reconexão:** cada célula mantém no máximo uma edição aguardando
  aceitação. Teclas posteriores ficam no rascunho e só usam a versão resultante
  da própria edição aceita. Uma mudança de outro cliente nunca serve de base
  automaticamente. Edições sem ACK podem ser reenviadas com a base e o pedido
  originais; se a mesma edição ainda for a atual, o servidor confirma sem
  incrementar a revisão. Caso contrário, a comparação de versão continua
  valendo. Isso amplia apenas para edições a política do item 1: execuções já
  enviadas continuam sem repetição automática.
- **Execuções antigas:** `run`/`run_all` aguardando envio há mais de 5 s são
  descartados com aviso. A mesma proteção vale para `stop` atrasado. Shift+Enter
  aguarda a aceitação do último rascunho e conserva o horário original do pedido;
  uma espera de mais de 5 s também descarta essa execução. “Rodar tudo” fica
  indisponível enquanto houver edições pendentes ou conflitos, com explicação
  no título do botão.
- **Conflito na célula (desvio 9):** mudança externa com editor em foco ou com
  rascunho pendente mantém o texto local e mostra “manter a minha versão” /
  “carregar a do servidor”. A primeira ação tenta gravar contra a versão mais
  recente conhecida (uma nova corrida ainda pode ser recusada); a segunda
  carrega o texto sem ecoá-lo como edição. Perder o foco não apaga o conflito.
- **Resumo acessível (desvio 8):** cada crash/interrupção recebe um identificador
  de incidente, preservado nos updates e snapshots. Uma região `aria-live`
  atômica anuncia um resumo por identificador, inclusive em “rodar tudo” e
  recuperação. Updates repetidos, resultados das células recuperadas e
  reconexão não repetem nem sobrescrevem o resumo. Falha definitiva de reinício
  não anuncia recuperação bem-sucedida.
- **Dívidas registradas:** DEBT-013, altura automática do iframe via
  `postMessage` com teto e preservação do isolamento; DEBT-014, indicador
  explícito de reinício do kernel.

### Validação final

- **179 testes backend aprovados:** 78 de WebSocket, Session, compilação
  assíncrona e scheduler; 101 dos demais módulos, incluindo CLI, Mojo real,
  análise, formato, engine e dívidas. A tentativa de CLI dentro do sandbox
  falhou/travou e foi interrompida; todos os 101 passaram fora dele.
- **33 testes frontend aprovados:** conexão e eventos tardios, limite de 5 s,
  preservação da base no reenvio, digitação durante ACK pendente, conflito com
  foco/rascunho offline, ambas as escolhas, prioridade e deduplicação do anúncio.
- **Build TypeScript + Vite aprovado.** Permanece o aviso já existente de bundle
  maior que 500 kB; não foi adicionada dependência.
- A ferramenta de navegador não encontrou nenhuma superfície conectada, e o
  navegador interno retornou indisponível. **Não houve nova validação visual nem
  com leitor de tela real nesta sessão**; comportamento coberto por testes da
  lógica usada pelos componentes e integração WebSocket/processos reais.
- A causa do incidente original de “parar”/edição sem efeito continua não
  determinada, conforme o relatório do item 1. Os testes na mesma conexão e a
  regressão de ciclo de vida corrigida não provam retrospectivamente essa causa.

## Após a v0.1.0 — preservar Markdown ao salvar e reabrir

**DEBT-006 paga.** O formato antigo não representava limites entre células
Markdown consecutivas, descartava células vazias e podia transformar exemplos
cercados de Python/Mojo/HTML em células executáveis na reabertura.

- O writer mantém a representação antiga quando ela reproduz exatamente as
  células. Nos demais casos, usa o cabeçalho `<!-- notebook-format: 1 -->` e
  blocos Markdown explícitos, com delimitadores que não colidem com o conteúdo.
  O payload é opaco e preserva linhas em branco; não há ids persistidos.
- O leitor aceita os notebooks antigos. Blocos explícitos malformados são
  recusados antes do carregamento. A extensão exige o leitor posterior à v0.1.0;
  compatibilidade e gramática estão registradas em D-007.
- Regressões cobrem adjacência, vazios no início/meio/fim, espaços nas bordas,
  CRLF, exemplos de código, cercas abertas e comentários semelhantes aos
  delimitadores. Antes da correção, a seleção de novos testes Markdown teve
  **14 falhas e 1 aprovação**.
- A integração WebSocket verifica o arquivo gravado **antes** de encerrar o
  servidor, pois `close()` também salva. Outra instância lê o arquivo e devolve
  as mesmas células, sem executar os exemplos Markdown.
- O novo teste Playwright usa Chromium e a interface real: cria quatro células
  Markdown consecutivas (duas vazias), edita uma existente, verifica o autosave
  no disco e inicia **outro processo** do servidor. Compara tipo, fonte e ordem,
  incluindo Python/Mojo/HTML e linhas em branco nas bordas. Também confirma o
  HTML visível no iframe. O arquivo fica em diretório temporário, removido ao
  final; falhas deixam trace, imagem e logs em `frontend/test-results/`.

**Validação:** 195 testes backend, 33 testes frontend e 1 teste Chromium
aprovados; build TypeScript + Vite aprovado. Permanece o aviso preexistente de
bundle maior que 500 kB. A captura da reabertura foi inspecionada. Comandos de
instalação do navegador e execução estão no README (`frontend-e2e-install` e
`frontend-e2e`).

## Após a v0.1.0 — salvamento visível e falhas de escrita

O bloco anterior foi consolidado no commit `96d8ce2`. Este bloco implementa D-020.

**Lacunas corrigidas:** `_save` descartava o agendamento antes de escrever e
executava I/O no event loop. Uma exceção aparecia apenas no log, sem estado de
falha no frontend e sem tentativa final no encerramento após esse erro.

- A interface mostra “salvando…”, “salvo” ou “falha ao salvar”. Pendências locais
  e desconexão têm mensagens próprias; receber ACK não basta para mostrar “salvo”.
  Falhas incluem explicação e “tentar salvar novamente”, e sobrevivem à reconexão.
- Uma única task grava cópias imutáveis fora do event loop. A revisão confirmada
  corresponde à cópia efetivamente gravada; uma edição recebida durante a escrita
  continua pendente e entra na próxima cópia. Arquivo e diretório são sincronizados
  antes da confirmação, com substituição atômica e limpeza do temporário.
- Falhas de escrita preservam a fonte aceita em memória. Uma tentativa explícita
  ou uma nova edição pode salvar novamente. Durante o encerramento, ações novas
  são recusadas, a escrita em curso é aguardada e falhas definitivas são propagadas
  após a limpeza dos sockets, processo do kernel e servidor HTTP.

**Verificação:**
- 208 casos backend aprovados: suíte completa com 206, mais os dois casos
  acrescentados na revisão final (exclusão da última célula e edição durante uma
  escrita que depois falha). Treze casos de persistência atravessam o servidor
  real, incluindo falta de permissão, disco cheio, substituição e sincronização
  do diretório, reconexão, novas tentativas, encerramento e “parar” na mesma
  conexão enquanto a escrita está bloqueada por uma barreira de teste.
- 43 testes frontend aprovados, incluindo confirmação de revisão, rascunhos e
  alterações sem ACK, desconexão e manutenção do erro até a gravação bem-sucedida.
- 2 testes Chromium aprovados: salvar/reabrir e falha real de permissão no
  diretório temporário, seguida de reload e nova tentativa pela interface.
  A captura do estado de erro foi inspecionada.
- Build TypeScript + Vite aprovado; permanece o aviso preexistente de bundle
  maior que 500 kB. Sem nova dependência.

**Limite explícito:** este bloco recupera uma gravação falha enquanto a fonte
permanece em memória. Rascunhos persistidos e recuperação após encerramento
forçado do servidor/navegador ainda não estão implementados.

## Após a v0.1.0 — fluxos críticos no navegador

O salvamento visível foi consolidado no commit `26e5112`. Este bloco acrescenta
sete cenários Playwright aos dois existentes, usando Chromium e processos reais
do servidor/kernel. Não houve alteração no código do aplicativo nem dependência
nova. Os helpers compartilhados ficam em `frontend/e2e/helpers.js`.

- **Interrupção:** inicia um loop infinito por Shift+Enter, clica “parar”,
  verifica interrupção, reinício, preservação da fonte e anúncio único. Depois
  edita e executa outras células e a própria célula interrompida. A observação
  passiva do WebSocket confirma uma única conexão e `run` seguido de `stop`.
- **Build promovido:** um wrapper temporário de `mojo`, exclusivo do servidor
  de teste, segura uma compilação marcada antes de chamar o compilador real.
  O teste promove esse build especulativo pela interface, edita/executa Python
  na mesma conexão enquanto a barreira está fechada e confirma que a promoção
  conserva o processo. Ao liberar a barreira, compila e verifica o resultado
  Mojo e o arquivo salvo. Assim a duração não depende da velocidade da máquina.
- **Fila offline:** interrompe o transporte, edita e pede execução simples,
  “rodar tudo” e Shift+Enter aguardando a edição. Avança o relógio do navegador
  em 6 s e reconecta. A fonte é salva, o aviso aparece e nenhuma execução antiga
  atravessa o socket; um contador no disco confirma a ausência de efeitos.
  Uma execução nova funciona. O transporte encaminha mensagens reais, sem
  fabricar ACKs, snapshots ou respostas do aplicativo.
- **Conflitos:** duas abas modificam a mesma célula com o primeiro editor em
  foco. Cada escolha (“manter a minha versão” / “carregar a do servidor”) tem
  seu próprio teste, com comparação dos dois editores e do arquivo salvo.
  Outro caso reconecta uma edição com base obsoleta, recebe o conflito do
  servidor e confirma que resolver o conflito não repete o Shift+Enter pendente.
- **Crash:** uma célula Mojo provoca SIGSEGV real durante “rodar tudo”. O teste
  verifica quarentena, bloqueio de descendentes sem preview antigo, recuperação
  das células sobreviventes e preservação da fonte na tela e no disco.
  Recuperação e reconexão mantêm um único anúncio; corrigir a célula permite
  executá-la e atualizar seus descendentes sem outro reinício.

**Validação:** `pixi run frontend-e2e` aprovou os **9 testes Chromium**, sem
retries, em 30,3 s; build TypeScript + Vite aprovado. Permanece o aviso
preexistente de bundle maior que 500 kB. A captura da recuperação do crash foi
inspecionada. As duas falhas na primeira execução eram seletores de teste que
não aceitavam o espaço entre o ícone e o texto do status; foram corrigidos.
Backend e testes unitários do frontend não foram alterados nem reexecutados
neste bloco.

**Limites:** os anúncios são observados na região `aria-live` do DOM, sem leitor
de tela real. Esta validação protege os fluxos atuais; a causa do incidente
original de “parar”/edição sem efeito continua não determinada.

## Após a v0.1.0 — recuperação de rascunhos (D-021)

Os fluxos críticos foram consolidados em `828371d`. Este bloco permite fechar
ou perder navegador/servidor e recuperar código que ainda não chegou ao arquivo.

- O servidor identifica o notebook pelo caminho resolvido e fornece UIDs de
  célula que sobrevivem a gravação, reinício e reordenação. O formato 2 guarda
  esses UIDs; os formatos anteriores continuam legíveis. O arquivo legado não
  é regravado só por abrir, e uma alteração externa antes da migração produz
  rascunhos órfãos em vez de associá-los por posição a outra célula.
- O editor protege cada versão digitada em IndexedDB antes de enviá-la. A
  confirmação local é distinta de “salvo”, que continua confirmando o arquivo.
  Quota, indisponibilidade e dados inválidos geram aviso; falhas preservam a
  cópia anterior. ACK e aceitação no servidor não apagam a recuperação.
- O painel compara rascunho e versão atual, permite ambas as escolhas de
  conflito e oferece criar uma célula quando a original não existe. Recuperação
  não executa código nem restaura comandos pendentes. A oferta original só é
  descartada quando o texto escolhido chega ao arquivo, ou por escolha explícita.
- A revisão cobriu versões imutáveis entre abas, troca de documento com leitura
  falha, digitação durante recuperação, conclusões assíncronas após desmontagem
  e perda da confirmação ao criar uma célula. Corrigiu também a reutilização de
  uma cópia em limpeza quando um novo conflito preservava o mesmo texto; esse
  caso agora cria uma nova cópia e tem teste de regressão.

**Defeito encontrado pelo teste de crash:** a primeira implementação usava
`localStorage`. Fechar o navegador normalmente preservava o rascunho, mas
SIGKILL no processo Chromium após uma falha de salvamento perdia a escrita
recente. Não foi adicionado atraso para fazer o teste passar: a persistência
foi trocada por IndexedDB, com transação `strict` e confirmação por `complete`.
O mesmo cenário de encerramento forçado passou com ambas as escolhas de conflito.

**Validação:**
- **217 testes backend aprovados**, incluindo identidade entre instâncias,
  reordenação externa, versões antigas recusadas e formato inválido rejeitado.
- **58 testes frontend aprovados**, incluindo regressões de filas, salvamento,
  conflitos, falhas de armazenamento e concorrência da recuperação.
- **14 testes Chromium aprovados**, sem retries: os nove existentes e cinco
  de recuperação. Os novos usam perfil persistente real, fechamento do processo
  do navegador, SIGKILL do servidor e do Chromium de teste, falha real de
  permissão no diretório, mudança externa de código/ordem e célula removida.
  Comparam a fonte gravada e verificam a ausência de execução por mensagens
  WebSocket e arquivo sentinela. Armazenamento local indisponível exibe aviso
  sem impedir o salvamento no arquivo. A captura de conflito foi inspecionada.
- Build TypeScript + Vite aprovado; permanece o aviso preexistente de bundle
  maior que 500 kB. Sem nova dependência.

**Limites de uso:** mesmo perfil, origem e caminho do notebook. Copiar/mover o
arquivo ou trocar host/porta não migra rascunhos. Dados do site apagados removem
as cópias. A proteção vale após a confirmação local da transação e cobre fonte,
não um histórico de inclusões/exclusões/ordenação. Edições externas com o servidor
aberto e múltiplos servidores no mesmo arquivo continuam sem coordenação.
