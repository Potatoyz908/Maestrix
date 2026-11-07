<p align="center">
  <img src="Sources/Assets.xcassets/AppIcon.appiconset/icon_512x512@2x.png" alt="Maestrix" width="96">
</p>

<h1 align="center">Maestrix</h1>

<p align="center">
  <strong>Canvas de orquestração de múltiplos agentes de IA para Linux</strong>
  <br>
  Gerencie agentes como um time, não como uma pilha de terminais.
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/licen%C3%A7a-GPL--3.0--or--later-green?style=flat-square" alt="Licença GPL-3.0-or-later"></a>
  <img src="https://img.shields.io/badge/plataforma-Linux%20(Ubuntu%20%2F%20Debian)-orange?style=flat-square" alt="Plataforma Linux">
  <img src="https://img.shields.io/badge/interface-GTK%204%20%2B%20Cairo-blue?style=flat-square" alt="GTK 4 + Cairo">
  <img src="https://img.shields.io/badge/core-Swift%206-F05138?style=flat-square" alt="Swift 6">
  <a href="https://github.com/Potatoyz908/Maestrix/actions/workflows/ci.yml"><img src="https://github.com/Potatoyz908/Maestrix/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

<p align="center">
  <a href="#início-rápido">Início rápido</a> &middot;
  <a href="#como-funciona">Como funciona</a> &middot;
  <a href="#recursos">Recursos</a> &middot;
  <a href="#cli-omaestri--maestrix-cli">CLI</a> &middot;
  <a href="README.linux.md">Guia Linux</a> &middot;
  <a href="docs/roadmap.md">Roadmap</a> &middot;
  <a href="CONTRIBUTING.md">Contribuindo</a>
</p>

<p align="center">
  <img src="docs/images/maestrix-dark-home.png" alt="Maestrix no tema Dark" width="860">
</p>

---

## O que é o Maestrix?

O Maestrix é uma **camada de orquestração baseada em canvas** para a era dos agentes de IA. Ele não é um agente: é o espaço de trabalho que cerca e coordena os agentes que você já usa (Claude Code, Codex, Gemini CLI, Antigravity, OpenCode ou um shell comum).

Coloque terminais, agentes, notas Markdown, navegadores de arquivos e navegadores web num canvas espacial infinito. Conecte-os com cabos animados por física. Deixe os agentes conversarem entre si pela CLI `omaestri`, sem você precisar ser o roteador de contexto entre janelas.

> Quando vários agentes de código rodam ao mesmo tempo, o desenvolvedor vira o mensageiro entre terminais. O Maestrix remove esse atrito.

