# Sprint 5 (GTK4): paridade com macOS em Floors UI, Context Toolbars e Snapping Guides (spec executável)

- **Data:** 2026-11-05 · **Branch:** `feat/linux-port` · **Autor:** Arquiteto
- **Fonte da verdade:** `docs/reference/official/03-canvas.md`, `05-notes.md`, `09-floors.md` e `docs/reference/maestri-reference-index.md` §3, §9, §14. Quando a referência e o código macOS divergem, **a referência vence** e a divergência fica marcada com ⚠.
- **Implementação macOS espelhada:** `Sources/WorkspaceCanvas/*ContextToolbar.swift`, `WorkspaceCanvasView.swift:46-210`, `Sources/Canvas/Visual/{TileSnapping,MagneticSnapGuideView}.swift`, `Sources/Floor/*`
- **Base:** `docs/linux-gtk-sprint4-spec.md`. A §1 dela (Floors) continua valendo, e aqui ela vira tarefas.

## 0. Regras da sprint

1. **Testes:** uma única rodada no fim da sprint, sempre headless: `xvfb-run -n 99 -s "-screen 0 1920x1080x24" env WAYLAND_DISPLAY= python3 -m pytest -q && swift test -Xswiftc -use-ld=lld`. Durante o desenvolvimento não se roda pytest, swift test nem E2E. Cada tarefa entrega os testes **escritos**, e eles só rodam na rodada final.
2. **Índice git compartilhado:** cada agente commita só os próprios arquivos (`git commit -- <paths>`). Para arquivo com hunks misturados, usar um `GIT_INDEX_FILE` temporário. Antes de editar, sempre olhar `git status` e `git diff <arquivo>`.
3. **`window.py` e `widget.py` não crescem.** Cada feature fica num módulo próprio. Na janela entram no máximo 3 linhas de ligação por feature (import, construção e um `connect`). No widget entra só API pública genérica.
4. **Formato:** campos e enums com os mesmos nomes do Swift (`ShapeStrokeStyle`, `ShapeFillStyle`, `StrokeContent` e os demais) e o wrapper `{ "kind": { "_0": … } }`.
5. **Antigravity (Sentinela, Forja):** recebem só tarefas marcadas **[atômica]**, com no máximo um arquivo de código e um arquivo de teste por tarefa, e argv/funções puras.

## 1. Inventário de paridade (estado em 2026-11-05)

| Item macOS / referência | GTK hoje | Lacuna |
|---|---|---|
| Toolbar contextual (single select) por tipo | **não existe**. A nota tem apenas swatches no rodapé e o menu ⋯ | toda a §3 |
| Multi-seleção: toolbar só com 🗑 | não existe | T3.1 |
| Nó `stroke` (line/arrow) e o `StrokeContextToolbar` dele | **o tipo não existe** em `drawn.py` | T3.6 |
| Snap magnético com Ctrl e guias azuis | existe (`snapping.py` + `renderer._overlay`) | alpha das guias e o *gap filling* (⚠ a referência pede, o macOS não implementa) |
| Tecla `L` = conectar a partir do selecionado | não existe em `interaction.key` | T3.2 |
| Botão de floor, os diálogos e Land | não existe. A sidebar mostra "🌿 main (active)" fixo | toda a §2 |
| `/api/floors` GET/POST/PATCH/DELETE | existe | faltam status, land-preview, land e hooks |
| Proteção R1 (floors não sobrescritos pelo sync) | parcial: o servidor só preserva quando o cliente manda `[]` ou omite o campo | **um snapshot GTK com lista antiga ainda ressuscita um floor apagado ou apaga um criado** (T2.0) |

## 2. Floors UI

Ordem: T2.0 → (T2.1 ∥ T2.2) → T2.3 → T2.4..T2.7.

