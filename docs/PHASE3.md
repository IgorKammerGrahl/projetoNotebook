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
