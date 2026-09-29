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
servidor. O navegador também mantém cópias das edições locais até a confirmação
de gravação no arquivo. **“Rascunho protegido neste navegador”** confirma essa
cópia local; enquanto aparecer “protegendo rascunho…”, a gravação local está
em andamento. **“Salvo”** continua se referindo ao arquivo do notebook.

Após fechar ou perder o navegador/servidor, inicie o servidor para **o mesmo
arquivo e porta** e abra a nova URL impressa, no mesmo perfil do navegador.
O painel **“Rascunhos disponíveis neste navegador”** permite comparar e
recuperar o texto, sem executar a célula. Se o arquivo mudou, escolha qual
versão manter; se a célula desapareceu, recupere em uma nova célula.

Essas cópias pertencem ao perfil e à origem usados (`127.0.0.1` e `localhost`
são origens diferentes). Limpar os dados do site remove os rascunhos. Se o
armazenamento local falhar ou ficar cheio, a interface avisa: mantenha a aba
aberta até salvar no arquivo ou copie o código.

**Conflitos no arquivo:** apenas um servidor pode abrir o mesmo notebook para
escrita. O bloqueio é liberado quando o processo termina, inclusive após uma
queda; o arquivo oculto `.nome-do-notebook.lock` permanece e não deve ser apagado
manualmente enquanto o servidor está aberto.

Se outro programa alterar ou remover o notebook, a próxima tentativa de salvar
mostra **“conflito no arquivo”** e suspende o autosave. Use **“preservar cópia da
sessão”** para gravar o conteúdo aceito pelo servidor em outro arquivo, cujo
caminho aparece na interface. Depois, **“carregar versão externa”** reinicia o
kernel com o conteúdo do arquivo, sem executar as células. Todas as abas abertas
precisam estar sem edições pendentes; qualquer nova edição exige atualizar a
cópia. Se ocorrer uma corrida durante a substituição, o aviso também informa
onde a versão deslocada foi preservada. Confira os arquivos antes de removê-los.

A proteção de arquivo é destinada a Linux com sistema de arquivos local e
suporte a `renameat2`; não há fallback para uma substituição sem verificação.
Ela detecta alterações externas, mas não coordena editores que ignoram o
bloqueio: uma escrita arbitrariamente tardia por um descritor do arquivo antigo,
após a última verificação, ainda pode escapar. Para edição externa simultânea
sem esse risco, encerre o servidor antes de usar outro editor. Hard links não
são aceitos; links simbólicos de entrada são resolvidos para seu destino.

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
  e anúncio único do incidente na região acessível;
- recuperação de rascunhos após encerrar navegador e servidor, incluindo
  interrupção forçada dos processos, conflitos após edição externa e células
  removidas, sem repetir execuções pendentes;
- conflito de arquivo externo, cópia da sessão, recarregamento sem execução e
  recusa de recarregamento enquanto outra aba possui edições pendentes.

Em caso de falha, capturam imagem, trace e logs em `frontend/test-results/`.

Ao salvar, o servidor escreve `<!-- notebook-format: 2 -->` e identificadores
de célula, preservando conteúdo, ordem e identidade entre reinícios. Markdown
consecutivo, vazio e com exemplos de código continua preservado. Arquivos sem
cabeçalho e com a versão 1 continuam sendo aceitos; arquivos da versão 2 exigem
este leitor atualizado.
Detalhes em [D-007](docs/DECISIONS.md#d-007--formato-do-arquivo-do-notebook-markdown-com-blocos-cercados-2026-09-23).

**Sem navegador:** `pixi run python -m kernel run arquivo.nb.md` roda tudo uma vez
e imprime o estado de cada célula.