**T2.0: fechar o R1 [atômica] (Sentinela)**
- `maestri_core/sync.py`: `snapshot()` remove as chaves `floors` e `crossFloorConnections` do dict enviado. O servidor já preserva campos ausentes (`InterAgentServer.swift:731`).
- Teste: `tests/unit/test_sync.py::test_snapshot_omits_server_owned_floor_fields`.

**T2.1: endpoints que faltam no core (Sentinela)**, em `InterAgentServer.swift` + `FloorManager.swift`:
| Rota | Comportamento |
|---|---|
| `GET /api/floors/{id}/status` | `{dirty, files:[{path,status}], ahead, behind, insertions, deletions}` a partir de `git -C <worktree> status --porcelain=v1` e `git diff --shortstat <base>...<branch>` |
| `GET /api/floors/{id}/land-preview?target=<branch>` | `git merge-tree --write-tree --name-only <target> <branch>`. Exit 1 significa conflitos: `{conflicts:[paths], insertions, deletions, fastForward: bool}` |
| `POST /api/floors/{id}/land` `{target}` | 409 se `dirty`. `git fetch <worktree> <branch>:<branch>`. Se `target == branch` ou o merge é fast-forward, faz `update-ref`. Senão, `merge-tree --write-tree` → `commit-tree -p target -p branch` → `update-ref refs/heads/<target>`. **Nunca** roda `git merge` no Ground (⚠ divergência do macOS). Se `target` estiver checked-out no Ground, devolve 409 com a mensagem "faça checkout de outra branch ou use o terminal" |
| `POST /api/floors/{id}/hooks/{setup\|run\|teardown}` `{index?}` | executa os hooks, um ou todos, com `cwd=worktree`, env `MAESTRI_*` + `OMAESTRI_*` e timeout de 600 s. A saída sai por `/ws/events` como `floorHookOutput{floorId, hook, index, chunk}` e o fim como `floorHookFinished{…, exitCode}` |
| `DELETE /api/floors/{id}?deleteBranch=0\|1` | renomear a query `removeWorktree` → `deleteBranch`. O worktree **sempre** é removido, como diz a referência. Teardown antes. Se for o último floor, apaga `.open-maestri/floors/` |

- Slug e `.git/info/exclude`: ver Sprint 4 §1.
- swift test: `FloorManagerTests.testSlug`, `testLandArgvNeverTouchesWorkingTree` e `testHookEnvHasBothPrefixes`.

**T2.2: cliente Python [atômica] (Forja)**
- `maestri_core/client/api.py`: `floor_status(id)`, `land_preview(id, target)`, `land_floor(id, target)`, `run_floor_hook(id, hook, index=None)` e `delete_floor(id, delete_branch)`.
- Teste: `tests/unit/test_api_client.py`, com URLs e bodies validados por mock.

**T2.3: `gtk4-ui/src/ui/floors.py`, classe `FloorsController(window)` (Pixel).** A janela só faz `self.floors = FloorsController(self)`.
- Estado: `floors: list[dict]` e `active_floor_id | None` (None = Ground). Recarrega em `workspace-loaded` e nos eventos `/ws/events` `floorCreated`, `floorRemoved` e `floorUpdated`. A rede roda em worker thread e o resultado volta por `GLib.idle_add`.
- **Botão de floor:** `Gtk.MenuButton` no overlay do `CanvasWidget`, `halign=END`, `valign=END`, margem de 16 px, à esquerda do minimap quando ele existir. O rótulo mostra `🏢 <nome do floor ativo | Ground>`. Ao lado fica `Gtk.MenuButton` ⚡, visível só quando o floor ativo tem hooks.
- **Popover do botão** (substitui a visão 3D ⚠):
  - lista de floors com Ground no topo; cada linha mostra nome, branch, badge `+ins −del` e ● se dirty;
  - clicar numa linha troca de floor;
  - botão × na linha abre T2.6;
  - **"＋ Novo floor…"** abre T2.4;
  - **"Land…"** abre T2.5 e só fica sensível num floor que não seja o Ground.
