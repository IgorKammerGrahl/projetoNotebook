# Histórico de mudanças

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
O projeto segue [versionamento semântico](https://semver.org/lang/pt-BR/) a partir da
1.0.0; o que é interface pública está em "Compatibilidade", no README (D-027).

## [1.0.0] — 2026-10-04

### Adicionado
- `pixi run nb arquivo.nb.md`: um comando prepara a interface (só quando ela mudou),
  abre o navegador e cria o notebook se ele não existir. Caminhos relativos partem da
  pasta onde o comando foi digitado (D-027).
- Arrays 2-D nas células Mojo (`ArrayIn[DType.<dt>, 2]`, `ArrayOut[DType.<dt>, 2]`,
  `m[i, j]`, `m.dim(d)`) e buffers locais `Scratch`, zerados e com checagem de limites
  (D-024).
- Figuras do matplotlib embaixo da célula; preview de arrays com mínimo, máximo, média
  e o canto de arrays 2-D; assinatura de funções; tempo de execução de cada célula;
  grafo de dependências de cima para baixo (D-025).
- Botão **reiniciar kernel** e indicador "reiniciando kernel…"; limite para o cache de
  builds Mojo (`--cache-limit`, padrão 512 MiB) (D-026).
- Notebook de referência `examples/heat.nb.md` (difusão de calor 2-D), teste de
  aceitação da versão (D-023).
- CI no GitHub Actions: backend, frontend e navegador em cada PR.
- Licença MIT.

### Corrigido
- A sessão travava para sempre quando um resultado do kernel passava de 64 KiB numa
  linha (por exemplo, uma saída longa com acentos).
- Apagar o `.nbcache` com o servidor aberto e reiniciar o kernel marcava uma célula
  Mojo válida como `crashed`; agora o build é refeito.
- Um erro de compilação causado por rank errado (ou por um `comptime assert` da
  célula) aparecia como "wrapper gerado"; agora aponta a linha da célula.
- O callback de alocação das células Mojo aceitava índices negativos, que caíam na
  indexação negativa do Python.

### Estável a partir desta versão
- Formato `.nb.md` v2, interface das células Mojo e comandos `serve`/`run` (D-027).

## [0.2.0] — 2026-09-29

### Adicionado
- Estado de salvamento visível ("salvando / salvo / falha ao salvar") e nova
  tentativa após falha de escrita (D-020).
- Recuperação de rascunhos após queda do navegador ou do servidor (D-021).
- Um único servidor por notebook e detecção de alterações externas, com cópia da
  sessão antes de carregar a versão do disco (D-022).
- 17 cenários de teste no Chromium.

### Corrigido
- Células Markdown consecutivas ou vazias se perdiam ao salvar (formato v2,
  DEBT-006).

## [0.1.0] — 2026-09-25

Primeira versão: notebook reativo em que a ordem de execução vem do grafo de
dependências entre células Python e Mojo. Células Mojo compiladas em segundo plano
enquanto se digita, kernel em processo separado com recuperação de crash e botão
parar, linguagem visual dos estados das células e acesso local protegido por token.

[1.0.0]: https://github.com/IgorKammerGrahl/projetoNotebook/compare/v0.2.0...v1.0.0
[0.2.0]: https://github.com/IgorKammerGrahl/projetoNotebook/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/IgorKammerGrahl/projetoNotebook/releases/tag/v0.1.0
