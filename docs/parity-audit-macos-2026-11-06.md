# Auditoria de paridade: Open Maestri Linux vs. macOS

Data: 2026-11-06 · Branch `feat/linux-port` · Referência: `docs/reference/maestri-reference-index.md`, `docs/reference/official/*.md`, `docs/reference/maestri-cli.md`

## Como ler este documento

- Quatro auditorias independentes, uma por bloco da referência, **leram o código-fonte sem executar nada** (sem GUI, sem testes, sem build). O que depende de execução está marcado `UNVERIFIED` ou "evidência estática".
- Cada linha traz status, evidência `arquivo:linha` e prioridade. **P0** = fluxo central quebrado ou ausente; **P1** = diferença visível de paridade; **P2** = acabamento.
- Status: `IMPLEMENTED` (handler ligado ponta a ponta) · `PARTIAL` · `STUB` (UI existe, handler não faz nada) · `MISSING` · `DIVERGENT` (funciona, mas difere do macOS) · `N/A-LINUX` (só macOS: Sparkle, Spotlight, Ombro etc.).
- Teclas: ⌘ do macOS = Ctrl no Linux.
- **O que o Maestro confirmou por conta própria na fonte** está marcado com **[V]** na tabela abaixo. Os demais achados são do auditor.
- Ombro está fora do escopo do port (CLAUDE.md) e não conta como lacuna.

## Contagem (324 linhas; algumas aparecem em mais de uma parte)

| Parte | Escopo | IMPL | PARTIAL | STUB | MISSING | DIVERG | N/A | UNVER |
|---|---|---|---|---|---|---|---|---|
| 1 | Layout, workspaces/sidebar, toolbar/atalhos, menus de contexto | 47 | 13 | 8 | 16 | 4 | 3 | 3 |
| 2 | Canvas e terminal | 36 | 13 | 11 | 11 | 4 | 0 | 2 |
| 3 | Notas, conexões, file tree, Maestro | 30 | 18 | 7 | 12 | 6 | 2 | 0 |
| 4 | Floors, portal, rotinas, SSH, CLI | 29 | 9 | 6 | 22 | 11 | 1 | 0 |
| **Total** | | **142** | **53** | **32** | **61** | **25** | **6** | **5** |

Cerca de 44% das linhas estão `IMPLEMENTED`. Os `STUB` são os mais enganosos: a interface existe e parece funcionar, mas o handler não faz nada.

## Os problemas mais graves (P0), consolidados e sem repetição

| # | Problema | Onde | Verificação |
|---|---|---|---|
| 1 | **Copy / Paste / Select All do menu do terminal não fazem nada**, e Ctrl+Shift+C/V não estão ligados. O código procura `f.content.terminal`, mas o atributo real é `.term` (ou o próprio widget no shell local). `hasattr` falha e a ação vira no-op silencioso. | `gtk4-ui/src/ui/context_menus.py:403-431`; correto: `terminal_of()` em `canvas-cairo/maestri_canvas/widget.py:165` | **[V]** código lido; nenhum outro uso de `copy_clipboard`/`paste_clipboard` no projeto |
| 2 | **Agentes ligados por conexão provavelmente não conseguem `ask`/`list` entre si** quando os terminais foram criados na GUI. O `CanvasNode` recebe um uuid, o `TerminalContent` gera OUTRO. A conexão é criada com ids de nó; o PTY e `MAESTRI_TERMINAL_ID` usam o id do terminal; o servidor procura conexões pelo id do terminal. No macOS os dois ids são iguais. | `window.py:1577,1602,1701` (criação), `maestri_core/client/api.py:1083` (sem tradução), `AskHandler.swift:23` | **[V]** código lido de ponta a ponta; **não exercitado em execução**. Vale um teste real com Xvfb |
| 3 | **Conexões persistidas não voltam depois que o servidor reinicia.** `restoreConnections` só é chamado pelo código macOS. | `ConnectionManager.swift:195`; único chamador `WorkspaceCanvasView.swift:353` | **[V]** |
| 4 | **As skills nunca são instaladas no Linux.** `SkillInjector.inject` é um no-op e `installSkillsIfNeeded` só roda no `AppDelegate` do macOS. Agentes iniciados aqui não aprendem a usar o CLI. | `Sources/Connection/SkillInjector.swift:15-18`; chamador `Sources/App/AppDelegate.swift:35` | **[V]** |
| 5 | **`ask` não funciona com agentes reais (Claude/Codex).** Timeout fixo de 30 s, heurística de prompt de shell e texto + CR num único write, que TUIs tratam como paste. A skill espera trabalhos de até 20 min. | `Sources/InterAgent/Handlers/AskHandler.swift:9,46-47` | **[V]** |
| 6 | **Qualquer `portal …` por CLI derruba o servidor**, e com ele todos os terminais. `PlatformHooks.portals` faz `fatalError` quando o serviço não foi injetado, e o servidor Linux não injeta. `list` também cai quando há conexão terminal↔portal. | `Sources/Shared/PlatformHooks.swift:21`; `Sources/Server/main.swift` não injeta | **[V]** código lido; **não provocado em execução** |
| 7 | **Notas gerenciadas não sincronizam ao vivo com o CLI.** O widget da nota é criado sem `watch_path`, então `note write` de um agente não aparece, e a próxima digitação do usuário sobrescreve. | `canvas-cairo/maestri_canvas/widget.py:~574`, `sticky_note.py:630` | **[V]** |
| 8 | **Modo Maestro inoperante de ponta a ponta.** O checkbox do diálogo é descartado, não há toggle em Edit, `is_manager` nunca é gravado pela UI, a skill de gerente não é instalada, e `recruit` cria PTY e conexão em memória mas nenhum nó no canvas. | `terminal_dialog.py:233-235`, `window.py:1583-1606`, `MaestroHandlers.swift:26-124` | Auditor (item 4 acima confirma parte da causa) |
| 9 | **Clique direito em file tree / nota / portal provavelmente não abre o menu próprio do nó.** O canvas reivindica o gesto em fase CAPTURE antes de decidir o alvo. | `window.py:~1402` (`CLAIMED`) vs `file_tree_node.py:536-541` | **[V]** o claim existe; o efeito em execução **não foi confirmado** |
| 10 | **CLI de notas sem escopo e com nomes errados.** Resolve por nome em todos os workspaces (substring), notas de path custom nunca são achadas, `list` mostra sempre "Note", `note create` só grava o arquivo (sem nó nem conexão). | `Sources/InterAgent/Handlers/NoteHandler.swift:98-170`, `ListHandler.swift:76` | Auditor |

## Temas P1 que mais pesam (visíveis ao usuário)

**Atalhos e navegação:** ⌘↑/↓ de workspace; ⌘+dígito para saltar entre terminais e badges numéricos; tecla `L` para conexão; Ctrl+C/V de nós; tecla N/F anunciadas em tooltip mas inexistentes; formas, elipse, losango e marca-texto sem acesso na toolbar (`DrawingTools.button` nunca é empacotado).

**Menus de contexto:** o menu do nó é o mesmo (de terminal) para todos os tipos; Reload, Unload, Copy Element, New Element Defaults e Partituras são stubs; Paste ignora a área de transferência; Rename por botão direito não funciona em notas; não existe "Delete Connection".

**Persistência que só vale na sessão:** Lock (`is_locked` nunca gravado nem lido ao carregar) e z-order; Monitor Activity grava num campo que o modelo não serializa.

**Terminal:** sem busca, sem links clicáveis, sem bell/notificação, sem status ocupado/ocioso, sem scrollback em disco (perde tudo ao reiniciar o servidor); o modal "New Terminal" descarta Monitor, Maestro, tema, fonte e role; presets só gravam o comando (sem ícone/cor); roles ausentes (Settings → Agents, injeção, reatribuição).

**Notas:** delete inconsistente (Del/⌘W/X deixam o `.md` órfão; o menu ⋯ apaga arquivo mesmo em path custom, contra a referência); soltar arquivo sobre nota existente não faz nada.

**Floors:** sem canvas por floor, sem "Clone Ground layout", sem renomear, sem lista de arquivos alterados, sem hooks (UI, execução e variáveis de ambiente nos PTYs do core).

**Rotinas:** o próximo prompt dispara após 5 s de silêncio, não ao terminar de fato; trocar de workspace pode pausar silenciosamente rotinas de outros; sem comando `routine` no CLI.

**CLI:** sem `maestri`/`MAESTRI_CLI` nos terminais Linux; faltam `routine`, `workspace`, `floor`, `partitura`, `band`; `note read` sem o formato `[N lines total]`.

**Remote SSH:** ausente por inteiro (UI, túnel e provider).

**Dados falsos:** os anéis de cota de IA na cápsula inferior mostram valores fixos (68%/42%/25%) apresentados como reais.

## Observações de qualidade do repositório

- `gtk4-ui/src/ui/canvas_widget.py` e `gtk4-ui/src/ui/filetree.py` são código morto (nada os importa).
- Os testes e2e do menu de nó (`tests/e2e/test_sprint7_context_menus_and_pills_e2e.py:83-130`) só conferem troca de atributo ou de classe CSS, o que mascara os stubs acima.
- `maestri_core/client/api.py` define `get_sidebar_layout`/`save_sidebar_layout` duas vezes (a versão dict vence e quebra offline).
- O menu File/View/Help é montado em `window.py:1047-1076` mas nada o instala (código morto).
- Strings de Floors estão em português num app em inglês.

## O que esta auditoria NÃO provou

- Nada foi executado: nenhum item `IMPLEMENTED` foi testado em tempo de execução, e os itens 2, 6 e 9 dos P0 são conclusões de leitura de código.
- A aparência visual (borda tracejada de seleção, blur, nitidez do terminal sob zoom) não foi conferida.
- Alt+arrastar (duplicar) e pinça em hardware real podem se comportar diferente no seu desktop.

## Sugestão de ordem de correção