- **Clique direito no botão** abre `Gtk.PopoverMenu` com "Renomear…", "Configurar Hooks…" e "Apagar…".
- **Popover ⚡:** hooks agrupados por Setup, Run e Teardown, ▶ por comando e **Run All**. A saída aparece num `Gtk.TextView` somente leitura dentro do popover.
- **Trocar de floor:** terminais criados depois da troca nascem com `cwd=worktreePath` e env `MAESTRI_FLOOR_NAME`, `MAESTRI_BRANCH_NAME`, `MAESTRI_FLOOR_PATH`, `MAESTRI_ROOT_PATH` e `MAESTRI_PROJECT_NAME`. O ponto de ligação é `window._create_node`, que pede `self.floors.terminal_env()`.
- **Sidebar:** `sidebar.py` passa a usar `FloorsController.floors` e deixa de usar o rótulo fixo.

**T2.4: `NewFloorDialog` (Pixel).** `Gtk.Window` modal com `transient_for`:
- `Gtk.Entry` "Nome" (obrigatório);
- `Gtk.DropDown` com "Nova branch" / "Branch existente". Na nova branch aparecem um `Gtk.Entry` com o nome (pré-preenchido `floor/<slug>`) e uma base (`Gtk.DropDown` com `GitOps.argv_for("branches")`). Na existente aparece um `Gtk.DropDown` com as branches;
- `Gtk.CheckButton` "Clonar layout do Ground". Quando marcado, copia os nós do Ground com novos UUIDs, e as conexões são remapeadas;
- botões Cancelar e **Criar** (`suggested-action`). Criar fica insensível com nome vazio ou ref inválida (`_valid_ref`). Durante o POST aparece um `Gtk.Spinner`. Um erro do core aparece num `Gtk.Label` vermelho, e o diálogo não fecha.

**T2.5: `LandDialog` (Pixel)**
- Layout: `[branch do floor]  ✈  [Gtk.DropDown destino]`, com "mesma branch do floor" como padrão.
- Ao trocar o destino, chama `land_preview`, que mostra stat `+N −M` e a lista de conflitos em vermelho.
- **Merge** fica insensível se `dirty` (com a mensagem "Faça commit antes de pousar") ou se houver conflitos (com a mensagem "Resolva os conflitos no terminal/IDE", ⚠ como na referência). Depois do sucesso, mostra um toast "Floor pousado em <target>" e fecha.

**T2.6: `DeleteFloorDialog` (Pixel).** `Gtk.AlertDialog` com os botões ["Cancelar", "Manter branch", "Apagar branch"]. `cancel_button=0`; "Apagar branch" é `destructive`. Chama `delete_floor(id, delete_branch)`. Se o floor apagado era o ativo, volta para o Ground.

**T2.7: `HooksDialog` e Renomear (Pixel)**
- HooksDialog tem três seções, Setup, Run e Teardown. Cada uma é um `Gtk.ListBox` de `Gtk.Entry`, com ✕ por linha e **"+ Add command"**.
- Setup tem ainda `Gtk.Switch` "Auto-run".
- O rodapé lista as variáveis disponíveis como texto selecionável.
- Salvar faz `patch_floor(id, hooks=…)`.
- Renomear usa um `Gtk.Entry` num popover e também chama `patch_floor`.

**Testes (escritos agora, rodados na rodada final):**
- `tests/unit/test_floors_controller.py`: estado, troca de floor e `terminal_env()`, puro, com client fake;
- `tests/e2e/test_floors_dialogs_e2e.py` (Xvfb): sensibilidade do Criar e do Merge, os 3 botões do Delete e o add/remove de linhas de hook.

## 3. Context Toolbars