O Maestrix é a versão **nativa para Linux** (GTK 4 + Cairo, núcleo em Swift) do [OpenMaestri para macOS](https://github.com/zlh-428/open-maestri). O [Maestri original](https://maestriapp.com) é a referência de conceito e interface.

## Por que o Maestrix?

| | **Maestrix** | OpenMaestri | Maestri |
|---|---|---|---|
| Plataforma | **Linux** (Ubuntu / Debian) | macOS 14+ | macOS |
| Interface | **GTK 4 + Cairo nativos** | SwiftUI / AppKit | Proprietária |
| Licença | **GPL-3.0-or-later** | GPL-3.0-or-later | Proprietária |
| Código-fonte | Aberto | Aberto | Fechado |
| Formato de dados | Compatível (`~/.open-maestri`) | Origem | Nativo |
| CLI para agentes | `omaestri` (alias `maestrix-cli`) | `omaestri` | `maestri` |

## Início rápido

### Opção 1: pacote `.deb` (recomendado)

O pacote traz os binários, o runtime Swift embutido e a integração com o menu de aplicativos.

```bash
sudo apt install ./releases/maestrix_1.0.0_amd64.deb
```

Também é possível baixar o `.deb` na página de [Releases](https://github.com/Potatoyz908/Maestrix/releases). Depois da instalação, procure **Maestrix** no menu de aplicativos ou rode `maestrix` no terminal.

### Opção 2: a partir do código-fonte

Requisitos: Ubuntu 22.04+ (x86_64), Swift 6, Python 3.10+ com PyGObject, GTK 4 e Cairo. A lista completa de dependências está no [guia Linux](README.linux.md).

```bash
git clone https://github.com/Potatoyz908/Maestrix.git
cd Maestrix
swift build -Xswiftc -use-ld=lld
./scripts/install-desktop.sh
maestrix
```

O lançador `maestrix` inicia o servidor em segundo plano (se ainda não estiver rodando) e abre a interface. Se o servidor cair com o app aberto, o app o reinicia sozinho.

Para gerar o pacote `.deb` localmente: `./scripts/build-deb.sh`.

### Primeiros passos

1. Crie um **workspace** na barra lateral (`+`), escolhendo nome, ícone e pasta de trabalho.
2. Adicione um terminal pela barra de ferramentas e escolha um preset (Claude Code, Codex, Gemini CLI, Antigravity, OpenCode ou Shell).
3. Arraste de um nó a outro para **conectá-los**. Conectados, os agentes se enxergam e podem trocar mensagens.
4. Em um terminal conectado, rode `omaestri list` para ver os vizinhos e `omaestri ask "Nome" "pedido"` para delegar uma tarefa.

## Como funciona

```
┌──────────────────────────────┐        ┌────────────────────────────────────┐
│  Interface GTK 4 + Cairo     │  REST  │  Servidor Swift                    │
│  canvas, cabos, terminais    │◄──────►│  workspaces, terminais (PTY),      │
│  (VTE), notas, arquivos,     │  WS    │  conexões, floors, rotinas, SSH    │
│  portais (WebKitGTK)         │        └──────────────┬─────────────────────┘
└──────────────────────────────┘                       │ socket Unix + HTTP
                                                       ▼
                                     ┌─────────────────────────────────────┐
                                     │  omaestri (CLI) dentro de cada      │
                                     │  terminal: list · ask · check · …   │
                                     └─────────────────────────────────────┘
```

- **O servidor é dono dos shells.** Os terminais são PTYs do servidor; a interface apenas os exibe. Por isso trocar de workspace, ou fechar e reabrir o app, não derruba um shell em execução, e o histórico da tela é restaurado.
- **A interface descobre o servidor sozinha.** O servidor publica sua porta em `~/.open-maestri/run/server.port`; o app a lê e reconecta quando o servidor volta.
- **Agentes se comunicam pelo servidor.** Cada terminal recebe `MAESTRI_SOCKET`, `MAESTRI_TERMINAL_ID` e a CLI no `PATH`; as permissões seguem as conexões desenhadas no canvas.
- **Dados em disco** ficam em `~/.open-maestri` (workspaces em `workspace.json`, notas em Markdown, scrollback dos terminais), no mesmo formato do Maestri.

## Recursos

<details open>
<summary><strong>Canvas infinito e nós</strong></summary>

- Terminais, notas Markdown, árvore de arquivos, portais (navegador embutido), textos e desenhos (retângulo, elipse, losango).
- Cabos conectando nós, com simulação de física; alinhamento magnético (snapping) e grade de pontos.
- Zoom, mapa e seleção em grupo; copiar e colar elementos entre workspaces.

<img src="docs/images/maestrix-dark-agents.png" alt="Terminais, nota e árvore de arquivos no canvas (cena de demonstração)" width="760">

</details>

<details>
<summary><strong>Terminais e agentes</strong></summary>

- Terminais reais (VTE) com presets: Claude Code, Codex, Gemini CLI, Antigravity (`agy`), OpenCode e Shell. Papéis (roles) personalizáveis por terminal.
- **Nomes únicos por workspace**: ao repetir um nome, o Maestrix numera (`Agente #2`).
- **Retomada de conversa**: se o shell de um agente se perdeu (servidor reiniciado, por exemplo), ao reabrir o workspace o agente volta pela última conversa (`claude --continue`, `agy --continue`, `codex resume --last`, `opencode --continue`).
- Indicador de atividade do agente, histórico de tela persistido e botão de fechar em cada nó.

<img src="docs/images/create-terminal.png" alt="Diálogo de novo terminal" width="560">

</details>

<details>
<summary><strong>Comunicação entre agentes e Modo Maestro</strong></summary>

- Agentes conectados trocam mensagens pela CLI: `omaestri ask` envia e espera a resposta; `omaestri check` lê a saída de outro agente; `omaestri note` lê e escreve notas conectadas.
- No **Modo Maestro**, um terminal pode recrutar outros agentes (`omaestri recruit`), conectá-los, atribuir papéis e dispensá-los. As skills necessárias são instaladas automaticamente nos diretórios de skills dos agentes (por exemplo `~/.claude/skills`).

</details>

<details>
<summary><strong>Floors (git worktrees), rotinas e partituras</strong></summary>

- **Floors**: ramos isolados do mesmo repositório como git worktrees, com hooks de setup, execução e teardown; o botão *Ground* volta ao repositório principal e *Land* traz os commits de volta.
- **Rotinas**: comandos e lembretes agendados para um agente.
- **Partituras**: salve e reaplique um arranjo de agentes, papéis e conexões.

</details>

<details>
<summary><strong>Arquivos, notas e Git</strong></summary>

- Árvore de arquivos em lista ou grade, com badges de status Git e visualização de diffs.
- Notas Markdown com visualização formatada, que também podem apontar para um arquivo existente.

<img src="docs/images/maestrix-dark-files.png" alt="Notas e árvore de arquivos" width="760">

</details>

<details>
<summary><strong>Remote SSH (workspaces remotos)</strong></summary>

- Escolha um host (lido de `~/.ssh/config` ou informado manualmente), teste a conexão, navegue pela pasta remota e abra um workspace cujos terminais rodam na máquina remota, na pasta escolhida.
- Primeira fase entregue: lista de hosts, teste de conexão, navegador de pastas, terminais remotos com conexão compartilhada. Túnel reverso, `omaestri` no remoto e árvore de arquivos remota ainda não existem. Veja [docs/linux-ssh-spec.md](docs/linux-ssh-spec.md).

</details>

<details>
<summary><strong>Temas e preferências</strong></summary>

- Tema da interface **Light** ou **Dark · Maestrix** (paleta do ícone: azul, roxo e ciano sobre azul-marinho), em *Preferências → General → Interface theme*; o tema dos terminais é independente.
- Fonte e tema do terminal, plano de fundo do canvas, intervalo de autosave, atalhos e editor preferido.

<img src="docs/images/maestrix-light-home.png" alt="Maestrix no tema Light" width="760">

</details>

## CLI `omaestri` / `maestrix-cli`

A CLI é injetada em cada terminal conectado (`maestrix-cli` e `open-maestri` são aliases de compatibilidade):

```bash
omaestri list                            # agentes, notas e portais conectados
omaestri ask "Revisor" "Revise meu PR"   # envia a mensagem e espera a resposta
omaestri check "Builder"                 # lê a saída atual de outro agente
omaestri note read "Spec"                # lê uma nota conectada
omaestri note write "Spec" "conteúdo"    # escreve em uma nota conectada
omaestri recruit "Helper" --preset claude_code   # Modo Maestro: cria e conecta um agente
```

Comandos disponíveis: `list`, `ask`, `check`, `note`, `portal`, `recruit`, `dismiss`, `connect`, `role`, `preset`, `partitura`, `routine`, `workspace`, `floor`.

## Compatibilidade

Os workspaces existentes em `~/.open-maestri`, o protocolo `maestri://`, os identificadores internos e as variáveis `OPEN_MAESTRI_*` são mantidos para preservar dados e integrações. A mudança de nome para Maestrix não exige migrar seus projetos. Para isolar testes dos seus dados reais, defina `OPEN_MAESTRI_DATA_DIR`.

## Estrutura do repositório

| Caminho | Conteúdo |
|---|---|
| `gtk4-ui/` | Interface GTK 4: janela, barra lateral, diálogos, temas, floors |
| `canvas-cairo/` | Motor do canvas em Cairo: nós, cabos, terminais VTE, portais |
| `maestri_core/` | Núcleo Python: modelos, cliente do servidor, preferências, sincronização |
| `Sources/` | Servidor e CLI em Swift (`open-maestri-server` e `omaestri`; no pacote `.deb` aparecem como `maestrix-server` e `maestrix-cli`) |
| `scripts/` | Lançador, instalação do desktop, empacotamento `.deb`, testes e2e |
| `tests/` | Testes unitários, de integração e e2e (Xvfb) |
| `docs/` | Arquitetura, roadmap, especificações e referência do produto |

## Desenvolvimento e testes

```bash
swift build -Xswiftc -use-ld=lld          # servidor e CLI
swift test  -Xswiftc -use-ld=lld          # testes Swift
python3 -m pytest tests/unit canvas-cairo/tests tests/integration   # testes Python
scripts/run-e2e-xvfb.sh                   # testes de interface em um Xvfb privado
```

Os testes de interface rodam em um display virtual (Xvfb) e usam um diretório de dados isolado (`OPEN_MAESTRI_DATA_DIR`), sem tocar nos seus workspaces. Veja [docs/quality.md](docs/quality.md) e o [guia Linux](README.linux.md).

## Contribuindo

Contribuições são bem-vindas. Leia [CONTRIBUTING.md](CONTRIBUTING.md) antes de abrir um PR e [SECURITY.md](SECURITY.md) para reportar vulnerabilidades. Abra uma *issue* para bugs e sugestões em [github.com/Potatoyz908/Maestrix/issues](https://github.com/Potatoyz908/Maestrix/issues).

## Créditos e licença

- **Euller**: desenvolvimento e adaptação do Maestrix para Linux.
- **OpenMaestri e seus contribuidores**: base de código para macOS e recursos herdados, incluindo o ícone presente no repositório.
- **Maestri original**: referência de produto, conceito e interface.
- Bibliotecas e dependências mantêm suas licenças e atribuições próprias.

Distribuído sob a **GPL-3.0-or-later**. Veja [LICENSE](LICENSE), [NOTICE](NOTICE) e a [documentação herdada](docs/UPSTREAM-README.md). O Maestrix é um derivado independente; não é uma versão oficial dos projetos de origem.
