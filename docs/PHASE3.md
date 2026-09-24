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