### Contrato comum: `canvas-cairo/maestri_canvas/context_toolbar.py`
- `ContextToolbarHost(canvas)` cria **uma** `Gtk.Box` no overlay do `CanvasWidget`, com `halign=CENTER`, `valign=START` e `margin_top=12`. É o equivalente ao "segundo nível" do macOS, 12 px abaixo da toolbar principal. CSS `.maestri-context-toolbar`: fundo `@window_bg_color`, raio 10, borda `alpha(currentColor,.1)`, sombra `0 2px 8px alpha(black,.08)`, padding 5×8 e espaçamento 2. Botões `flat` de 30×30 com tooltip. Os separadores são `Gtk.Separator(VERTICAL)` com margem 4. Botão destrutivo leva `.destructive` (ícone vermelho no hover).
- **Visibilidade:** atualiza em `selection-changed` e `tool-changed`. Fica escondido quando `controller.tool != "select"`, quando a seleção está vazia ou quando o editor inline de texto está ativo. Em multi-seleção mostra só 🗑. Em seleção única mostra o variant do `kind`. A transição é um `Gtk.Revealer` `SLIDE_DOWN` de 150 ms.
- O conteúdo do toolbar é marcado com `_ROLE=("content", …)` para não iniciar drag nem pan no canvas.
- Variants são classes `XxxToolbar(Gtk.Box)` com `bind(node_id, content: dict)` e um sinal `content-changed(node_id, dict)`. O host reemite esse sinal como `node-content-changed` no canvas. A janela já persiste o conteúdo dos nós desenhados via `DrawingTools._on_content_changed`, e a mesma rota passa a valer para os outros tipos (`bus.emit("node-updated")`).
- Ícones: nomes simbólicos do Adwaita. O mapeamento de SF Symbol → ícone GTK fica na tabela `ICONS` do módulo, por exemplo `trash` → `user-trash-symbolic`, `arrow.trianglehead.branch` → `network-transmit-receive-symbolic`, `bold` → `format-text-bold-symbolic`.

### Variants (paridade exata com o Swift)

| Variant | Botões, na ordem (separador = ┃) | Faixas |
|---|---|---|
| **T3.1 Multi** | 🗑 Delete | — |
| **T3.2 Terminal** (`NodeContextToolbar`) | ✎ Edit · ⇄ Connect · [badge N conexões] · ⟳ Refresh · 🗑 | Refresh fica **insensível** (no macOS é um botão reservado) |
| **T3.3 FileTree** | 📂 Abrir no gerenciador de arquivos · ⚙ Trocar raiz · 🗑 | — |
| **T3.4 Note** | 🎨 Cor · Aa Tamanho ┃ **B** · *I* · ~~S~~ · `</>` ┃ H (menu H1/H2/H3) · ☑ Tarefa · • Lista · {} Bloco ┃ 🖼 Imagem · ⧉ Copiar tudo · ⤓ Salvar como · ↪ Mover para… ┃ ⇄ Connect · [badge] · M Formatado/Raw (toggle ativo) · 🗑 | fonte 10–32. "Mover para…" vem da referência 05-notes (⚠ não está no toolbar macOS) |
| **T3.5 Text** | − tamanho + ┃ Aa (fonte) · 🎨 cor ┃ 🗑 | fonte 8–96 |
| **T3.6 Shape** | 🎨 tema ┃ − espessura + ┃ ≡ traço (solid/dashed/dotted) · ⧉ preenchimento (solid/none/hatched/crossHatched) ┃ − fonte + ┃ 🗑 | espessura 1–20, fonte 8–72 |
| **T3.7 Stroke** | 🎨 tema ┃ − espessura + ┃ ≡ traço ┃ 🗑 | espessura 1–20 |
| **T3.8 Freehand** | 🎨 tema ┃ − espessura + ┃ 🗑 | espessura 1–20 |