1. Itens 1 e 7 (pequenos, localizados, de alto impacto no uso diário).
2. Itens 3, 4 e 5 + o teste do item 2: sem isso a colaboração entre agentes, que é o centro do produto, não funciona.
3. Item 6 (portal derruba o servidor): no mínimo trocar o `fatalError` por uma resposta de erro, antes mesmo da ponte completa.
4. Item 8 (Maestro) depende de 2-5 e da criação de nós por CLI no canvas.
5. Em seguida os P1 por tema.

---
## Parte 1 — Layout, Workspaces/Sidebar, Toolbar/Atalhos, Menus de contexto (ref §1, 2, 14, 15; official 01/02)

Auditor leu apenas código (nada executado). Árvore em edição por outros agentes no momento da leitura.
Nota do auditor sobre a referência: ela não fala de drag-reorder nem de SSH na criação de workspace; o Swift original (`WorkspaceSidebarView.swift`) também não tem drag-reorder.

### 1.1 Layout geral

| Feature (ref) | Status | Evidência | Gap / o que corrigir | Prio |
|---|---|---|---|---|
| Sidebar esquerda, largura fixa, recolhível (§1) | IMPLEMENTED | `window.py:76-85`, `:1228-1261`, `:122-134`, Ctrl+B `:1117` | — | — |
| Canvas infinito (§1) | IMPLEMENTED | `window.py:86-138` | Cairo em vez de Pixi.js, sem impacto | — |
| Toolbar superior (§1/§14) | PARTIAL | `floating_bars.py:23-110` | Ver 1.3 | P1 |
| Zoom % + minimapa no canto inferior direito (§1) | IMPLEMENTED | `floating_bars.py:580-770` | — | — |
| Cápsula de Floor ao lado do zoom | IMPLEMENTED (UNVERIFIED a fundo) | `floors.py:748-800`, `:915-925` | — | — |
| Anéis de agentes (cápsula 2) | STUB | `floating_bars.py:266-365`: valores fixos 68%/42%/25% e tooltip falso; `set_model_usage` nunca é chamado | Mostra dados inventados. Usar atividade real ou ocultar | P1 |
| Botão "Open in editor" (§1, official 02) | MISSING | só existe a preferência `settings_window.py:88`; nada abre o IDE | Botão no header que lança o IDE preferido no `working_directory` | P1 |
| Header: Add, toggle sidebar, Preferences, status | IMPLEMENTED | `window.py:920-1003` | Semáforos macOS falsos (`:927-955`) escondem controles reais do WM (`:1210-1226`) — DIVERGENT | P2 |
| Barra de menu File/View/Help | STUB | `window.py:1047-1076` monta o `Gio.Menu`, nada o instala (código morto) | Faltam acessos visíveis a New Terminal, Routines, Snap | P2 |
| Workspaces rodam em background ao trocar (official 02) | UNVERIFIED | `window.py:1806-1818` remove e recria nós; terminais core-backed sobrevivem (`:1180`), locais não | Confirmar que o shell volta com scrollback | P1 |
| Spotlight / escopo macOS-only | N/A-LINUX | — | — | — |

### 1.2 Workspace e sidebar

| Feature (ref) | Status | Evidência | Gap | Prio |
|---|---|---|---|---|
| Botão "+" (New Workspace… / New Group…) | IMPLEMENTED | `sidebar.py:145-152`, `:755-760` | — | — |
| Criar workspace: diretório + ícone | IMPLEMENTED | `workspace_dialogs.py:189-337`, `:42-133` | — | — |
| Criar em local ou SSH | MISSING | só grava `"locationType": "local"` (`workspace_dialogs.py:87,109`) | Sem seletor; modelo/servidor já aceitam `locationType` (`api.py:756`). Swift original também não tem | P2 |
| Cartões com ícone, nome, caminho, cor | IMPLEMENTED | `sidebar.py:431-504` | `icon-color-rainbow` não existe no CSS (`window.py:819` só cobre `.color-rainbow`) | P2 |
| Ícones SF Symbol → outline | PARTIAL | `sidebar.py:52-91` (28 nomes, resto cai em folder) | Lista fechada | P2 |
| Cápsula de contagem de terminais (some em 0) | IMPLEMENTED | `sidebar.py:475-488`, `:255-317` | Falta contador de atividade não lida (`unreadActivityCount` no Swift) | P2 |
| Busca de workspaces | IMPLEMENTED | `sidebar.py:123-127`, `:240-246` | — | — |
| Pin/Unpin; fixados primeiro | IMPLEMENTED | `sidebar.py:698-707`, `:352-354` | — | — |
| Grupos colapsáveis | IMPLEMENTED | `sidebar.py:375-429` | — | — |
| Folders distintas de Groups (official 02) | DIVERGENT | só existe grupo-com-ícone-de-pasta (Swift também) | — | P2 |
| Drag-reorder / mover entre grupos por arrasto | MISSING | sem `DragSource`/`DropTarget`; ordem = fixado+nome (`:354`), ignora `top_level_items` | Não está na referência nem no Swift | P2 |
| Menu workspace: Edit…, Duplicate, Open in File Manager, Pin, Remove/Move to Group, New Group with Workspace, Delete… | IMPLEMENTED | `sidebar.py:555-753`, `workspace_dialogs.py:537` | "Move to:" é lista plana em vez de submenu | P2 |
| Menu do grupo: Rename / Delete | IMPLEMENTED | `sidebar.py:581-592`, `:788-800` | — | — |
| Edit sheet: nome, ícone (24), cor (9), diretório | IMPLEMENTED | `workspace_dialogs.py:340-534` | — | — |
| Edit sheet: instruções do agente, CLAUDE.md/AGENTS.md (official 02) | MISSING | só `syncConfigFiles: False` fixo (`workspace_dialogs.py:122`) | Editor de instruções. Swift original também não tem | P1 |
| "Manter CLAUDE.md e AGENTS.md em sincronia" | MISSING | `syncConfigFiles` só no modelo (`maestri_core/models/workspace.py:104`) | Switch + rotina de sync | P2 |
| Número customizado por workspace | MISSING | grep `shortcut_number`, `workspaceNumber`: nada | Depende dos atalhos | P2 |
| ⌘↑/⌘↓ próximo/anterior workspace (§2/§14) | MISSING | só `window.py:1115-1120`, `routines_window.py:94`; Swift tem `navigateWorkspace` (`WorkspaceSidebarView.swift:209`) | `win.next-workspace`/`win.prev-workspace` (Ctrl+Down/Up) | P1 |
| Duplo ⌘ + número | MISSING | — | Overlay de números na sidebar | P2 |
| ⌘ + scroll cicla workspaces | MISSING | `interaction.py:389-398`: Ctrl+scroll = zoom | Conflito; escolher outro modificador | P2 |
| Atalhos customizáveis (official 02) | STUB | `settings_window.py:22-29,126-130`: só exibição, 6 itens | Sem rebind | P2 |
| Persistência manifest/workspace.json/sidebar-layout | IMPLEMENTED | `core_link.py:128-376`; servidor `InterAgentServer.swift:1308-1594` | — | — |

### 1.3 Toolbar e atalhos (§14)

