# 🎼 Guia de Transição e Contextualização do Maestro — Open Maestri Linux

Bem-vindo ao comando do **Open Maestri Linux**, Maestro!

Este documento foi elaborado para garantir que você assuma a regência do projeto com total clareza sobre o produto, as decisões de arquitetura já consolidadas, o estado atual do código, as regras inegociáveis de desenvolvimento e como operar a equipe de agentes de inteligência artificial conectada ao canvas.

---

## 1. Missão do Projeto & O Papel do Maestro

### A Missão
O **Open Maestri Linux** é o porte nativo para sistemas Linux (Ubuntu 24.04/26.04 sob Wayland e X11) do aplicativo oficial **Maestri** do macOS. O Maestri é um ambiente de desenvolvimento espacial que conecta em um canvas 2D infinito:
- **Terminais de IA com Agentes Autônomos** (Claude Code, Codex, Gemini CLI, OpenCode, Shell);
- **Notas de Texto e Fichários Conectados** (Markdown sincronizado em tempo real);
- **Navegadores e Portais Web** (WebKitGTK);
- **Árvores de Arquivos e Git Worktrees** (Isolamento por "Floors");
- **Cabos Físicos e Redes de Conexão** entre terminais e agentes para colaboração inter-agente.

### O Seu Papel como Maestro
Como **Maestro** (`Maestro #2`), você é o regente técnico de todo o sistema:
1. **Liderança Técnica & Decisão de Design:** Você decide como as funcionalidades são arquitetadas, priorizando a paridade estrita e visual com a versão macOS.
2. **Orquestração da Banda de Agentes:** Você delega tarefas em paralelo para os agentes conectados no canvas do Maestri (`Vanguarda`, `Bastião`, etc.), aprova comandos interativos e integra suas entregas.
3. **Guardião da Qualidade & Compatibilidade:** Garante que todo commit passe nos testes unitários e e2e, que o schema `workspace.json` (schemaVersion: 2) permaneça íntegro e que nenhuma extensão de desktop corrompa a experiência visual do usuário.

---

## 2. Arquitetura do Sistema

O Open Maestri Linux é composto por três camadas complementares:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   Open Maestri Linux - Arquitetura                    │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   ┌────────────────────────────────────────────────────────────────┐   │
│   │               Frontend GTK 4 (`gtk4-ui/src/`)                  │   │
│   │  • Janela Principal (`ui/window.py`): HeaderBar, Paned, Overlay│   │
│   │  • Barra Superior Flutuante (`ui/floating_bars.py`): Ferramentas│  │
│   │  • Controles Inferiores 4 Cápsulas (Floor, Anéis Cota, Zoom)  │   │
│   │  • Sidebar Responsiva (`ui/sidebar.py`): Workspaces & Grupos   │   │
│   │  • Diálogos Modais macOS (`ui/terminal_dialog.py`, etc.)       │   │
│   │  • Terminal VTE (`ui/terminal_view.py`) & WebKitGTK            │   │
│   └───────────────────────────────┬────────────────────────────────┘   │
│                                   │                                    │
│   ┌───────────────────────────────▼────────────────────────────────┐   │
│   │               Canvas Engine 2D (`canvas-cairo/`)               │   │
│   │  • Renderização Cairo Pura (`maestri_canvas/renderer.py`)      │   │
│   │  • Física de Cordas Catenárias (`maestri_canvas/rope.py`)      │   │
│   │  • Máquina de Estados de Gestos (`maestri_canvas/interaction.py`)│ │
│   │  • Snapping Magnético & Guias Inteligentes (`snapping.py`)     │   │
│   └───────────────────────────────┬────────────────────────────────┘   │
│                                   │ Unix Socket / REST                 │
│   ┌───────────────────────────────▼────────────────────────────────┐   │
│   │             Backend Swift Core (`open-maestri-server`)         │   │
│   │  • Localizado em `Sources/` (compilado via Swift Package)       │   │
│   │  • PTY Process Manager (`Sources/Terminal/POSIXPtyProcess.swift`)│  │
│   │  • Servidor Inter-Agent (`Sources/InterAgent/InterAgentServer`) │  │
│   │  • Floor Manager (Git Worktrees em `Sources/Floor/`)           │   │
│   │  • Autosave & Migrations (`Sources/Workspace/PersistenceManager`)│ │
│   └────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Como Operar a Equipe de Agentes Conectada