Os botões − e + são um `Gtk.Button` cada, com um `Gtk.Label` "Npt" ou "N" entre eles, limitados às faixas acima. Os menus de estilo são `Gtk.MenuButton` com popover de opções e um ✓ na opção atual. A cor de tema abre um popover com a paleta `_NAMED` de `drawn.py` mais um `Gtk.ColorDialogButton` (hex). A cor da nota usa `note_colors.PRESETS` mais o custom.

**Ações por variant:**
- **T3.2 Terminal (Pixel):**
  - Edit abre `EditTerminalDialog` (`gtk4-ui/src/ui/edit_terminal.py`) com abas Details (nome, comando) e Appearance (tema `terminal_themes`, fonte via `Gtk.FontDialogButton`). Role fica fora do escopo.
  - Connect chama `canvas.begin_connection(node_id)`, uma nova API pública que entra no estado de conexão a partir do pin direito.
  - O badge abre um popover que lista os pares (nome e ícone) com ✕, e o ✕ chama `window._delete_connection(id)`.
  - **A tecla `L`** com um terminal selecionado e o canvas focado chama `begin_connection`. O lugar é `interaction.key` [atômica] (Sentinela).
- **T3.3 FileTree [atômica] (Forja):**
  - "Abrir no gerenciador" chama `org.freedesktop.FileManager1.ShowFolders` via `Gio.DBusProxy`. O fallback é `Gio.AppInfo.launch_default_for_uri`.
  - "Trocar raiz" abre `Gtk.FileDialog.select_folder`, depois chama `FileTreeView.set_root` e persiste `rootPath`.
- **T3.4 Note (Pixel):** a `StickyNoteView` ganha API pública:
  - `wrap_selection(prefix, suffix)`;
  - `insert_line_prefix(p)`, que alterna o prefixo se ele já existe;
  - `insert_code_block()`;
  - `insert_image()`, que abre `Gtk.FileDialog` e copia o arquivo para `notes_dir` como `![](…)`;
  - `copy_all()` via `Gdk.Clipboard`;
  - `save_as()`, que abre `Gtk.FileDialog.save` e grava uma cópia sem mudar o storage.

  O menu ⋯ do header continua existindo, e os dois chamam a mesma API. Os swatches do rodapé **saem**: a cor fica só no toolbar, como no macOS.
- **T3.5 Text:** "Aa" abre `Gtk.FontDialog.choose_family`, que grava `fontFamily`.
- **T3.6 Stroke, o tipo novo (Pixel + Sentinela).** É pré-requisito do T3.7.
  - `drawn.py` ganha `STROKE_TOOLS=("line","arrow")`, `default_stroke()` (campos de `StrokeContent`, com pontos normalizados 0–1), `draw_stroke()` (arrow é uma Bézier quadrática por `controlPoint` com cabeça de 12 px; os traços dashed e dotted usam `set_dash` com `[6,4]` e `[1,4]`) e `kind_for_tool("line"|"arrow") → "stroke"`.
  - `DrawingTools` ganha as ferramentas "／ Linha" e "→ Seta".
  - A prévia de arraste fica no overlay (tracejado animado, como `MagneticSnapGuideView.drawStrokePreview`).
  - O round-trip com Swift é validado em `test_cross_language_doc_generator.py`.

**Testes (escritos agora):**
- `tests/unit/test_context_toolbar.py`: escolha do variant por kind e seleção; esconder com ferramenta ativa; clamps das faixas; o `content-changed` produz o dict esperado (por exemplo, `strokeStyle` "dashed"). Sem display: os builders retornam especificações, com testes puros para `toolbar_spec(kind) -> list[ButtonSpec]`.
- `tests/unit/test_drawn_stroke.py`;
- `tests/e2e/test_context_toolbar_e2e.py` (Xvfb): selecionar uma nota e clicar **B** deve envolver a seleção com `**`; selecionar dois nós deve mostrar só 🗑.

## 4. Snapping Guides

