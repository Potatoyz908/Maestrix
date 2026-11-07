# Sprint 7 (GTK4): paridade fina em barra inferior, menus de contexto e docking de conexão

- **Data:** 2026-11-06 · **Branch:** `feat/linux-port` · **Autor:** Arquiteto
- **Fonte da verdade:** as 3 capturas do macOS enviadas pelo usuário (`uploaded_media_0..2`). **Elas vencem** o código Swift deste repositório quando divergem, e cada divergência fica marcada com ⚠. Os itens inferidos (o que a captura não mostra) estão marcados com 🛈.
- **Estado de partida:** `gtk4-ui/src/ui/floating_bars.py` (pill único), `window.py:1179` (menu do canvas com 4 itens), WIP não commitado em `scene.py` e `interaction.py` (`hit_connect_target` e `nearest_side`).
- **Regras herdadas:**
  - testes numa rodada única no fim da sprint, com `xvfb-run -n 99`;
  - commit só dos próprios paths;
  - `window.py` não cresce: cada feature vive no seu módulo;
  - ícones são **sempre** nomes `*-symbolic` (outline monocromático). **Emojis ficam proibidos** em toda a UI tocada por esta sprint.

## 1. Barra inferior: 4 pills (`floating_bars.py` → `BottomBar`)

Captura 0: `[ 🥞 ws_name ]  [ ⭕⭕⭕ ]  [ 🗺️ ]  [ — 87% + ]`. São quatro `Gtk.Box.maestri-floating-pill` **separados**, 8 px entre eles, ancorados com `halign=END`, `valign=END` e margem de 16 px. Cada pill tem fundo próprio, raio 10, altura 32 e sombra `0 2px 8px alpha(black,.08)`.