No Maestri, você possui uma banda de agentes de IA rodando em terminais no canvas. A comunicação com eles é feita através do CLI `maestri`.

### Os Agentes Principais da Banda:
- **`Vanguarda` (Codex / GPT-6.1 Terra):** Especialista em Backend, Subprocessos e PTY. Ideal para tarefas de lógica de workspace, banco de dados, integração de APIs e testes de servidor.
- **`Bastião` (Codex / GPT-6-Astra):** Especialista em Frontend, GTK 4 e Visual. Ideal para CSS, animações, layout responsivo, supressão de extensões e paridade de pixels.
- **`Opus-Reviewer`:** Revisor sênior para auditorias de segurança, integridade de schema e validação de releases.
- **Outros agentes conectados:** `Arquiteto`, `Sentinela`, `Pixel`, `Forja`, `Monitor`, `Nexus`, `Vértice`, `Códice`.

### Comandos Essenciais do CLI `maestri`:

```bash
# 1. Listar agentes conectados e notas no canvas
maestri list

# 2. Enviar tarefa para um agente específico (aguarda conclusão)
maestri ask "Vanguarda" "Execute os testes do backend e reporte o status."

# 3. Enviar tarefas para múltiplos agentes em PARALELO
maestri ask --batch '{"Vanguarda": "Tarefa A...", "Bastiao": "Tarefa B..."}'

# 4. Inspecionar o terminal do agente em tempo real (sem enviar prompt)
maestri check "Vanguarda"
maestri check "Bastiao"

# 5. Enviar comandos ou aprovações interativas para agentes pausados
# Exemplo: aprovar prompt do Codex (opção 1 ou enter):
maestri ask "Vanguarda" --raw "1\n"
maestri ask "Bastiao" --raw "1\n"
```

> [!IMPORTANT]
> **Aprovações Interativas no Codex:**
> Quando os agentes Codex executam comandos shell arriscados, o TUI deles pausa solicitando: `1. Yes, proceed (y)`. Se um agente parecer congelado, use `maestri check "<Nome>"` para ver o terminal dele. Se estiver pausado no menu de aprovação, destrave-o enviando `maestri ask "<Nome>" --raw "1\n"`.

---

## 4. Estado Atual das Funcionalidades Entregues

Tudo o que foi implementado e validado nas últimas sprints (branch `feat/linux-port`):

1. **Dimensionamento Pré-criação de Terminais (Novidade):**
   - Ao selecionar a ferramenta **Terminal** (`T` ou botão na barra superior), o cursor vira `crosshair`.
   - O usuário arrasta um retângulo no canvas com preview de borda azul tracejada (`#0A84FF`), definindo a posição e tamanho exatos antes que o popup de escolha de IA apareça.
   - Ao soltar o clique, o modal **New Terminal** abre com dimensões já pré-definidas.
2. **Paridade Visual com macOS:**
   - Todos os emojis dos botões de ferramentas foram removidos e substituídos por **ícones vetoriais outline SVG** (`maestri-*-symbolic`).
   - Controles inferiores dispostos em **4 cápsulas (pills) horizontais com vidro escuro** que nunca se sobrepõem:
     - Pill 1: Seletor de Floor (`[ 🏢 Ground ]`);
     - Pill 2: Anéis de Cota de IA dinâmicos estilo Apple Watch desenhados em Cairo (Claude Code coral, Codex verde-água, Gemini azul royal);
     - Pill 3: Minimap / Reset View (`[ 🗺 ]`);
     - Pill 4: Controle de Zoom (`[ −  100%  + ]`).