O que já existe e **não deve ser reescrito**: `snapping.snap()` (porte 1:1 de `TileSnapping.swift`: limiar 12, raio 600, guias ±20), a ativação com Ctrl no drag (`interaction._drag`) e o desenho em `renderer._overlay`.

**T4.1: visual [atômica] (Forja).** Em `renderer._overlay`, as guias passam a usar `ACCENT` com alpha 0.7 e linha de 1 px de tela (`1/zoom`), como no `MagneticSnapGuideView`. As guias só aparecem enquanto Ctrl está pressionado: soltar o Ctrl no meio do drag limpa as guias e retoma o movimento livre no próximo `move`, e isso já funciona. Teste: `test_cairo_renderer.py::test_guides_alpha`.

**T4.2: gap filling (⚠ a referência pede "填隙", o macOS não implementa) (Sentinela [atômica], só `snapping.py` + o teste)**
- Para cada eixo, procurar o par de vizinhos A e B que se sobrepõem ao nó arrastado no eixo ortogonal (no eixo X, os intervalos Y se sobrepõem) e que têm o vão `g = B.min − A.max` com `|g − d| ≤ 2·SNAP_THRESHOLD`, onde `d` é a largura (no eixo X) ou a altura do nó.
- Se o centro do nó está dentro do vão, ou seja, `A.max ≤ cx ≤ B.min`, o nó encaixa em `x = A.max + (g − d)/2`, centralizado no vão. **Isso não redimensiona.** O resultado gera **duas** guias verticais, em `A.max` e `B.min`.
- O gap filling **tem prioridade** sobre o wall matching no mesmo eixo, porque um encaixe nos dois lados é melhor que em um.
- A assinatura não muda: `snap(dragging, others) -> (Point, list[GuideLine])`.
- Testes em `tests/unit/test_snapping.py`: vão exato; vão com ±10 de folga; vão grande demais (cai no wall matching); vizinhos sem sobreposição ortogonal (ignorados); e os casos de wall matching existentes devem continuar passando.

**T4.3: multi-seleção e duplicação [atômica] (Sentinela).** Confirmar, com teste, que no batch drag o snap usa o nó primário e desloca o grupo (o macOS faz o mesmo em `CanvasInteractionHandler.swift:409`). Confirmar também que no Alt-drag (duplicar) o snap vale para os fantasmas. Teste: `test_interaction.py::test_ctrl_snap_batch_and_duplicate`.

**T4.4: Ctrl + clique [atômica] (Sentinela).** A seleção aditiva é Shift (`interaction.py:216`), então o Ctrl fica livre para o snap. Garantir com teste que um Ctrl+clique sem movimento apenas seleciona o nó, não move nada e não deixa guias no overlay. Teste em `test_interaction.py`.

## 5. Plano de execução e donos

| Ordem | Tarefa | Dono | Depende de |
|---|---|---|---|
| 1 | T2.0, T4.1, T4.2, T4.3, T4.4 | Sentinela / Forja (atômicas) | — |
| 2 | T2.1 | Sentinela | T2.0 |
| 2 | T2.2, T3.3 | Forja | — |
| 2 | Host do toolbar + T3.1, T3.2 (sem `L`), T3.5 | Pixel | — |
| 3 | T3.6 stroke (modelo: Sentinela; UI: Pixel) e depois T3.7, T3.8 | Pixel | — |
| 3 | T3.4 Note | Pixel | host |
| 4 | T2.3 → T2.4..T2.7 | Pixel | T2.1, T2.2 |
| 5 | **Rodada única de testes (Xvfb :99)**, correções, commits e PR | Forja (CI) e Arquiteto (revisão) | todas |

**Definição de pronto da sprint:**
- todos os itens da §1 resolvidos ou marcados ⚠ com justificativa;
- `window.py` com crescimento menor que 30 linhas;
- a rodada única verde ou com as falhas documentadas;
- uma captura em Xvfb de cada variant de toolbar e dos 4 diálogos de floor em `artifacts/sprint5/`.
