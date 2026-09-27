# Notebook reativo Python + Mojo

Notebook em que a ordem de execução vem do grafo de dependências entre células
Python e Mojo. Decisões em `docs/DECISIONS.md`, medições em `docs/PHASE*.md`,
dívidas em `docs/DEBTS.md`, guia das células Mojo em `docs/MOJO_CELLS.md`.

## Rodar localmente

Pré-requisitos: [pixi](https://pixi.sh) e um compilador C (`gcc`). O pixi instala
Python, Mojo, NumPy e Node.

```bash
pixi install
pixi run frontend-install      # dependências do frontend (npm ci)
pixi run frontend-build        # gera frontend/dist, servido pelo servidor

# o servidor grava no arquivo a cada edição: abra uma cópia da demonstração
cp examples/demo.nb.md /tmp/demo.nb.md
pixi run python -m kernel serve /tmp/demo.nb.md
```

Abra a URL impressa (`http://127.0.0.1:8765/?token=...`). O token é obrigatório
(D-016). A demonstração explica, na primeira célula, como provocar cada estado.

O topo mostra o estado de salvamento. **“Salvo”** confirma que as alterações
aceitas foram gravadas; “alterações pendentes” também considera rascunhos,
conflitos e mensagens ainda sem confirmação. Durante uma desconexão, a
interface aguarda reconectar para confirmar o estado do arquivo.

Se aparecer **“falha ao salvar”**, corrija a permissão ou libere espaço e use
**“tentar salvar novamente”**. As alterações aceitas permanecem na memória do
servidor; rascunhos locais permanecem na aba. Mantenha ambos abertos até aparecer
“salvo”: recuperação de rascunhos após encerramento forçado ainda não existe.

Opções úteis de `serve`:
- `--port`;
- `--verbose`: registra conexões e mensagens recebidas;
- `--core-dumps`: para depurar um SIGSEGV;
- `--speculate-debounce`;
- `--dev-origin`.

**Desenvolvimento do frontend** (Vite com recarga):

```bash
pixi run python -m kernel serve /tmp/demo.nb.md --dev-origin http://localhost:5173
pixi run npm --prefix frontend run dev
# abra http://localhost:5173/?token=<o token impresso pelo servidor>
```

**Testes:**

```bash
pixi run test            # backend (pytest)
pixi run frontend-test   # lógica do frontend (vitest)
pixi run frontend-e2e-install # instala o Chromium do Playwright (primeiro uso)
pixi run frontend-e2e     # build + testes no navegador, sem janela
```

Os testes no navegador usam Chromium, notebooks temporários e o servidor real.
Cobrem:

- autosave e reabertura em outro processo, preservando conteúdo, tipo e ordem
  das células, incluindo Markdown consecutivo e vazio;
- falha real de permissão de escrita, reconexão e nova tentativa de salvar;
- interrupção de loop infinito e edição durante um build Mojo promovido, na
  mesma conexão que iniciou o trabalho;
- edições offline, descarte de execuções antigas e conflitos entre duas abas,
  com ambas as escolhas de resolução;
- crash real de Mojo durante “rodar tudo”, recuperação, preservação do código
  e anúncio único do incidente na região acessível.

Em caso de falha, capturam imagem, trace e logs em `frontend/test-results/`.

O formato de gravação preserva células Markdown consecutivas, vazias e com
exemplos de código. Quando necessário, inclui o cabeçalho
`<!-- notebook-format: 1 -->` e delimitadores de célula; esses arquivos exigem o
leitor atual, posterior à v0.1.0. Notebooks antigos continuam sendo aceitos.
Detalhes em [D-007](docs/DECISIONS.md#d-007--formato-do-arquivo-do-notebook-markdown-com-blocos-cercados-2026-09-23).

**Sem navegador:** `pixi run python -m kernel run arquivo.nb.md` roda tudo uma vez
e imprime o estado de cada célula.