| Pill | Widget | Ícone | Ação | Sinais de entrada | Saída |
|---|---|---|---|---|---|
| **P1 Floor/Workspace** | `Gtk.MenuButton` | `view-dual-symbolic` (pilha) + `Gtk.Label` com o nome do workspace, ou `ws_name · floor` quando o floor ativo não é o Ground. Elipsizado em 24 chars | abre o popover de floors (`FloorsController.popover`, Sprint 5 T2.3). O botão **é** o MenuButton: nada de `emit("clicked")` | `bus` `workspace-loaded`, `workspace-renamed` e `floor-switched` | — |
| **P2 Anéis de agentes** | `Gtk.Box` com até 5 `AgentRing` (`Gtk.DrawingArea` 22×22) e `+N` | anel = círculo com a cor do ícone do terminal. **Arco animado** enquanto houver atividade, **anel cheio** quando ocioso e **ponto vermelho** quando o processo saiu | clique num anel centraliza e foca o terminal (`canvas.focus_node(id)`, a mesma lógica do `\`). O tooltip mostra o nome e o estado | `terminal-activity(id, active)`, um novo sinal do canvas descrito abaixo; `terminal-exited`; `node-added` e `node-removed` | — |
| **P3 Minimap** | `Gtk.ToggleButton` | `map-symbolic` 🛈 | alterna a visibilidade de `Minimap` (T1.3) | — | `win.toggle-minimap` (estado) |
| **P4 Zoom** | `Gtk.Box`: `Gtk.Button` `zoom-out-symbolic` · `Gtk.Button` com o `Gtk.Label` "87%" · `Gtk.Button` `zoom-in-symbolic` | — | `−`/`+` chamam `viewport.zoom_step(center, ∓1)`, que **respeita** `CANVAS_MIN/MAX_ZOOM` (0,1–3,0). Clicar no `%` faz `set_zoom_at(center, 1.0)` | `viewport-changed` | `viewport-changed` |

Os anéis só aparecem para terminais com `monitorWithOmbro == true`, o que liga a P2 ao item **Monitor Activity** do menu (§3). Sem nenhum terminal monitorado, a P2 fica oculta. ⚠ No macOS esse dado vem do Ombro; aqui o cálculo é local e o Ombro continua fora de escopo.

**`terminal-activity`:** novo sinal em `CanvasWidget`, `(str id, bool active)`. Um terminal fica **ativo** quando o Vte recebe `contents-changed` e **ocioso** depois de 1,5 s sem mudança (timer `GLib.timeout_add`, rearmado a cada mudança, emitindo só nas transições). Isso vale tanto para a VTE local quanto para a `VteTerminalView` do core.

**Bugs atuais que esta seção corrige (T1.0, atômica):**
- **B1:** `_on_floors_clicked` chama `window.floors.button.emit("clicked")`, mas `FloorsController` não tem `button`. O clique não faz nada.
- **B2:** o "minimap" põe `origin=(0,0)` e `zoom=1`, o que leva a uma área vazia, já que o conteúdo fica em torno de (9800, 8500).
- **B3:** `_zoom_step` limita o zoom a 4,0 (o máximo real é 3,0), escreve em `vp.zoom` direto e emite `viewport-changed` de fora do controller.

**T1.3 `Minimap` (`canvas-cairo/maestri_canvas/minimap.py`):**
- É uma `Gtk.DrawingArea` de 180×120 que fica acima da P3, com `halign=END`, `valign=END` e `margin_bottom=56`.
- Desenha os retângulos dos nós em escala, com cor por kind, e o retângulo do viewport. Clicar ou arrastar centraliza o viewport ali.
- O estado visível vai para `preferences.json` como `showMinimap`.

## 2. Menu de contexto do canvas vazio (captura 1)

Fica em `gtk4-ui/src/ui/context_menus.py`, classe `CanvasContextMenu`. A janela só faz a construção.

```
＋ Add                  ▸   list-add-symbolic
    Terminal            ▸   utilities-terminal-symbolic
        <preset ativo 1..n>          (ícone do preset)
    Note                    text-x-generic-symbolic
    File Tree               folder-symbolic
    Portal                  web-browser-symbolic
    Text                    format-text-plain-symbolic   🛈 (a captura mostra o texto, não o ícone)
Paste                       edit-paste-symbolic          (insensível se o clipboard não tiver um elemento nem texto)
─────────
Partituras              ▸   view-grid-symbolic 🛈
    <partitura 1..n>        → insert
    ─────
    Save current layout as Partitura…
```
⚠ O Swift tem um item "Attachment" que a captura não mostra, por isso ele **sai**.

| Item | Gio action (grupo `ctx`, alvo = ponto do clique em coords de canvas, guardado no `CanvasContextMenu`) | Efeito |
|---|---|---|
| Terminal ▸ preset | `ctx.add-terminal` `s:preset_id` | `window._create_node("terminal", at=pt, preset=id)`. Isso pula o `NewTerminalDialog`, porque o preset já define o agente, como no macOS |
| Note / File Tree / Portal / Text | `ctx.add` `s:kind` | `_create_node(kind, at=pt)`. Text usa `DrawingTools.create("text", Rect(pt, TEXT_DEFAULT_SIZE))` e abre o editor inline |
| Paste | `ctx.paste` | lê `Gdk.Clipboard`. Com o mime `application/x-maestri-element` (vindo de **Copy Element**, §3), recria o nó com novo UUID em `pt`. Com `text/plain` contendo uma URL, cria um Portal. Com outro texto, cria uma Note com o conteúdo 🛈 |
| Partitura ▸ nome | `ctx.partitura-insert` `s:name` | equivalente REST a `maestri partitura insert "Name"`: `POST /api/partituras/{name}/insert {at:[x,y]}`. A resposta é aplicada ao documento na main thread |
| Save current layout… | `ctx.partitura-create` | diálogo com o nome e um checklist dos terminais selecionados (padrão: todos) → `POST /api/partituras` |

**Core (Sentinela, atômicas):**
- `GET /api/partituras`, `POST /api/partituras`, `POST /api/partituras/{name}/insert` e `DELETE /api/partituras/{name}`.
- Armazenamento em `~/.open-maestri/partituras/{slug}.json` com `{name, nodes, connections, roles}`. Os frames são relativos ao canto superior esquerdo do bounding box, e os UUIDs são regenerados no insert.
- A CLI `omaestri partitura list|insert|create|update|delete` precisa bater com a skill `maestri-manager`.

## 3. Menu de contexto de nó (captura 2: Terminal) e variantes

Abre com um clique direito em **qualquer** ponto do nó terminal, header ou conteúdo VTE. Para os outros kinds, abre só pelo header e pelas bordas: o conteúdo de nota, file tree e portal mantém o menu próprio do widget.

```
Copy                    edit-copy-symbolic           Ctrl+Shift+C
Paste                   edit-paste-symbolic          Ctrl+Shift+V
Select All              edit-select-all-symbolic     Ctrl+Shift+A
─────────
Rename                  document-edit-symbolic
Edit…                   preferences-system-symbolic
Monitor Activity    ✓   eye-open-negative-filled-symbolic / (check)
─────────
Blur                ✓   view-conceal-symbolic        🛈 alterna o embaçamento do conteúdo (privacidade)
Reload                  view-refresh-symbolic
Unload                  media-playback-stop-symbolic
New Element Defaults ▸  emblem-default-symbolic      🛈
    Use This Size for New Terminals
    Use This Appearance for New Terminals
    Reset Defaults
─────────
Copy Element            edit-copy-symbolic
Duplicate               edit-duplicate-symbolic (fallback: list-add-symbolic)
Lock / Unlock       ✓   changes-prevent-symbolic / changes-allow-symbolic
─────────
Delete                  user-trash-symbolic          (classe CSS .destructive: texto vermelho)
─────────
Partituras           ▸  view-grid-symbolic           (submenu igual ao §2, com "Save layout with this terminal…")
```
⚠ O Swift atual monta outra ordem e tem "Clear Buffer" e "Copy Terminal". A captura vence. "Clear Buffer" continua disponível só pelo atalho Ctrl+Shift+K.

**Mapeamento ação → sinal → persistência.** Grupo `node`, parâmetro `s:node_id`. O id vai no **parâmetro**, não em estado capturado, para não sobreviver a uma troca de workspace.

| Item | Action | Implementação | Sinal / bus | Persistido |
|---|---|---|---|---|
| Copy | `node.copy` | `term.copy_clipboard_format(Vte.Format.TEXT)` | — | — |
| Paste | `node.paste` | `term.paste_clipboard()` (bracketed paste via VTE) | — | — |
| Select All | `node.select-all` | `term.select_all()` | — | — |
| Rename | `node.rename` | `NodeFrame.begin_rename()`, um `Gtk.EditableLabel` no título. O Enter confirma | `node-renamed(id, name)` → `bus node-updated` | `content.name` |
| Edit… | `node.edit` | abre o `EditTerminalDialog` existente (`terminal_dialog.py`) | `node-updated` | `content.*` |
| Monitor Activity | `node.monitor` (stateful `b`) | alterna `content.monitorWithOmbro`. A P2 assina | `node-updated` e `terminal-monitor-changed(id, bool)` | `monitorWithOmbro` |
| Blur | `node.blur` (stateful `b`) | `content.add_css_class("maestri-blurred")`, onde o CSS aplica `filter: blur(6px)` ao filho. A entrada continua ativa 🛈 | — | não (só na sessão) 🛈 |
| Reload | `node.reload` | `terminate_terminal()` seguido de um novo `spawn_async` com o mesmo `cwd`, `argv` e env. No core-backed: `POST /api/terminals/{id}/restart` | `terminal-reloaded(id)` | — |
| Unload | `node.unload` | encerra o PTY (SIGHUP) e troca o conteúdo por um placeholder "Unloaded: clique para carregar" (`Gtk.Button` `media-playback-start-symbolic`), que faz o equivalente a Reload | `terminal-unloaded(id)` | não 🛈 |
| Use This Size… | `node.defaults-size` | `preferences.newElementDefaults.terminal.size = [w,h]` | `preferences-changed` | `preferences.json` |
| Use This Appearance… | `node.defaults-appearance` | copia `themeId`, `fontFamily`, `fontSize`, `icon` e `iconColor` | `preferences-changed` | `preferences.json` |
| Reset Defaults | `node.defaults-reset` | apaga `newElementDefaults.terminal` | `preferences-changed` | `preferences.json` |
| Copy Element | `node.copy-element` | `Gdk.ContentProvider` com o mime `application/x-maestri-element`, contendo o JSON `{node, connections:[]}` em schema v2 (`{"terminal":{"_0":…}}`), mais o fallback `text/plain` com o nome | — | — |
| Duplicate | `node.duplicate` | reusa `duplicate-requested({id: frame+24,24})` | `duplicate-requested` → `node-added` | sim |
| Lock | `node.lock` (stateful `b`) | `NodeItem.locked` + `CanvasNode.is_locked`. O header mostra `changes-prevent-symbolic` | `node-updated` | `isLocked` |
| Delete | `node.delete` | `delete-requested((id,))`. Se o nó estiver **locked**, o item fica insensível | `node-removed` | sim |
| Partituras ▸ | `ctx.partitura-*` | igual ao §2 | — | core |

**Variantes de outros kinds:** mesma estrutura, removendo os grupos que não se aplicam.
- **Note:** Rename · Copy Element · Duplicate · Lock · Delete · Partituras.
- **Portal:** Reload (`web.reload()`) · Copy Element · Duplicate · Lock · Delete.
- **File Tree / Text / Shape / Stroke / Freehand:** Copy Element · Duplicate · Lock · Delete.

`NewElementDefaults` também vale para Note (size e color) através do mesmo submenu 🛈.

**Implementação:**
- `CanvasWidget` ganha o sinal `context-requested(str kind, str id, float x, float y)`, onde `kind` é `canvas`, `node` ou `connection`. Ele é emitido por um único `Gtk.GestureClick(button=3)` em fase CAPTURE no próprio widget, e `target_at()` decide o kind.
- Para o conteúdo VTE, a gesture é **reivindicada**, porque a VTE GTK4 não tem menu próprio. Para notes, portals e file trees, quando o alvo é `NodeContent`, a gesture é negada e o filho mantém o menu dele.
- O clique direito numa conexão (`connection`) abre "Delete Connection" (paridade com `buildConnectionMenu`).
- Isso **substitui** `window._setup_context_menu` e `_on_canvas_right_click`.
- `context_menus.py` monta todos os `Gio.Menu`. Os ícones usam `Gio.MenuItem.set_icon(Gio.ThemedIcon.new(name))`.
- O item **Delete** fica vermelho assim:
  - O `Gio.Menu` não tem atributo de classe CSS, então o item é um **custom slot**: `item.set_attribute_value("custom", GLib.Variant("s", "delete"))`.
  - Depois, `popover.add_child(btn, "delete")`, onde `btn` é um `Gtk.Button` `flat` com a classe `.destructive` (`color: @error_color`). O clique ativa `node.delete` e fecha o popover.

## 4. Docking de conexão: soltar em qualquer área do nó

**Regra:** soltar o rope em **qualquer ponto** do retângulo de um nó conectável liga a conexão no **centro da borda** desse nó voltada para o nó de origem. A borda é escolhida pelo segmento centro→centro, proporcional ao aspecto (`nearest_side`). A mesma regra vale para a origem, em relação ao alvo. Isso é paridade com `ConnectionOverlayView.edgeAnchor`, trocando a interseção do raio pelo centro da borda que a captura mostra.

**Por que a borda voltada para o outro nó, e não a mais próxima do ponteiro:** os lados **não são persistidos**, porque o schema da Maestri (`TerminalConnection`) só tem `terminalIdA/B` e `ropePoints`. Com uma regra puramente geométrica, a âncora vista ao soltar é **a mesma** depois de reabrir o workspace e se atualiza sozinha quando os nós se movem. Já uma regra "mais próxima do ponteiro" faria a âncora pular no reload.

**Estado do WIP em `scene.py`/`interaction.py` (não commitado, de outro agente) e o que falta (T4.x, Pixel):**

| # | Situação | Ação |
|---|---|---|
| T4.1 | `PinSide` passou a incluir `top`/`bottom`, e `pin_point` calcula o centro da borda ✔ | manter |
| T4.2 | `hit_connect_target` aceita o corpo do nó ✔, mas **grava** `nearest_side` no `ConnectRequested`, e esse lado fica fixo no `ConnItem` mesmo depois que os nós se movem | um drop no **corpo** deve emitir `side=None` (automático). Só um drop num **pin** explícito grava o lado, e só na sessão |
| T4.3 | `Scene.anchors()` ainda usa `facing_sides` (só left/right) para `side=None` | trocar por `nearest_side(a.frame, b.frame)` e `nearest_side(b.frame, a.frame)` |
| T4.4 | a prévia durante o arraste (`overlay.connecting`) parte do pin de origem fixo (`st.side`) | a origem também segue a regra automática, a não ser que o arraste tenha começado num pin. O alvo destacado desenha a borda azul alpha 0,4, raio 6 e os **4 conectores** dos centros das bordas (`drawEdgeConnectors`), com o conector escolhido preenchido |
| T4.5 | área de acerto: `frame.contains(p)` | expandir em `DOCK_MARGIN_PX = 12 / zoom`, para que soltar logo fora da borda também conecte. O nó de cima vence, e kinds desenhados e o próprio nó de origem são transparentes ✔ |
| T4.6 | os pins visíveis continuam sendo só left/right | sem mudança na captura. Os 4 conectores aparecem apenas durante o arraste (T4.4) |
| T4.7 | `window._on_canvas_connect_requested` repassa os lados para `plan_connection` | sem mudança: `side=None` já é suportado. Confirmar que o `ConnItem` otimista usa `None` |

**Testes (escritos agora, rodados na rodada final):**
- `tests/unit/test_scene_docking.py`: drop no centro, num canto e na margem de 12 px; nó desenhado por cima (transparente); a origem nunca é alvo; um nó largo à direita entra pela esquerda; um nó logo abaixo entra por cima; e as âncoras recalculam depois de `set_frame`.
- `test_interaction.py::test_body_drop_emits_auto_side`.

## 5. Plano e donos

| Ordem | Tarefas | Dono |
|---|---|---|
| 1 | T1.0 (B1–B3), sinal `terminal-activity`, T4.2–T4.5 (depois do commit do WIP atual de docking) | Pixel |
| 1 | endpoints e CLI de partituras e `POST /api/terminals/{id}/restart` [atômicas] | Sentinela |
| 1 | `newElementDefaults` em `preferences.py` e o mime `application/x-maestri-element` (serialização pura) [atômicas] | Forja |
| 2 | `BottomBar` com as 4 pills + `minimap.py` | Pixel |
| 2 | `context_menus.py` (canvas, nó e conexão) + `context-requested` | Pixel |
| 3 | **Rodada única de testes (Xvfb :99)** + capturas em `artifacts/sprint7/` (a barra, os 2 menus e um drop no corpo) comparadas lado a lado com `uploaded_media_0..2` | Forja e Arquiteto |

**Definição de pronto:**
- nenhum emoji em `floating_bars.py` nem `context_menus.py` (`grep -P '[\x{1F300}-\x{1FAFF}]'` vazio);
- os 4 pills separados;
- os menus na ordem exata das capturas;
- o drop no corpo conecta no centro da borda e a âncora é idêntica depois do reload;
- a rodada única verde ou com as falhas documentadas.
