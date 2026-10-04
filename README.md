# Notebook reativo Python + Mojo

Notebook local em que a ordem de execução vem do grafo de dependências entre as
células. Mude um valor e só o que depende dele roda de novo. Células Python e Mojo
trocam dados (arrays NumPy sem cópia); as células Mojo são compiladas em segundo
plano enquanto você digita.

## Começar

Pré-requisitos: Linux x86-64, [pixi](https://pixi.sh) e um compilador C (`gcc`). O
pixi instala Python, Mojo, NumPy, matplotlib e Node.

```bash
git clone https://github.com/IgorKammerGrahl/projetoNotebook.git
cd projetoNotebook
pixi run nb ~/notebooks/analise.nb.md
```

Na primeira vez, o comando instala as dependências e monta a interface; depois, só
quando ela muda. O navegador abre sozinho, e o arquivo é criado se não existir.
Caminhos relativos partem da pasta onde o comando foi digitado. **Ctrl+C** encerra.
Para continuar depois, rode o mesmo comando.

Para ver uma análise completa, abra uma cópia do notebook de referência: difusão de
calor 2-D em Mojo, conferida com NumPy e desenhada com matplotlib. O servidor grava
no arquivo a cada edição, por isso use uma cópia.

```bash
cp examples/heat.nb.md ~/notebooks/heat.nb.md
pixi run nb ~/notebooks/heat.nb.md
```

`examples/demo.nb.md` mostra cada estado de célula (erro, bloqueio, crash,
interrupção…), com um roteiro na primeira célula.

## Usar

- **Shift+Enter** ou **▶** executa a célula e propaga a mudança às dependentes.
  Apenas editar não executa.
- **Células Mojo:** a assinatura de `run` define entradas e saídas, inclusive arrays
  1-D e 2-D; `Scratch` dá buffers de trabalho locais. Guia em
  [`docs/MOJO_CELLS.md`](docs/MOJO_CELLS.md).
- **Saídas:** o que a célula imprime, um resumo de cada nome que ela define (arrays
  com mínimo, máximo e média) e as figuras do matplotlib que ela deixou abertas.
  O tempo da última execução aparece ao lado do número da célula.
- **Parar** mata o kernel; a célula em execução fica interrompida e o resto é
  reexecutado. **Reiniciar kernel** libera a memória, inclusive as versões antigas
  das células Mojo, e reexecuta o que já tinha rodado.
- **Sem navegador:** `pixi run python -m kernel run arquivo.nb.md` roda tudo uma vez
  e imprime o estado de cada célula.

`pixi run nb` repassa opções para `python -m kernel serve`:
- `--port` (padrão 8765). Para abrir um segundo notebook ao mesmo tempo, use outra
  porta (`pixi run nb outro.nb.md --port 8766`) e mantenha sempre a mesma porta para
  o mesmo notebook, porque os rascunhos do navegador ficam guardados por porta;
- `--cache-limit MB`: tamanho do cache de builds Mojo (`.nbcache/`, padrão 512);
  os builds menos usados saem primeiro;
- `--verbose`: registra conexões e mensagens recebidas;
- `--core-dumps`: para depurar um SIGSEGV;
- `--speculate-debounce`;
- `--dev-origin`.

O servidor só aceita conexões locais, e a URL leva um token obrigatório (D-016).

## Salvamento e recuperação

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

## Compatibilidade

A partir da 1.0.0, o projeto segue [versionamento semântico](https://semver.org/lang/pt-BR/)
(D-027). Mudanças incompatíveis no que está abaixo só acontecem numa 2.0:

- **Formato do arquivo:** `.nb.md` versão 2 (`<!-- notebook-format: 2 -->`, com
  identificadores de célula). Toda versão 1.x lê e grava a versão 2, e continua
  lendo arquivos sem cabeçalho e da versão 1. Detalhes em
  [D-007](docs/DECISIONS.md#d-007--formato-do-arquivo-do-notebook-markdown-com-blocos-cercados-2026-09-23).
- **Células Mojo:** a gramática de `run`, os tipos aceitos e a API de `ArrayIn`,
  `ArrayOut` e `Scratch` descritos em [`docs/MOJO_CELLS.md`](docs/MOJO_CELLS.md).
  Novos tipos podem entrar numa 1.x; os existentes não mudam.
- **Linha de comando:** `pixi run nb`, `python -m kernel serve` e `run`, com as
  opções listadas acima.

Não são interface pública: o protocolo WebSocket entre o servidor e a interface,
o conteúdo de `.nbcache/` e os módulos Python internos.

## Desenvolvimento

Interface com recarga automática (Vite):

```bash
pixi run python -m kernel serve /tmp/demo.nb.md --dev-origin http://localhost:5173
pixi run npm --prefix frontend run dev
# abra http://localhost:5173/?token=<o token impresso pelo servidor>
```

Testes, os mesmos que o CI roda em cada PR:

```bash
pixi run test                 # backend (pytest)
pixi run frontend-test        # lógica do frontend (vitest)
pixi run frontend-e2e-install # instala o Chromium do Playwright (primeiro uso)
pixi run frontend-e2e         # build + testes no navegador, sem janela
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
  recusa de recarregamento enquanto outra aba possui edições pendentes;
- figuras do matplotlib, preview 2-D com estatísticas e tempo de execução;
- reinício do kernel pelo botão, com reexecução em outro processo.

Em caso de falha, capturam imagem, trace e logs em `frontend/test-results/`.

Documentação do projeto: decisões em [`docs/DECISIONS.md`](docs/DECISIONS.md),
medições em `docs/PHASE*.md`, dívidas técnicas em [`docs/DEBTS.md`](docs/DEBTS.md)
e mudanças por versão em [`CHANGELOG.md`](CHANGELOG.md).

## Licença

[MIT](LICENSE).
