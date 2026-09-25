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
```

**Sem navegador:** `pixi run python -m kernel run arquivo.nb.md` roda tudo uma vez
e imprime o estado de cada célula.