3. **Barra Lateral Responsiva & Proteção contra Extensões GNOME:**
   - Sidebar fixa em 260px (`set_shrink_start_child(False)`), sem corte de textos longos ou campo de busca.
   - Animação suave (easing 180ms) ao recolher e expandir.
   - Botão flutuante na tela para restaurar a sidebar quando escondida.
   - Supressão contínua no nível de widget de quaisquer botões fantasmas (`titlebutton` ou `WindowControls`) injetados por extensões do GNOME Shell, preservando os três semáforos macOS (`traffic_lights`).
4. **Resiliência Offline Completa:**
   - Criação de workspaces com o botão `+` da barra lateral funciona tanto com o backend Swift core online quanto offline (fallback atômico escrevendo em `~/.open-maestri/manifest.json` e `workspaces/<id>/workspace.json`).
5. **Execução Interativa de Modelos & Descoberta de PATH:**
   - Comandos como `claude`, `codex`, `gemini` são disparados em shell de login interativo (`bash -i -l -c "<cmd>; exec bash"`), garantindo que o terminal não feche após o comando e descobrindo binários em `~/.nvm/versions/node/*/bin`, `~/.cargo/bin` e `~/.local/bin`.

---

## 5. Regras Inegociáveis de Engenharia (Working Agreement)

1. **Controle de Branches e Git:**
   - Branch ativa principal: `feat/linux-port`.
   - **NUNCA faça commit direto na branch `main`**.
   - Cada rodada de mudanças deve terminar com um commit em estilo Conventional Commits com emojis:
     - `feat: ✨ ...`
     - `fix: 🐛 ...`
     - `refactor: ♻️ ...`
     - `test: 🧪 ...`
     - `docs: 📝 ...`
   - Realize `git push origin feat/linux-port` ao finalizar uma entrega redonda.
2. **Ambiente de Testes:**
   - Todo teste GTK4 ou visual deve ser executado sob `DISPLAY=:99`:
     ```bash
     DISPLAY=:99 python3 -m pytest tests/e2e/test_visual_parity_mac_e2e.py -v
     DISPLAY=:99 python3 -m pytest tests/e2e/test_sidebar_responsive.py -v
     DISPLAY=:99 python3 -m pytest tests/e2e/test_sprint7_context_menus_and_pills_e2e.py -v
     DISPLAY=:99 python3 -m pytest tests/unit/test_workspace_creation.py -v
     ```
3. **Compilação Swift no Linux:**
   - O linker `lld` é obrigatório para evitar falhas de relocação no Linux:
     ```bash
     swift build -Xswiftc -use-ld=lld
     swift test -Xswiftc -use-ld=lld
     ```
4. **Regra de Ouro do GTK 4 / Python 3.14 (Prevenção de Segfaults):**
   - Diálogos modais (`Gtk.Window` com `modal=True` e `transient_for`) sofrem segfault nativo em Python 3.14 se forem fechados abruptamente enquanto o loop de eventos processa surtos de destruição.
   - **Sempre implemente o desvínculo seguro antes do `super().close()`**:
     ```python
     def close(self) -> None:
         try:
             self.set_visible(False)
             self.set_modal(False)
             self.set_transient_for(None)
         except Exception:
             pass
         super().close()
     ```
   - Timers contínuos (`GLib.timeout_add`) ou callbacks de animação (`paned.add_tick_callback`) devem ser **obrigatoriamente removidos** no método `_cleanup()` da janela.

---

## 6. Próximos Passos Recomendados para o Novo Maestro

1. **Acompanhar a Validação do Usuário:**
   - O usuário está testando visualmente a criação e o dimensionamento prévio do terminal no canvas. Solicite feedback sobre a sensibilidade do arrasto e o fluxo dos modelos.
2. **Migração Offline de Notas (Item 1.0.1):**
   - Notas criadas em modo offline ficam salvas no diretório temporário local; ao restabelecer a conexão com o backend Swift core, sincronizá-las formalmente via REST `/api/notes`.
3. **Refinamento do Ask-Agent com Foco:**
   - Ao enviar comandos de automação para terminais focados, certificar-se de que o input não interrompa uma execução interativa de shell em andamento.

---

*Sucesso na regência do Open Maestri Linux, Maestro! A orquestra está afinada e pronta para suas ordens.*
