# Fase 1 — Motor reativo só com Python

## Previsões (escritas ANTES de implementar)

| Item | Previsão |
|------|----------|
| `analyze()` de uma célula de 50 linhas (`ast.parse` + `symtable`) | 0,1–0,3 ms |
| Overhead do motor por edição, cadeia de 200 células, sem contar o código do usuário | < 10 ms (grafo reconstruído inteiro a cada edição) |
| `parse` + `serialize` de um arquivo com 200 células | < 5 ms |

## Medido (`PYTHONPATH=. pixi run python tests/bench_phase1.py`)

| Item | Previsão | Medido | Diferença |
|------|----------|--------|-----------|
| `analyze()`, célula de 48 linhas | 0,1–0,3 ms | **1,09 ms** | 4–10× pior |
| Edição no topo de uma cadeia de 200 células (reexecuta as 200) | < 10 ms | 1,71 ms | melhor |
| Edição na folha da mesma cadeia (reexecuta 1) | — | 0,14 ms | — |
| `parse` + `serialize`, 200 células | < 5 ms | 0,29 ms | melhor |

**Por que `analyze` errou a previsão:** só o `ast.parse` custa 0,43 ms e o
`symtable` outros 0,33 ms, porque reparseia o fonte. O resto é o percurso do
nível de topo. Previ como se o parse fosse gratuito. Não otimizei: só a célula
editada é analisada, e 1 ms por edição é irrelevante perto do código do usuário
(e de 1,3 s de uma compilação Mojo). Se precisar, dá para cachear a análise por
hash do código.

## O que existe

- `kernel/fmt.py`: formato `.nb.md` (D-007), com `parse` e `serialize`.
- `kernel/engine.py`: análise de nomes (`symtable` + correções pelo `ast`,
  D-008), grafo, execução reativa, estados `ok | error | syntax-error |
  multiple-definition | cycle | blocked`, e captura de stdout/stderr por
  célula.
- `kernel/__main__.py`: CLI `pixi run python -m kernel run arquivo.nb.md`, com
  a verificação de ambiente D-006 no boot. Exit code 1 se alguma célula falhar.
- Testes (`pixi run test`, 48): formato (round-trip, cercas estranhas, crases no
  conteúdo), análise (13 casos), DAG (ordem topológica, desempate por posição,
  ciclo, descendente de ciclo, duplicata introduzida e resolvida por edição,
  remoção, invalidação, erro de runtime sem estado parcial, erro de sintaxe,
  nomes privados), CLI de ponta a ponta e dívidas.

## Fora desta fase

Células Mojo, Markdown e HTML são lidas pelo formato, mas ignoradas pelo
motor. Não há exibição do valor da última expressão da célula (só stdout),
`watch` do arquivo nem API de edição pela CLI: a reatividade é exercitada pela
API `Engine` nos testes.