| Feature | Status | Evidência | Gap | Prio |
|---|---|---|---|---|
| Select (Esc) | IMPLEMENTED | `floating_bars.py:42`, `interaction.py:429,360` | Só com foco no canvas | — |
| Terminal (tool + T) | IMPLEMENTED | `floating_bars.py:129-130`, `drawing.py:60-63`, `window.py:1586`, `interaction.py:444` | — | — |
| Note | PARTIAL | `floating_bars.py:131-133` cria no centro | macOS desenha o frame; tooltip diz "(N)" mas tecla N não existe | P2 |
| Text | IMPLEMENTED | `floating_bars.py:143-144`, `drawing.py:66-78` | — | — |
| Drawing (formas, caneta, marca-texto) | PARTIAL | `floating_bars.py:145-146` só ativa `freehand_pen`; `DrawingTools.button`/popover (`drawing.py:48-62`) nunca empacotado | Retângulo, elipse, losango e marca-texto inalcançáveis. Trocar por MenuButton com o popover | P1 |
| File (attach) | IMPLEMENTED | `floating_bars.py:134-135,197-219` | — | — |
| File Tree | IMPLEMENTED | `floating_bars.py:137-139` | Tooltip promete "(F)"; tecla F não existe | P2 |
| Connection (tecla L) | PARTIAL | só pins esquerdo/direito (`interaction.py:196-200`); botão "Connect Nodes" (`floating_bars.py:174-190`) liga ao primeiro nó arbitrário; sem tecla L | Modo conexão: L inicia, clique no alvo conecta | P1 |
| Portal (P) | IMPLEMENTED | `floating_bars.py:140-142`, `interaction.py:442` | — | — |
| Barra contextual Edit/Duplicate/Delete | IMPLEMENTED | `floating_bars.py:65-109,157-195` | — | — |
| Deletar nó (⌘W) | DIVERGENT | `interaction.py:422,434` | Adaptado p/ evitar readline; razoável | — |
| Copiar nó (⌥+arrastar) | IMPLEMENTED | `interaction.py:183,294-309`, `window.py:1467-1495` | Alt+arrastar pode ser capturado pelo WM (UNVERIFIED) | P2 |
| Focar nó (`\`) | IMPLEMENTED | `interaction.py:436-441` | — | — |
| Zoom ⌘+/⌘- | IMPLEMENTED | `interaction.py:413-419`, `widget.py:1002-1010`; Ctrl+0 reseta | Foco na sidebar não zooma | P2 |
| Snapping magnético (⌘+arrastar) | IMPLEMENTED | `interaction.py:278-283`; guias `Ctrl+;` | — | — |
| Saltar p/ terminal por número (⌘+dígito) | MISSING | grep `jump_to_terminal`, `terminal_by_index`: nada | Ctrl+1..9 foca o N-ésimo terminal | P1 |
| Setas movem nó selecionado | IMPLEMENTED (extra) | `interaction.py:448-460` | — | — |
| Ombro (⇧O) | N/A-LINUX | — | — | — |
| Copiar/colar no terminal (Ctrl+Shift+C/V) | MISSING | grep `copy_clipboard`/`paste_clipboard`: só handlers quebrados do menu; `terminal_node.py` não liga atalho | VTE GTK4 não faz sozinho | P0 |

### 1.4 Menus de contexto (§15 + spec Sprint 7)

| Feature | Status | Evidência | Gap | Prio |
|---|---|---|---|---|
| Disparo por clique direito, Esc/fora fecha | IMPLEMENTED | `window.py:1375-1424` | Popovers de botões em vez de `Gio.Menu` do spec | — |
| Clique direito em file tree/nota/portal mantém menu próprio | DIVERGENT, provável regressão (UNVERIFIED) | `window.py:1402` faz `CLAIMED` p/ qualquer alvo; `file_tree_node.py:536-541` tem `GestureClick(button=3)` próprio | Claim em CAPTURE no pai nega os filhos: menu de file tree/Git provavelmente nunca abre | P0 |
| Canvas ▸ Add ▸ Terminal (presets) / Custom… | IMPLEMENTED | `context_menus.py:93-216,254-278` | Submenus são páginas de Stack; 5 presets fixos | P2 |
| Add ▸ Note / File Tree / Portal | IMPLEMENTED | `context_menus.py:146-165` | — | — |
| Add ▸ Text | PARTIAL | `context_menus.py:167-172,280-282` só ativa a ferramenta | Criar o texto no ponto clicado e abrir editor inline | P1 |
| Paste | STUB | `context_menus.py:284-294` só duplica seleção; ignora clipboard | Ler `application/x-maestri-element`; URL→Portal; texto→Note | P1 |
| Partituras ▸ (lista + Save layout…) | MISSING | `context_menus.py:296-299` `_save_partitura` é `pass`; grep `partitura`/`api/partituras` em `Sources` e `maestri_core`: zero | Backend (endpoints, storage, CLI) + UI inteira. Também não existe em `Sources/` | P1 |
| Nó terminal: Copy / Paste / Select All | STUB (bug) | `context_menus.py:403-431` usa `f.content.terminal`, mas o atributo real é `.term` (`terminal_node.py:34`); deveria usar `terminal_of()` (`widget.py:165`) | Três ações viram no-op silencioso | P0 |
| Rename | IMPLEMENTED | `context_menus.py:433-437` → `widget.py:286-320` → `window.py:1337-1348` | — | — |
| Edit… | PARTIAL | `context_menus.py:439-442` → `window.py:1621-1651` | Funciona p/ terminal e nota; portal/file tree/desenhos: no-op | P2 |
| Monitor Activity ✓ | STUB | `context_menus.py:339-350,444-450` grava `content.monitor_activity`, inexistente no modelo (`node.py:72` é `monitor_with_ombro`) | Não persiste; sem consumidor | P2 |
| Blur | PARTIAL | `context_menus.py:452-459`, CSS `window.py:715` | Só na sessão; sem marca de checado | P2 |
| Reload | STUB | `context_menus.py:461-466` (`pass`) | `POST /api/terminals/{id}/restart` ou respawn | P1 |
| Unload | STUB | `context_menus.py:468-470` (`pass`) | Encerrar PTY e mostrar placeholder | P2 |
| New Element Defaults ▸ | STUB | `context_menus.py:360` `callback=None` | Faltam submenu, prefs e aplicação | P1 |
| Copy Element | PARTIAL | `context_menus.py:472-479` usa `model_dump_json()` (provavelmente inexistente; modelo usa `to_dict`) e cai em `except` silencioso | Sem mime `application/x-maestri-element`; Paste não lê | P1 |
| Duplicate | IMPLEMENTED | `context_menus.py:481-486` → `window.py:1467-1495` | — | — |
| Lock / Unlock | PARTIAL | `context_menus.py:488-496` alterna `NodeItem.locked` | Não grava `CanvasNode.is_locked` nem restaura (`window.py:1286-1308`): perde ao reabrir; deletar continua permitido | P1 |
| Delete (vermelho) | IMPLEMENTED | `context_menus.py:384-390,498-500` → `window.py:1452-1465` | Desabilitar quando travado | P2 |
| Variantes por tipo (Nota, Portal, File Tree, texto/forma) | MISSING | `NodeContextMenu` sempre monta o menu de terminal (`context_menus.py:314-398`) | Em nota/portal/file tree itens aparecem e não fazem nada; nota deveria ter Rename/Move to…/Raw-Formatted | P1 |
| Edit Terminal / Assign Role / Enable Maestro (§15) | PARTIAL | só no diálogo (`terminal_dialog.py:233,285,334`) | Itens no menu. Efeito de `maestro` no backend UNVERIFIED | P1 |
| Menu da conexão: Delete (§15) | MISSING | grep `Delete Connection`/`delete_connection`: nada; `Target` sem tipo de conexão (`interaction.py:43-52`) | Remover conexão por clique direito | P1 |
| Menu "Partituras" no nó | MISSING | `context_menus.py:395` sem callback | Mesmo backend ausente | P1 |
| Visual do menu (fundo escuro, 8px, destrutivo vermelho) | IMPLEMENTED | `window.py:673-700`, `context_menus.py:29-68` | — | — |

### Top 10 do auditor (parte 1)
1. Copy/Paste/Select All do menu de nó são no-op; Ctrl+Shift+C/V não ligados.
2. Claim do clique direito em CAPTURE (`window.py:1402`) provavelmente bloqueia menu de file tree/Git/nota.
3. Faltam atalhos: ⌘↑/↓ de workspace, duplo-⌘+número, ⌘+dígito p/ terminal, tecla L p/ conexão.
4. Menu de nó é igual p/ todos os tipos; Reload/Unload são `pass`; New Element Defaults e Partituras sem handler.
5. Partituras: sem backend nem UI.
6. Copy Element/Paste não fecham o ciclo; Add ▸ Text não cria no ponto clicado.
7. Lock e Monitor Activity não persistem.
8. "Open in editor" ausente apesar de `preferredIDE`; formas/elipse/losango/marca-texto sem acesso (`DrawingTools.button` órfão).
9. Edit Workspace sem instruções de agente/CLAUDE.md/AGENTS.md/sync; criação sem local/SSH.
10. Anéis de agentes mostram dados falsos; "Connect Nodes" conecta a nó arbitrário; clique direito em conexão sem Delete.

Contagem: IMPLEMENTED 47 · PARTIAL 13 · STUB 8 · MISSING 16 · DIVERGENT 4 · N/A-LINUX 3 · UNVERIFIED 3

---

## Parte 2 — Canvas e Terminal (ref §3, §4; official 03/04)

Auditor leu apenas código (nada executado). Mapeamento de teclas: ⌘ → Ctrl. O alvo `open-maestri-server` (Package.swift:98-104) compila só a whitelist `coreSources` (inclui `LinuxTerminalManager`, `POSIXPtyProcess`, `InterAgentServer`, `WebSocketServer`); o resto de `Sources/` (SwiftTermProvider, TerminalManager, AttentionNotifier, NewTerminalSheet…) NÃO roda no Linux.
Achados gerais: (1) `gtk4-ui/src/ui/canvas_widget.py` é código morto (importa `canvas-cairo/src`, que não existe; ninguém o importa). (2) Os e2e do menu de nó (`tests/e2e/test_sprint7_context_menus_and_pills_e2e.py:83-130`) só conferem troca de atributo/classe CSS — mascaram os stubs abaixo.

### 2.A Canvas

| Feature (ref §) | Status | Evidência | Gap | Prio |
|---|---|---|---|---|
| Pan trackpad (dois dedos) | IMPLEMENTED | `widget.py:511-514,972-980` → `interaction.py:389-398` | Scroll sobre conteúdo de nó vai para o nó; pan só sobre fundo/header | P2 |
| Pan com botão do meio | IMPLEMENTED | `interaction.py:166` | — | — |
| Pan+zoom segurando a roda e rolando | MISSING | só há pan por arrasto | Combinar botão 2 + scroll | P2 |
| Zoom Ctrl+roda | IMPLEMENTED | `interaction.py:391-392` | — | — |
| Zoom por pinça | IMPLEMENTED | `widget.py:517-521,982-984` | Não verificado em hardware | — |
| Pan Space+arrastar | PARTIAL | `interaction.py:166,431-435`; `widget.py:1004-1010` | Só com foco no canvas | P2 |
| Zoom por teclado Ctrl+=/−/0 | IMPLEMENTED | `interaction.py:413-420`; `widget.py:1002-1009` | Menu View só tem Reset Zoom | P2 |
| Limites 0.1–3.0, passo 0.25 | IMPLEMENTED | `constants.py:3-6`; `viewport.py:37-43` | — | — |
| Cápsula de zoom e presets | IMPLEMENTED | `floating_bars.py:638-715` | — | — |
| Minimapa | IMPLEMENTED | `floating_bars.py:476-770` (popover, clique centraliza) | Popover, não painel sempre visível (Swift também usa popover) | P2 |
| Focar nó com `\` | IMPLEMENTED | `interaction.py:436-441`; `viewport.py:46-49` | Só com foco no canvas; `focus_on` nunca amplia além de 1.0 | P2 |
| Origem inicial (9800,8500) + persistência origem/zoom | IMPLEMENTED | `constants.py:3`; `sync.py:138-141` | Persiste (CLAUDE.md diz só runtime) — aceitável | — |
| Grade e fundo | DIVERGENT | `renderer.py:28,77-96` (pontos a cada 40); macOS `canvasGridSpacing=16`; 3 modos via `preferences.canvasBackground` | Espaçamento diferente, cores fixas claras, sem aparência escura | P2 |
| Seleção: clique, Shift, marquee | IMPLEMENTED | `interaction.py:214-240,338`; `widget.py:933-946` | Marquee por interseção, não contenção (comparar com Swift) | — |
| Borda tracejada de seleção | UNVERIFIED | `widget.py:415`; `renderer.py:154` | GTK4 CSS pode renderizar `dashed` contínuo; precisa captura | P2 |
| Ctrl+A (extra) | IMPLEMENTED | `interaction.py:424` | — | — |
| Mover nó pelo header (multi) | IMPLEMENTED | `interaction.py:177-182,275-300` | — | — |
| Redimensionar (8 alças, grade 16) | IMPLEMENTED | `widget.py:375-397`; `resize.py` | — | — |
| Magnetic tile snapping (Ctrl+arrastar) | IMPLEMENTED | `snapping.py:28-47`; `interaction.py:278-283` | Só alinha paredes (limiar 12) | P2 |
| Guias inteligentes (extra) | DIVERGENT | `alignment_guides.py`; `window.py:1122` | Extra além da referência | — |
| Duplicar com Alt+arrastar | IMPLEMENTED | `interaction.py:182,296-312`; `window.py:1467-1495` | Alt+arrastar costuma ser capturado pelo WM; sem alternativa | P2 |
| Duplicar por menu/toolbar | IMPLEMENTED | `context_menus.py:481-486`; `floating_bars.py:157-168` | — | — |
| Deletar (Ctrl+W, Del, menu, toolbar) | IMPLEMENTED | `interaction.py:422-423,443-444`; `window.py:1452-1465` | Ignora `locked`; Ctrl+W só com foco no canvas | P2 |
| Copiar/colar elementos (Ctrl+C/V, Paste do menu) | STUB | `interaction.py:409-452` não tem c/v (v = ferramenta select); `context_menus.py:284-294` não lê clipboard | Mime `application/x-maestri-element` | P1 |
| Desfazer / refazer | MISSING | grep `undo\|redo`: nada | Não está no ref §3, mas esperado | P2 |
| Z-order: trazer p/ frente ao clicar | PARTIAL | `scene.py:102-111`; `interaction.py:222` | `_on_canvas_frame_changed` (`window.py:1437`) não grava `z_index`: ordem perde ao recarregar | P1 |
| Lock / Unlock | PARTIAL | `context_menus.py:488-496`; `interaction.py:177-189` | Não grava `CanvasNode.is_locked` (`node.py:291`); `_add_node_to_canvas` (`window.py:1286-1307`) não lê `isLocked`: perde ao recarregar e ignora lock vindo do macOS | P1 |
| Criar Terminal: ferramenta + arrastar → modal | IMPLEMENTED | `interaction.py:326-358` (mín. 280x180); `drawing.py:77-81` | — | — |
| Criar Note / File Tree / Portal | DIVERGENT | `floating_bars.py:136-147` cria direto no centro | Ref pede ferramenta + arrastar; motor já aceita (`drawing.py:82-84`), falta ligar botões | P1 |
| Text / formas / desenho livre | IMPLEMENTED | `drawing.py`; `interaction.py:326-358` | (Auditoria 1: formas inalcançáveis pela UI) | — |
| Atalhos de ferramenta | PARTIAL | `interaction.py:442-447` (p, t, v) | Tooltips anunciam N e F que não existem | P2 |
| Menu de contexto do canvas | PARTIAL | `context_menus.py:71-299`; `window.py:1399-1416` | Partituras só "Save…" = `pass`; presets duplicam lógica em vez de `_create_node` | P1 |
| Soltar arquivos no canvas → nós de preview | IMPLEMENTED | `widget.py:728-754`; `window.py:1351-1366` | — | — |
| Culling de viewport + hit-test z-ordenado | IMPLEMENTED | `widget.py:859-885`; `scene.py:116-124` | — | — |
| Botão "Connect" da toolbar contextual | PARTIAL | `floating_bars.py:174-190` | `[n.id for n in self.cw.scene.nodes]` itera chaves str do dict → `.id` falha com 1 nó ou nenhum (UNVERIFIED) | P1 |

### 2.B Terminal

| Feature (ref §) | Status | Evidência | Gap | Prio |
|---|---|---|---|---|
| Fluxo de criação | IMPLEMENTED | `drawing.py:78-80`; `window.py:1586-1619` | — | — |
| Presets (Claude Code, Codex, Gemini CLI, OpenCode, Shell) | IMPLEMENTED | `terminal_dialog.py:18-54`; `window.py:1565-1584` | Só grava `command`; `agent_type` fica `generic_shell`; ícone/cor do preset não persistem (`node.py:62-66`) | P1 |
| Nome e ícone no modal | PARTIAL | `terminal_dialog.py:213`; `window.py:1588-1593` | Sem seletor de ícone/cor; nó fica com ícone "terminal" e cor "#000000" | P1 |
| Abas Details/Appearance/Roles | STUB | `terminal_dialog.py:317-341` coleta; `window.py:1588-1606` só lê cwd/name/command | Monitor, Maestro, tema, fonte e role descartados; nomes de tema ("Maestri Dark", "Hacker Matrix"…) nem existem em `terminal_themes.THEMES` | P1 |
| Header: ícone + nome + badge ⌘número | PARTIAL | `widget.py:232-269` | Sem ícone/cor do agente nem badge numérico | P1 |
| Pular entre terminais com ⌘+número (até 9) | MISSING | grep `<Control>[1-9]`/digit: nada | Mostrar números com Ctrl pressionado e focar | P1 |
| Roles (atribuir, injetar CLAUDE.md/AGENTS.md, Settings → Agents) | MISSING | `ensure_terminal` (`api.py:962-983`) não envia `roleName`; `settings_window.py` só General/Terminal/Shortcuts; dropdown (`terminal_dialog.py:281-294`) cosmético | `RoleInjector.swift` está no server mas nada na UI o aciona | P1 |
| Ciclo de vida do PTY (core) | IMPLEMENTED | `window.py:1180-1203,1497-1501,1452-1465`; `InterAgentServer.swift:751-823` | — | — |
| Fallback VTE local (offline) | IMPLEMENTED | `widget.py:115-157`; `window.py:1278-1284` | — | — |
| Shell sobrevive ao reinício da UI | IMPLEMENTED | `terminal_node.py:64-69`; `pty_transport.py:269-279` | — | — |
| Scrollback persistente em disco | MISSING | `ScrollbackStore` só em `SwiftTermProvider`/`TerminalManager` (macOS); histórico é buffer em memória (`POSIXPtyProcess.swift:~51`) | Perde tudo ao reiniciar o server; gravar/restaurar `terminals/{id}.scrollback` | P1 |
| Replay do buffer ao reconectar | IMPLEMENTED | `WebSocketServer.swift:237-251` | Só buffer em memória | — |
| Redimensionar (SIGWINCH) | IMPLEMENTED | `terminal_node.py:71-77`; `LinuxTerminalManager.swift:110-122` | — | — |
| Diretório de trabalho | IMPLEMENTED | `window.py:1189`; `POSIXPtyProcess.swift` | Edit… muda cwd no documento sem reiniciar o PTY | P2 |
| Fonte/tamanho/tema globais ao vivo | IMPLEMENTED | `settings_window.py:108-115`; `window.py:1147-1161`; `terminal_themes.py:149-169` | — | — |
| Catálogo e ids de tema | DIVERGENT | `terminal_themes.py:110-136` (8 temas, ids `dark`/`light`); macOS 16 temas (`maestri-dark`…) | Faltam 8; `themeId` do macOS cai em `dark` | P2 |
| Tema/fonte por nó | PARTIAL | `window.py:1163-1176` lê os campos | Nenhuma UI grava | P1 |
| Prefs `optionAsMeta` / limite de scrollback | STUB | `settings_window.py:117-122` grava; sem consumidor | Aplicar no VTE | P2 |
| Busca no terminal | MISSING | grep `search\|find_`: nada | VTE tem `search_*`; falta barra e atalho | P1 |
| Copiar/colar por teclado (Ctrl+Shift+C/V) | MISSING | grep `copy_clipboard\|paste_clipboard` fora do menu: nada | VTE-GTK4 não traz por padrão (UNVERIFIED) | P0 |
| Menu do terminal: Copy / Paste / Select All | STUB | `context_menus.py:403-431` usa `f.content.terminal` | Atributo não existe (`.term` ou o próprio widget); usar `terminal_of(frame.content)` (`widget.py:165`) | P0 |
| Rename | PARTIAL | `context_menus.py:433-437` → `widget.py:286-320` | Duplo clique no título pode ser engolido pelo gesto de captura (UNVERIFIED); rename não chega ao core (`displayName`) | P1 |
| Edit… | PARTIAL | `window.py:1621-1651`; `terminal_dialog.py:353-467` | Só nome/comando/cwd; macOS tem tema, fonte, ícone, role | P1 |
| Monitor Activity | STUB | `context_menus.py:339,444-450` | Grava `content.monitor_activity`, que `TerminalContent.to_dict` não serializa (`node.py:96-128`); campo real é `monitorWithOmbro`; sem consumidor | P1 |
| Blur | PARTIAL | `context_menus.py:452-459`; CSS `window.py:715-718` | Só na sessão | P2 |
| Reload | STUB | `context_menus.py:461-466` (`pass`) | Falta `POST /api/terminals/{id}/restart` no server | P1 |
| Unload | STUB | `context_menus.py:468-470` (`pass`) | — | P2 |
| New Element Defaults ▸ | STUB | `context_menus.py:360` (`callback=None`) | — | P2 |
| Copy Element | STUB | `context_menus.py:472-479` chama `self.node.model_dump_json()` (inexistente em `CanvasNode`); `except` engole | Nada é copiado | P1 |
| Duplicate | IMPLEMENTED | `context_menus.py:481-486` | — | — |
| Delete / fechar processo | IMPLEMENTED | `context_menus.py:498-500`; `window.py:1452-1465` | — | — |
| Partituras ▸ no menu do nó | STUB | `context_menus.py:395` | Sem endpoints nem CLI | P2 |
| Status ocupado/ocioso | MISSING | grep `activity\|busy\|idle`: sem sinal `terminal-activity` nem indicador | Server tem `TerminalActivityMonitor`; UI não consome | P1 |
| Anéis de quota/atividade | STUB | `floating_bars.py:266-308` (0.68/0.42/0.25 fixos); `set_model_usage` nunca chamado | Dados falsos exibidos como reais | P1 |
| Bell / notificações de atenção | MISSING | grep `bell\|notif\|urgency\|attention`: nada; `AttentionNotifier.swift` fora do server | — | P1 |
| Maximizar / tela cheia do terminal | MISSING | grep `fullscreen\|maximize`: nada | — | P2 |
| Arrastar arquivos para o terminal | IMPLEMENTED | `file_dnd.py:75-96`; `widget.py:145` | — | — |
| Links clicáveis (URLs, OSC 8) | MISSING | grep `hyperlink\|match_add_regex`: nada | — | P1 |
| Foco | IMPLEMENTED | `widget.py:933-946,1004-1010` | — | — |
| Indicador de processo encerrado | PARTIAL | `widget.py:138-143` (só VTE local) | Core: server publica `terminalExited` (`LinuxTerminalManager.swift:50-57`), nenhuma UI consome | P2 |
| Env do PTY (`TERM`, `MAESTRI_TERMINAL_ID`, `MAESTRI_SERVER_PORT`, `MAESTRI_AUTH_TOKEN`) | IMPLEMENTED | `POSIXPtyProcess.swift` | — | — |
| Renderização com canvas em zoom | UNVERIFIED | `widget.py:876` | Nitidez/hit-test do VTE sob escala sem prova visual | P2 |

### Top 10 do auditor (parte 2)
1. Copy/Paste/Select All do menu do terminal são no-ops; sem Ctrl+Shift+C/V.
2. Sem busca no terminal e sem links clicáveis.
3. Scrollback não persiste em disco no Linux.
4. Reload, Unload, Copy Element, New Element Defaults e Partituras são stubs; falta endpoint `restart`.
5. Roles ausentes (atribuição, injeção, Settings → Agents, reatribuição); dropdown do modal é decorativo.
6. Modal "New Terminal" descarta Monitor, Maestro, tema, fonte e role; header sem ícone/cor do agente; preset só grava `command`.
7. Sem pulo ⌘+número nem badges numéricos nos headers.
8. Lock e z-order só em runtime; Delete ignora lock.
9. Sem status ocupado/ocioso, bell e indicador de saída; anéis de quota com dados falsos.
10. Note/File Tree/Portal pela toolbar não usam "ferramenta + arrastar"; sem Ctrl+C/V de nós; botão Connect com bug provável.

Contagem (77 linhas): IMPLEMENTED 36 · PARTIAL 13 · MISSING 11 · STUB 11 · DIVERGENT 4 · UNVERIFIED 2 · N/A-LINUX 0

---

## Parte 3 — Notes, Connections, File Tree, Maestro (ref §5-8; official 05-08)

Leitura estática (nada executado); conclusões que dependem de runtime estão marcadas.
Abreviações: `cc/` = `canvas-cairo/maestri_canvas/`, `ui/` = `gtk4-ui/src/ui/`, `S/` = `Sources/`, `IAS` = `S/InterAgent/InterAgentServer.swift`.

**Dois achados transversais que explicam várias linhas:**
- **Id do nó ≠ id do terminal.** Terminais criados no GTK (`window.py` `_create_node`, `create_terminal_preset`, `on_create`) usam `CanvasNode(id=uuid1)` com `TerminalContent()` gerando OUTRO `terminal_id` (`maestri_core/models/node.py:84-94`). A UI conecta com ids de nó (`window.py:1521`, `client.create_connection(a,b)`); o PTY e `MAESTRI_TERMINAL_ID` usam `terminal_id` (`window.py:1177-1189`, `S/Terminal/POSIXPtyProcess.swift:105`); o servidor busca conexões por terminal id (`AskHandler.swift:23`) e o nó Maestro por node id (`MaestroHandlers.swift:54`). No macOS os dois são iguais.
- **Estado de runtime de conexões só existe em memória** (`ConnectionManager.swift:38`) e só é reconstruído por código macOS (`WorkspaceCanvasView.swift:353`).

### 3.1 Notes (§5)

| Feature | Status | Evidência | Gap | Prio |
|---|---|---|---|---|
| Ferramenta Note (desenhar retângulo, cria `.md`) | PARTIAL | `ui/floating_bars.py:131` → `_create_node("stickyNote")`; `window.py:1677-1718` | Sem modo de desenho: cria 420x300 no centro; `.md` só nasce no 1º save; tooltip "(N)" promete atalho inexistente (`interaction.py:409-446` só tem p/t/v) | P2 |
| `.md` real em pasta gerenciada | IMPLEMENTED | `cc/note_storage.py` → `PUT /api/notes` (`IAS:867`) | — | — |
| Raw / Formatted (persistido) | IMPLEMENTED | `cc/sticky_note.py:130-136,296-305` | — | — |
| Header: nome + menu ⋯ (Rename / Move to… / Delete) | IMPLEMENTED | `sticky_note.py:145-162` | Extras: Larger/Smaller text | — |
| Render Markdown (h1-h3, negrito/itálico/tachado, código, listas, citação, hr, links) | IMPLEMENTED | `cc/markdown_spans.py:3`; `sticky_note.py:203-222,307-334` | — | — |
| Tabelas | DIVERGENT | `markdown_spans.py:110-119` | Vira texto monoespaçado com `├─┼─┤`, não grade | P2 |
| Imagens inline (colar; preview) | IMPLEMENTED (não rodado) | `sticky_note.py:341-360,668-696` | Só Ctrl+V no Raw; http/https não carregam (intencional) | P2 |
| Nome automático da 1ª linha | IMPLEMENTED | `sticky_note.py:431-441` | — | — |
| Rename por duplo clique no header | IMPLEMENTED | `sticky_note.py:444-448`; `widget.py:925` | — | — |
| Rename por botão direito | STUB | `ui/context_menus.py:433-437` → `widget.py:288` | Nota usa `header_title`; `title.get_parent()` é `None` → retorna sem fazer nada. Chamar `content.begin_rename()` | P1 |
| Limpar nome custom e voltar ao automático | MISSING | `sticky_note.py:450-461`: `has_custom_name` nunca volta a False | — | P2 |
| Move to… (pasta custom) | IMPLEMENTED | `sticky_note.py:513-557` | — | — |
| Arrastar `.md/.markdown/.txt` p/ canvas | IMPLEMENTED | `window.py:1350-1368`; `cc/file_ops.py:130-138` | — | — |
| Soltar arquivo sobre nota existente | STUB | `sticky_note.py:584-589` emite `file-dropped`; `widget.py:574-576` não conecta | `adopt_file` (`:559`) nunca é chamado | P2 |
| Excluir nota apaga nota e arquivo | DIVERGENT | Teclas/X: `window.py:1456-1470`, `widget.py:610-627` não apagam o arquivo; menu ⋯ `sticky_note.py:591-626` apaga sempre | Teclas/X deixam `.md` órfão; ⋯ apaga também path custom (a ref proíbe). Unificar: gerenciado apaga, custom só desvincula | P1 |
| Sync ao vivo com o arquivo (agente escreve via CLI) | PARTIAL | `sticky_note.py:628-666` monitora só `custom_path`/`watch_path`; `widget.py:574` não passa `watch_path` | Nota gerenciada nunca monitorada: `omaestri note write` não aparece até recarregar e a próxima digitação SOBRESCREVE. Passar `watch_path={notes_dir}/{name}.md` | P0 |
| Cores (7 presets + custom), tamanho de fonte | IMPLEMENTED | `sticky_note.py:225-243,388-420`; `cc/note_colors.py:14` | — | — |
| Lock | STUB | `ui/context_menus.py:488-497` só `NodeItem.locked` em memória | Não grava `CanvasNode.is_locked` (`node.py:291,334`) nem aplica ao carregar; ⋯ da nota sem Lock | P1 |
| Stacks / fichários | N/A | não consta em `docs/reference` | — | — |
| CLI `note read/write/edit` | PARTIAL | `S/InterAgent/Handlers/NoteHandler.swift:127-160` | Sem checagem de conexão; busca por nome em TODOS os workspaces com `contains` fuzzy; notas de path custom nunca achadas | P0 |
| `note create` (cria nó e liga ao terminal) | STUB | `NoteHandler.swift:98-115` | Só grava arquivo: sem nó, conexão nem `nodeAdded` | P1 |
| `list` mostra nome da nota | DIVERGENT | `S/InterAgent/Handlers/ListHandler.swift:76` usa `NoteRegistry.name(forNodeId:)`, `register` só chamado por código macOS | No Linux toda nota aparece como "Note" | P1 |
| Note chaining (Note↔Note) | PARTIAL | `cc/connection_rules.py:17`; `IAS:1036+`; `ListHandler.swift` ignora `noteToNote` | Desenha e persiste, mas o servidor não percorre a cadeia | P1 |
| Menu de nó (variante Note) | PARTIAL | `ui/context_menus.py:472-486` | "Copy Element" usa `model_dump_json()` inexistente (no-op) | P1 |

### 3.2 Connections (§6)

| Feature | Status | Evidência | Gap | Prio |
|---|---|---|---|---|
| Criar por arrasto (pin → pin ou corpo do alvo) | IMPLEMENTED | `cc/interaction.py:190-197,262,316-320`; `cc/scene.py`; `widget.py:826` | — | — |
| Ferramenta Connection (linha segue o cursor; clicar no 2º nó) | DIVERGENT | `ui/floating_bars.py:171-190` | Botão conecta na hora o selecionado ao primeiro outro nó (ou cria terminal); sem modo de rastreio | P1 |
| Atalho `L` | MISSING | `interaction.py:409-446` | Buscado: `"l"`, `begin_connect`, `connect_mode` | P1 |
| Corda com física (Verlet) | IMPLEMENTED | `cc/rope.py:33-162`; `renderer.py:98-113` | — | — |
| Persistir `ropePoints` | MISSING | `rope.on_sleep` nunca atribuído; `client.update_connection` (`api.py:1100`) sem chamadores; servidor grava zeros (`IAS:1050-1063`) | Cordas re-simulam ao abrir | P2 |
| Âncoras laterais | PARTIAL | `scene.py` `anchors()` usa `facing_sides` (só left/right); spec sprint7 T4.3 pede `nearest_side` | — | P2 |
| Várias conexões por nó | IMPLEMENTED | `scene.py` `can_connect` | — | — |
| Regras de pares | IMPLEMENTED | `cc/connection_rules.py:13-19`; `window.py:1507-1518` | — | — |
| Feedback de par recusado | IMPLEMENTED (extra) | `widget.py:694-` | — | — |
| T↔T: ask/check/list com escopo por conexão | PARTIAL (estático) | `AskHandler.swift:23`; `ConnectionManager.swift:45-67` | Conexões por node id, sessões por terminal id (transversal); `connectTerminals` cria `ActiveConnection` com `UUID()` novo (`:50`) ≠ `connId` persistido por `IAS:1036`. Se não casar, `ask` responde "agent not found" | P0 |
| Reconstruir conexões ao reiniciar servidor/workspace | MISSING | `ConnectionManager.swift:195` `restoreConnections`; único chamador `S/WorkspaceCanvas/WorkspaceCanvasView.swift:353` (macOS); `S/Server/main.swift` e GTK não chamam | Após reiniciar o servidor, todas as conexões somem do runtime. Chamar na carga/troca de workspace (`/api/workspace`) | P0 |
| Remover conexão (UI) | MISSING | buscado: `delete_connection`, `Delete Connection`, `hit_rope`, `connection_at`; `window.py` `_on_canvas_right_click` só trata Background/nós | Hit-test na corda, menu "Delete Connection", `client.delete_connection` (`api.py:1119`). Servidor `IAS:1209` faz `disconnect(connId)` com id que não bate → não revoga a permissão | P1 |
| Excluir nó limpa conexões no servidor | PARTIAL | `window.py:1450-1466` | `ConnectionManager` não é avisado: permissões penduradas, `list` mostra "unknown" | P1 |
| SkillInjector escreve skills em `~/.claude/skills` | MISSING | `S/Connection/SkillInjector.swift:15-18` `inject()` é no-op; `installSkillsIfNeeded` só em `S/App/AppDelegate.swift:35` (macOS); `main.swift` do servidor e Python não chamam; `ls ~/.claude/skills`: nenhuma `open-maestri*` | Agentes iniciados no Linux não aprendem `omaestri`. Chamar `installSkillsIfNeeded()` no `runServer()` ou equivalente em Python | P0 |
| CLI `omaestri` no PATH / `MAESTRI_CLI` | PARTIAL | `scripts/install-desktop.sh:24-27`; `POSIXPtyProcess.swift:105-112` não define `MAESTRI_CLI` | A skill usa `$MAESTRI_CLI` como fallback; a variável não existe | P2 |
| T↔Note: agente lê/edita a nota | PARTIAL | `IAS:1128-1140`; CLI: ver Notes | Sem escopo; sem sync ao vivo | P0 |
| T↔Portal | IMPLEMENTED (conexão) | `connection_rules.py:15` | Comandos `portal` fora do escopo | — |
| Portal↔Portal compartilha sessão | IMPLEMENTED | `widget.py:662` | — | — |
| "Receptor deve ficar não selecionado" | N/A | `AskHandler.swift:55-95` detecta ociosidade pelo PTY | Não necessário | — |
| Estado visual da corda (idle/communicating/glow) | MISSING | `renderer.py:98-113` só tracejado `pending` | Sem eventos de status do servidor para a GUI | P2 |

### 3.3 File Tree (§7)

| Feature | Status | Evidência | Gap | Prio |
|---|---|---|---|---|
| Ferramenta File Tree (arrastar; abre no working dir do workspace) | DIVERGENT | `floating_bars.py:137-139`; `window.py:1713` `FileTreeContent(root_path=os.path.expanduser("~"))` | Cria direto sem desenhar; raiz é `~`, não `payload.working_directory` | P1 |
| Várias instâncias com estado próprio | PARTIAL | `window.py:1334-1346` persiste `rootPath`/`viewMode` | Pastas expandidas e histórico não persistem | P2 |
| List view hierárquica (lazy, monitorada) | IMPLEMENTED | `cc/file_tree_node.py:1-22,81-170` | `ui/filetree.py` (`FileTreeWidget`) é código morto | — |
| Back / Forward (+Up) | IMPLEMENTED | `file_tree_node.py:231-248` | — | — |
| Collapse All | MISSING | só docstring em `file_tree_node.py` | Botão ausente | P2 |
| Icon grid com thumbnails / Quick Look | IMPLEMENTED | `cc/thumbnails.py`, `cc/quick_look.py`; `file_tree_node.py:497-523,871-910` | — | — |
| Trocar a raiz pela toolbar | PARTIAL | `file_tree_node.py:220-248,581-588` | Na List, pasta só expande; sem "definir como raiz" nem seletor | P2 |
| Menu de contexto (criar/renomear/mover/excluir) | IMPLEMENTED | `file_tree_node.py:594-668,682-760,837-870` | Excluir vai p/ lixeira | — |
| Arrastar para terminal | IMPLEMENTED (não rodado) | `cc/file_dnd.py:76`; `terminal_node.py:54` | Cola path com quoting | — |
| Arrastar para canvas (preview) | IMPLEMENTED | `window.py:1350-1368`; `file_ops.py:130` | Imagem/PDF/vídeo viram Portal `file://` | P2 |
| Indicador de branch | IMPLEMENTED | `cc/git_menu.py:26-75,207-222` | — | — |
| Menu Git (Commit/Pull/Push/Checkout/New Branch/Merge/Fetch/Stash) | IMPLEMENTED | `git_menu.py:163-184`; `maestri_core/gitops.py:212-326` | Extras: Stash Pop, Show Changes, Refresh | — |
| Badges de status git | IMPLEMENTED | `cc/git_badges.py`; `file_tree_node.py:390-427` | — | — |
| Diff view | IMPLEMENTED | `cc/diff_view.py`; `file_tree_node.py:359-368` | — | — |
| Selecionar bloco no diff → perguntar ao agente | DIVERGENT | `diff_view.py:63-110`; `ui/ask_agent.py` | Botão no header (não flutua sobre a seleção); texto vai ao terminal ativo; File Tree não é conectável (`connection_rules.py:26`) | P2 |

### 3.4 Maestro (§8)

| Feature | Status | Evidência | Gap | Prio |
|---|---|---|---|---|
| Checkbox Maestro no Novo Terminal | STUB | `ui/terminal_dialog.py:233-235,334` | `on_create` (`window.py:1583-1606`) ignora `data["maestro"]`; `is_manager` nunca setado (grep em `gtk4-ui`: vazio) | P0 |
| Edit Terminal → Details → Maestro | MISSING | `terminal_dialog.py:377-440`; `window.py:1630-1640` | Sem toggle de Maestro nem de role | P0 |
| `isManager` serializado em `workspace.json` | IMPLEMENTED | `maestri_core/models/node.py:71,109,149` | UI nunca grava True | — |
| Skill de gerente (`open-maestri-manager`) | MISSING | `SkillInjector.swift` define; nada instala no Linux | — | P0 |
| `omaestri recruit` | PARTIAL | `S/InterAgent/Handlers/MaestroHandlers.swift:26-124` | Exige `isManager` do `workspace.json` em disco por node id (`:52-59`); cria só PTY e conexão em memória e posta `.maestroRecruited` (`:109`) que só `WorkspaceCanvasView.swift:361` (macOS) observa → no Linux: sem nó no canvas, sem conexão persistida, sem `nodeAdded` | P0 |
| Layout automático dos recrutas | MISSING | comentário `MaestroHandlers.swift:126` (macOS); grep `recruit` em `*.py`: vazio | — | P1 |
| Conectar recruta a notas (`connect`) | PARTIAL | `MaestroHandlers.swift:154-169` | Só casa terminais por `command`/`roleName`, não notas; não persiste nem emite evento | P1 |
| Reatribuir role mantendo posição/nome/conexões | PARTIAL | `MaestroHandlers.swift:227-252` | Grava arquivos do role; "takes effect on next restart"; não reinicia o agente | P1 |
| Dismiss | PARTIAL | `MaestroHandlers.swift:131-149` | Remove PTY e conexão em memória; nó continua no canvas (sem `nodeRemoved`) | P1 |
| Roles ao criar terminal (dropdown) | STUB | `terminal_dialog.py:289,327,337` (`ROLES` fixa); `ensure_terminal` sem `role` | `RoleInjector` nunca acionado pela UI | P1 |
| Biblioteca de roles (Settings → Agents) | MISSING | grep `role` em `settings_window.py`/`preferences.py`: vazio | `omaestri role create/edit` grava `prefs.rolePresets` mas a UI não lista nem edita | P1 |
| Presets de agente / time misto (`--preset`) | PARTIAL | `MaestroHandlers.swift:62-71,259-274`; `terminal_dialog.PRESETS` | UI não lê `prefs.agentPresets` | P2 |
| `list` no recruta mostra nome e role | PARTIAL | `ListHandler.swift:21-24` | "You:" só nome, sem role | P2 |
| Partituras (times salvos) | STUB | `ui/context_menus.py:296-299` `pass` | Sem lista, inserir, endpoints (grep `partitura`: vazio) nem CLI | P1 |
| Maestro não pode excluir roles | IMPLEMENTED | `MaestroHandlers.swift:178-256` | — | — |

### Top 10 do auditor (parte 3)
1. Maestro inoperante de ponta a ponta (checkbox descartado, sem toggle no Edit, skill de gerente não instalada, `recruit` sem nó).
2. Skills `open-maestri*` nunca são instaladas no Linux (`SkillInjector.inject` no-op; `installSkillsIfNeeded` só no macOS).
3. `ask`/`list` dependem de ids que não casam (node id da UI ≠ terminal id do PTY; `ActiveConnection` com `UUID()` próprio). Validar rodando em Xvfb.
4. Conexões persistidas não voltam após reiniciar o servidor (`restoreConnections` só macOS).
5. Sem como remover conexão (sem hit-test, menu nem revogação de permissão no servidor, nem ao excluir o nó).
6. Nota gerenciada não sincroniza ao vivo com o CLI (`watch_path` não passado; próximo save sobrescreve).
7. CLI de notas sem escopo, `list` mostra sempre "Note", path custom não achado, `note create` não cria nó, chaining não percorrido.
8. Delete de nota inconsistente (Del/⌘W/X deixam `.md` órfão; ⋯ apaga path custom); Rename pelo botão direito é no-op.
9. Lock não persiste; Copy Element é no-op.
10. Botão Connection conecta nós arbitrários, sem `L`, `ropePoints` não persistem; File Tree com raiz `~` e sem Collapse All.

Contagem (75 linhas): IMPLEMENTED 30 · PARTIAL 18 · MISSING 12 · STUB 7 · DIVERGENT 6 · N/A 2 · UNVERIFIED 0

---

## Parte 4 — Floors, Portal, Routines, SSH e contrato CLI (ref §9-12; official 09-12; `docs/reference/maestri-cli.md`)

Somente leitura. Contexto: `Package.swift` compila no `OpenMaestriCore` só uma whitelist; `Sources/SSH`, `Sources/Portal`, `RoutineScheduler.swift`, `SwiftTermProvider` e `AppDelegate` ficam de fora. `open-maestri-server` (`Sources/Server/main.swift`) só chama `InterAgentServer.start()`: não injeta `portalsService`, não instala skills e não restaura conexões. Os PTYs vivem no servidor Swift: se ele cair, todos os terminais caem. O arquivo `maestri-cli.md` cobre só `list/check/ask/note read|write|edit`; para o resto do CLI o auditor usou as skills reais em `~/.claude/skills/maestri*/SKILL.md` (linhas marcadas "(skill)").

### 4.1 Floors (§9)

| Feature | Status | Evidência | Gap | Prio |
|---|---|---|---|---|
| F1 Botão Floor no canto inferior direito | IMPLEMENTED | `floors.py:748-800,915-925`; `floating_bars.py:581-607` | — | — |
| F2 Vista 3D de floors | DIVERGENT | sem `3d/perspective/isometric/overview`; popover em lista (`floors.py:786-790`) | Sem vista espacial; a lista cumpre a função | P2 |
| F3 Diálogo New Floor (nome, branch nova/existente, base) | IMPLEMENTED | `floors.py:415-562`; `InterAgentServer.swift:1652` → `FloorManager.createFloor` (`git worktree add`) | Strings em português (F19) | — |
| F4 "Clone Ground layout" | MISSING | grep vazio; diálogo só tem Nome/Tipo/Branch/Base | Depende de F5 | P1 |
| F5 Floor como ambiente isolado (canvas por floor) | PARTIAL | `floors.py:295-312`: `switch_to` só muda cwd e env de novos terminais; nós sem `floorId`, nada filtra | Trocar de floor não troca o canvas | P1 |
| F6 APFS / clone copy-on-write | N/A-LINUX | `FloorManager.swift:6` usa git worktree | — | — |
| F7 Local e cópia do estado do Ground | DIVERGENT | `FloorManager.swift:20` grava em `<root>/.open-maestri/floors/<slug>`; worktree não copia não rastreados/dependências | Opcional | P2 |
| F8 Requisito de repositório git | IMPLEMENTED | `FloorManager.swift:94-98`; `floors.py:240` | — | — |
| F9 Painel com arquivos alterados e diff stats | PARTIAL | `floors.py:837-870` só badge `+ins −del` e ponto dirty; `/api/floors/:id/status` devolve `files` (`InterAgentServer.swift:1767`) sem UI | Lista de arquivos | P1 |
| F10 Detecção de conflito de merge | IMPLEMENTED | `FloorManager.swift:264-312`; `floors.py:650-668` | Conflito não resolvível na UI (igual macOS) | — |
| F11 Land (preview, Merge, mesma branch/outra) | IMPLEMENTED | `floors.py:563-710`; `InterAgentServer.swift:1725-1765`; `FloorManager.land:314` | Floor não é removido após land (skill diz que sim) | P2 |
| F12 Delete (manter/apagar branch; limpa pasta) | IMPLEMENTED | `floors.py:712-740`; `FloorManager.removeFloor:122`; `InterAgentServer.swift:1826` | — | — |
| F13 Renomear floor (clique direito) | MISSING | sem `rename` em `floors.py`; servidor tem `PATCH /api/floors/:id` (`:1789`), cliente `api.py:1176`, sem UI | Menu de contexto na linha | P1 |
| F14 "Configure Hooks…" (Setup/Run/Teardown, Auto-run) | MISSING | `hook` na UI só em docstring; botão sem clique direito | UI de hooks inexistente | P1 |
| F15 Execução dos hooks | STUB | servidor guarda `hooks` (`:1789-1810`) mas nunca executa; `HooksManager` só chamado de `FloorOverviewView.swift:152,166` (macOS) | `runSetupHooks` no `POST /api/floors`, `runTeardownHooks` no `DELETE`, endpoint de run | P1 |
| F16 Ícone ⚡, play e "Run All" | MISSING | grep `bolt` só ícones genéricos | Falta tudo | P1 |
| F17 Variáveis `$MAESTRI_FLOOR_NAME/BRANCH_NAME/FLOOR_PATH/ROOT_PATH/PROJECT_NAME` | PARTIAL | `floors.py:157-170`, `FloorManager.hookEnvironment:58`; `window.py:1301` só passa env ao VTE local; core-backed (padrão, `window.py:1281-1283`) não recebe: `POSIXPtyProcess.swift:103-112` | Passar env no `POST /api/terminals` | P1 |
| F18 Persistência, eventos ao vivo, proteção contra snapshot velho | IMPLEMENTED | `floors.py:257-266,314-345`; `InterAgentServer.swift:1652-1860`, R1 `:~730` | — | — |
| F19 Idioma da UI | DIVERGENT | `floors.py:420,563,725` em português; resto do app e macOS em inglês | Padronizar | P2 |

### 4.2 Portal (§10)

| Feature | Status | Evidência | Gap | Prio |
|---|---|---|---|---|
| PO1 Criar por toolbar/`P`, pedindo URL | PARTIAL | `floating_bars.py:47,140`; `interaction.py:442`; `window.py:1706` | Nasce com `https://google.com` fixo, sem pedir URL | P2 |
| PO2 Navegador embutido (WebKitGTK) com barra de endereço, back/forward/reload/stop | IMPLEMENTED | `portal_node.py:55-110`; `portal_model.py:62` | — | — |
| PO3 Preview de arquivo local | IMPLEMENTED | `portal_model.py`; `window.py:1365` | — | — |
| PO4 Storage isolado por portal | IMPLEMENTED | `portal_node.py:_session` (NetworkSession efêmera) | macOS também `nonPersistent` | — |
| PO5 Portal↔portal compartilha sessão | IMPLEMENTED | `widget.py:662-692`; `scene.py:16` | Recarrega a página ao trocar de sessão | — |
| PO6 Links `target=_blank` / `window.open` | STUB | `widget.py:462,584` emite `portal-popup`; nenhum handler em `window.py` | Clique engolido | P1 |
| PO7 Menu de contexto do portal (Reload etc.) | STUB | `context_menus.py:461-466` `_on_reload` só terminal | Ligar `web.reload()` | P2 |
| PO8 Estado persistido (URL, status, chromeHidden, storageScope) | IMPLEMENTED | `portal_model.py`; `window.py:1309-1316` | — | — |
| PO9 Conexão terminal↔portal pela UI | IMPLEMENTED | `scene.py:16`; `window.py:1525`; `InterAgentServer.swift:1146-1149` | Só em memória no servidor (C13) | — |
| PO10 Backend do CLI de portal (`PlatformHooks.portalsService`) | MISSING | `PlatformHooks.swift:14,21`: `portals` faz `fatalError` se não injetado; só `App/AppDelegate.swift:20` (macOS) injeta; `Sources/Server/main.swift` e `InterAgentServer.start()` (`:158-165`) injetam só terminais; 34 usos em `Handlers/` | Qualquer `omaestri portal …` derruba o `open-maestri-server`; `list` também derruba com conexão terminal↔portal (`ListHandler.swift:~77`). Falta ponte servidor→GTK (`PortalManaging` Linux) | P0 |
| PO11 Agente cria portal (`portal create URL`) | MISSING | `PortalHandler.swift:84` posta `.portalCreatedViaCLI`, só `WorkspaceCanvasView.swift:364` (macOS) consome; antes, `ensureWebView` (`:81`) crasha | Portal não aparece no canvas Linux | P0 |
| PO12 Navegação por CLI (`navigate/back/forward/reload/info`) | STUB | primitivas Python em `portal_node.py:228-262` sem chamador | Depende do PO10 | P0 |
| PO13 Leitura por CLI (`snapshot/text/html/screenshot/evaluate`) | STUB | `portal_node.py:228-262`; `screenshot()` devolve caminho, Swift espera base64 | Depende do PO10; alinhar formato | P0 |
| PO14 Interação por CLI (`click/fill/type/key/hover/scroll/drag/wait/select/check/focus`) e console | MISSING | Swift monta JS via `evaluate` (`PortalHandler.swift:394`); Python sem captura de console | Depende do PO10 | P0 |
| PO15 Resolução do portal por nome | DIVERGENT | `PortalHandler.swift:544-549` `resolvePortalId` ignora `name` e devolve o primeiro conectado | Com vários portais, vai sempre ao primeiro | P1 |
| PO16 Devices Android (`portal devices`, `--simulator`, `swipe`, `launch`…) (skill) | MISSING | grep `android\|adb\|emulator\|simulator`: só `set_device_scale` | Fora do reference-index | P2 |
| PO17 `portal close`, `upload`, `create --size` (skill) | MISSING | não em `PortalHandler.swift:32-60` | Depois do PO10 | P2 |

### 4.3 Routines (§11)

| Feature | Status | Evidência | Gap | Prio |
|---|---|---|---|---|
| R1 File → Routines → New Routine | IMPLEMENTED | `window.py:1060-1063,151`; `routines_window.py:install_routines`, Ctrl+Shift+R | — | — |
| R2-R4 Editor (nome, prompt, intervalo, target) | IMPLEMENTED | `routines_window.py:RoutineEditor`; mín. 60 s (`routines.py:MIN_INTERVAL_SECONDS`) | Só lista terminais do workspace aberto (R14) | — |
| R5 "Começa a rodar imediatamente" | DIVERGENT | `routines.py:set_routines` `next_due = now + interval` | Swift também sem disparo imediato | P2 |
| R6 Encadeamento `&&`, prompts sequenciais | IMPLEMENTED | `routines.py:parse_prompts` (46-57); `RoutineScheduler._advance`; `RoutineModels.swift:30` | — | — |
| R7 "Próximo só dispara após o anterior completar" | DIVERGENT | `TerminalBridge.is_idle` = 2 s sem saída (`IDLE_QUIET_SECONDS`) + 3 s de graça (`IDLE_GRACE_SECONDS`) | Agente que "pensa" em silêncio >5 s recebe o próximo prompt antes da hora; macOS usa `terminalBecameIdle` | P1 |
| R8-R10 Pausar/retomar, editar, excluir (com confirmação) | IMPLEMENTED | `PATCH`/`DELETE /api/routines/:id` (`InterAgentServer.swift:1893,1973`) | — | — |
| R11 Indicador ao vivo + badge de timer | IMPLEMENTED | `routines_window.py:CSS`, `apply_routine_badges` → `widget.py:600` | — | — |
| R12 Run now | IMPLEMENTED | `scheduler.run_now`; Enter separado após 120 ms (`ENTER_DELAY_MS`) | — | — |
| R13 `POST /api/routines/:id/run-now` | DIVERGENT | `InterAgentServer.swift:1954-1965` escreve todos os prompts de uma vez, sem idle nem Enter separado; GUI não usa | TUIs tratam como paste | P2 |
| R14 Routine com target em outro workspace | DIVERGENT | rotinas globais (`routines.json`); `TerminalBridge` só resolve terminais do `window.document` atual; alvo não encontrado → `send` False → `_fail` pausa e persiste `isActive=false` (`routines.py:_fail`, `routines_window.py:_on_event`) — lido, não executado | Trocar de workspace pode pausar rotinas de outros workspaces silenciosamente | P1 |
| R15 Compatibilidade de `routines.json` com macOS | IMPLEMENTED | `routines.py:dump_container/load_container`; `RoutineModels.swift:46-64` | — | — |
| R16 CLI `routine list/show/create/edit/enable/disable/run/delete` (skill) | MISSING | `CLIRouter.swift:14-35` sem "routine"; REST existe (`InterAgentServer.swift:1863-1990`) | Agentes não conseguem agendar | P1 |
| R17 Agendas extras (`--daily/--weekly/--once`, `--count`, `--until`, `--pre-run`, `--reminder`) (skill) | MISSING | `Routine` só tem `intervalSeconds` | Fora do reference-index | P2 |

### 4.4 Remote SSH (§12)

| Feature | Status | Evidência | Gap | Prio |
|---|---|---|---|---|
| S1 Settings > Remote SSH > "Enable SSH workspaces" + Tunnel Port (7433) | MISSING | `settings_window.py` só General/Terminal/Shortcuts | Falta UI | P1 |
| S2 Config por conexão (Host, User, Port 22, Script Path, Add to PATH) | MISSING | `terminal_dialog.py`/`workspace_dialogs.py` sem campos SSH | Falta UI | P1 |
| S3 Instalar script curl remoto e túnel reverso | MISSING | `SSHManager.swift`, `SSHTunnelService.swift` fora do `coreSources` | Reimplementar com `ssh -R` | P1 |
| S4 Terminal remoto via SSH | MISSING | `SSHTerminalProvider.swift` fora da whitelist; `LinuxTerminalManager` só PTY local | Falta provider | P1 |
| S5 Preservar campos SSH (round-trip macOS) | IMPLEMENTED | `preferences.py:40,43,63,86`; `models/node.py:82-160` (`_extra`) | Dados sobrevivem mas não são editáveis | — |

### 4.5 Contrato CLI e skills

| Feature | Status | Evidência | Gap | Prio |
|---|---|---|---|---|
| C1 Nome do binário/PATH (doc usa `maestri`; port só `omaestri`) | DIVERGENT | `scripts/install-desktop.sh:25-29` symlinka só `omaestri` e só se compilado; `MAESTRI_CLI` só em `SwiftTermProvider.swift:133` (macOS), não em `POSIXPtyProcess.swift:103-112` | Sem alias `maestri` nem `MAESTRI_CLI` nos terminais Linux | P1 |
| C2 Transporte `POST /cli`, `X-Terminal-ID`, env | IMPLEMENTED | `Sources/CLI/Transport.swift`; `POSIXPtyProcess.swift:103-112`; `InterAgentServer.swift:651-668` | Unix socket confiado (0600); aceitável | — |
| C3 `list` | PARTIAL | `ListHandler.swift` | Crasha com portal conectado (PO10); notas aparecem "Note" (`NoteRegistry.register` só no macOS); acrescenta seção "You:" fora da doc; depende de C13 | P0 |
| C4 `check` | PARTIAL | `CheckHandler.swift:22-34` (escopo por conexão, 20 linhas, remove ANSI; ring buffer por `\n`) | Com TUIs pode sair truncado/ruidoso (não testado); depende de C13 | P1 |
| C5 `ask` | DIVERGENT | `AskHandler.swift:9,73,122`: timeout fixo 30 s, snapshot; todo terminal Linux é `agentType: "generic_shell"` (`InterAgentServer.swift:783`) → heurística de prompt (`hasPromptReturned`, sufixo `$ % > ❯`), não `terminalBecameIdle`; texto + `"\r"` num único write (`AskHandler.swift:46-47`; o port documenta que TUIs tratam como paste: `ask_agent.py:36`, `routines_window.py:35`) | Delegar a Claude/Codex pode nunca submeter e retornar após 30 s com saída parcial; a skill espera até 20 min | P0 |
| C6 `ask --batch`, `--raw`, `notify` (skill) | MISSING | `CLIRouter.swift:14-35`; `AskHandler.swift:16-19` | — | P2 |
| C7 `note read "Name" [offset] [limit]` com `[N lines total]` | DIVERGENT | `NoteHandler.swift:36-38` devolve texto cru; `readWithLineRange` nunca chamado | Formato do contrato ausente | P1 |
| C8-C9 `note write` / `note edit` → `OK` | IMPLEMENTED | `NoteHandler.swift:46-83` | Refresh ao vivo no canvas não verificado (`.noteFileDidChange` é in-process; GTK é outro processo) | — |
| C10 `note create` | STUB | `NoteHandler.swift:~95-120` só grava `.md` | Nota não aparece no canvas; faltam `--name/--stack/--color` | P1 |
| C11 `note delete`, `stack/unstack`, `arrange` (skill) | MISSING | — | — | P2 |
| C12 Escopo de permissão por `X-Terminal-ID` | PARTIAL | escopado: `AskHandler:23-24`, `CheckHandler:22`, `ListHandler`, `dismiss`; NÃO escopado: `note read/write/edit` (`NoteHandler.swift:128-170` varre todos os workspaces, substring) e `connect` (`MaestroHandlers.swift:154-170`); `recruit` olha o workspace ativo do app-state, não o do chamador | Vazamento entre workspaces | P1 |
| C13 Restaurar conexões do `workspace.json` ao iniciar o servidor | MISSING | `restoreConnections` só em `WorkspaceCanvasView.swift:353` (macOS); no Linux só `POST /api/connections` alimenta o gerenciador (`InterAgentServer.swift:1138-1153`, em memória) | Após reiniciar, `list/ask/check/note/portal` veem "No connections" até religar | P0 |
| C14 `recruit/dismiss/connect` (Maestro) | PARTIAL | `MaestroHandlers.swift:26-170`; nó só chega ao canvas via `.maestroRecruited` (`:110`, só macOS); flags `--floor/--workspace/--band/--dir/--account/--icon/--color/--replace` não tratadas | Recruta existe invisível | P1 |
| C15 `role` e `preset` | PARTIAL | `MaestroHandlers.swift:~176-275`; `Help.swift:16-17` | Faltam `preset show/create/set/delete` e `role --scope` | P2 |
| C16 `portal *` (contrato) | MISSING | ver PO10-PO14 | Crash do servidor | P0 |
| C17 `routine *` | MISSING | ver R16 | — | P1 |
| C18 `workspace`, `floor`, `partitura`, `band` (skill) | MISSING | `CLIRouter.swift:14-35`; grep `partitura` em `InterAgentServer.swift`: 0, embora `docs/linux-gtk-sprint7-spec.md` §2 declare esses endpoints | `maestri-workspace` e `floor status` sem backend | P1 |
| C19 `debug` e `--help` | IMPLEMENTED | `CLIRouter.buildDebugInfo`; `Sources/CLI/Help.swift`; `DebugCommand.swift` | — | — |
| C20 Skills injetadas em `~/.claude/skills` | MISSING | `SkillInjector.swift:16-18` `inject` no-op; `installSkillsIfNeeded` só em `AppDelegate` macOS; `Sources/Server/main.swift:10-30` não chama; conteúdo = 3 skills (`open-maestri`, `-portal`, `-manager`) | Nenhuma skill instalada no Linux; faltam routines, workspace, portal-devices, chat, computer; nomes diferem dos `maestri*` | P1 |

### Top 10 do auditor (parte 4)
1. Portal via CLI derruba o servidor (`PlatformHooks.portalsService` nunca injetado) e mata todos os PTYs.
2. Conexões não são restauradas no start do servidor (C13).
3. `ask` não funciona para agentes reais (30 s, heurística de shell, texto+CR num write).
4. Nós criados por CLI (nota, recruta, portal) nunca chegam ao canvas GTK.
5. Skills não são instaladas; sem `maestri`/`MAESTRI_CLI`.
6. Hooks de floor: sem UI, sem execução, sem env nos PTYs do core.
7. Remote SSH ausente por inteiro.
8. CLI de `routine`, `workspace`, `floor`, `partitura` inexistente (REST existe).
9. Floors sem camada de canvas (sem Clone Ground layout, sem canvas por floor, sem renomear, sem lista de arquivos).
10. `note read` sem o formato do contrato; rotinas pausadas silenciosamente ao trocar de workspace.

Contagem (78 linhas): IMPLEMENTED 29 · PARTIAL 9 · STUB 6 · MISSING 22 · DIVERGENT 11 · N/A-LINUX 1 · UNVERIFIED 0
