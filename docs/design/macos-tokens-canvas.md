# macOS design tokens: canvas (fonte → port GTK4/Cairo)

Referência macOS: `.upstream-macos-ref/Sources` (citada como `Sources/…:linha`) e `.upstream-macos-ref/docs/images/*.png`.
Port citado como `canvas-cairo/maestri_canvas/…:linha` ou `gtk4-ui/src/ui/…:linha` (linhas conferidas em 2026-11-06; outros agentes editam a árvore em paralelo, então confira com grep antes de mudar).
Capturas do port: `artifacts/design-canvas/01_empty_canvas.png`, `02_note_default.png`, `03_filetree_and_note.png`. Foram feitas em Xvfb :93 privado, com `OPEN_MAESTRI_DATA_DIR` temporário e servidor isolado. `~/.open-maestri` não mudou: `ls -la --time-style=full-iso` antes e depois, diff vazio.

Valores de cor de sistema do macOS 14 usados abaixo (claro / escuro): systemBlue `#007AFF`/`#0A84FF` (SwiftUI `.blue` e `Color.accentColor` padrão); systemGreen `#28CD41`/`#32D74B`; systemRed `#FF3B30`/`#FF453A`; systemOrange `#FF9500`/`#FF9F0A`; systemGray `#8E8E93`/`#98989D`; systemTeal `#59ADC4`/`#6AC4DC`; systemPurple `#AF52DE`/`#BF5AF2`; labelColor = preto 85% / branco 85%; secondaryLabel = preto 50% / branco 55%; tertiaryLabel = preto 26% / branco 25%; separatorColor = preto 10% / branco 10%; windowBackgroundColor `#ECECEC`/`#323232`; controlBackgroundColor `#FFFFFF`/`#1E1E1E`; textBackgroundColor `#FFFFFF`/`#1E1E1E`.
**O canvas do macOS não tem modo escuro**: o fundo é branco fixo (`Sources/Canvas/Core/CanvasViewportView.swift:374`, `Sources/Canvas/Visual/CanvasBackground.swift:51`) e as barras flutuantes usam `.white` literal. Por isso a coluna "macOS" vale para os dois temas, a não ser quando há nota em contrário. Medida em `docs/images/home.png`: os headers dos nós saem ≈ `#E0E4E5` (o material `.sidebar` mistura a cor do papel de parede); o corpo da nota sai ≈ `#E3EEEE`.

## 1. Tabela de tokens

| Elemento | Propriedade | Valor macOS | Fonte | Valor GTK hoje | Status | Correção |
|---|---|---|---|---|---|---|
| Canvas | fundo (modo `dotGrid`) | `#FFFFFF` (branco fixo, ignora o modo escuro) | `Sources/Canvas/Visual/CanvasBackground.swift:51`; `Sources/Canvas/Core/CanvasViewportView.swift:374` | `#F2F2F2` (0.949) | DIFFERENT | `renderer.py:20` `BG = (1.0, 1.0, 1.0)` |
| Canvas | fundo `solid` | `NSColor(white: 0.98)` = `#FAFAFA` | `Sources/Canvas/Visual/CanvasBackground.swift:35` | igual ao `BG` (`#F2F2F2`) | DIFFERENT | ramo `solid` usa `(0.98,0.98,0.98)` |
| Canvas | fundo `transparent` | `NSColor.clear` | `Sources/Canvas/Visual/CanvasBackground.swift:38` | não pinta | MATCH | — |
| Grade | tipo | **linhas** (o nome "dotGrid" engana): cada tile desenha a borda direita e a inferior | `Sources/Canvas/Visual/CanvasBackground.swift:98-106` | **pontos** 1.5×1.5 px | DIFFERENT | `renderer.py:77-91`: trocar por linhas verticais e horizontais |
| Grade | espaçamento | 16 unidades de canvas × zoom | `Sources/Shared/Constants.swift:17`; `CanvasBackground.swift:54` | `GRID_STEP = 40`; dobra enquanto `step*zoom < 12` | DIFFERENT | `renderer.py:28` `GRID_STEP = 16.0` (usar `CANVAS_GRID_SPACING` de `constants.py:7`, que já é 16) |
| Grade | cor | `NSColor(white: 0.90)` = `#E6E6E6`, alpha 1 | `Sources/Shared/Constants.swift:18` | `#CCCCCC` (0.80) | DIFFERENT | `renderer.py:21` `GRID = (0.90, 0.90, 0.90)` |
| Grade | espessura | 0.5 px **de tela** (não escala com o zoom) | `Sources/Shared/Constants.swift:19`; `CanvasBackground.swift:92-96` | ponto de 1.5 px | DIFFERENT | `cr.set_line_width(0.5)` em coordenadas de tela, linhas em `round(x)+0.25` para não borrar |
| Grade | some em zoom baixo | some quando `16*zoom < 4` px (não dobra o espaçamento) | `Sources/Canvas/Visual/CanvasBackground.swift:57` | dobra o passo até ≥ 12 px | DIFFERENT | `renderer.py:30,79`: `if 16*zoom < 4: return`; remover a duplicação |
| Grade | fase | `-(origin*zoom) mod spacing` | `Sources/Canvas/Visual/CanvasBackground.swift:72-73` | `floor(origin/step)*step` | MATCH (equivalente) | — |
| Zoom | mín / máx / passo | 0.1 / 3.0 / 0.25 | `Sources/Shared/Constants.swift:14-16` | 0.1 / 3.0 / 0.25 | MATCH | — |
| Nó (casca) | raio do canto | 10 | `Sources/Canvas/Core/CanvasNodesView.swift:11` | 8 px | DIFFERENT | `widget.py:429` `border-radius: 10px`; header `10px 10px 0 0` (`widget.py:433`) |
| Nó | fundo | `windowBackgroundColor` 75% sobre vibrancy `.popover` (behindWindow) → claro ≈ `#F1F1F1`, escuro ≈ `#323232` a 75% | `Sources/Canvas/NodeLayer/NodeShellView.swift:101-105` | `@window_bg_color` (tema GTK: roxo-escuro no Yaru-dark, veja a captura 03) | DIFFERENT | `background: rgba(236,236,236,.92)` claro / `rgba(50,50,50,.92)` escuro; não depender do tema GTK |
| Nó | borda | `separatorColor` × 0.6, 0.5 pt → preto 6% / branco 6% | `Sources/Canvas/NodeLayer/NodeShellView.swift:116-117` | `1px alpha(currentColor,.25)` | DIFFERENT | `border: 1px solid rgba(0,0,0,.06)` (GTK não faz 0.5 px; use 1 px com alpha reduzido) |
| Nó | sombra | preto 15%, raio 10, y +3 | `Sources/Canvas/NodeLayer/NodeShellView.swift:107` | nenhuma (só a nota tem `0 2px 8px alpha(black,.25)`, `sticky_note.py:32`) | MISSING | `.maestri-node { box-shadow: 0 3px 10px rgba(0,0,0,.15); }` (o blur do SwiftUI ≈ o blur-radius do CSS) |
| Nó | divisória header/corpo | `Divider()` opacidade 0.5 (separator) | `Sources/Canvas/NodeLayer/NodeShellView.swift:133` | nenhuma | MISSING | `.maestri-node-header { border-bottom: 1px solid rgba(0,0,0,.05); }` |
| Header | altura | 32 | `Sources/Canvas/Core/CanvasNodesView.swift:6`; `NodeShellView.swift:131` | 28 | DIFFERENT | `widget.py:51` `HEADER_H = 32` |
| Header | fundo | material `.sidebar` (behindWindow) + `themeColor` 18% só em nota | `Sources/Canvas/NodeLayer/NodeShellView.swift:227-233` | `alpha(currentColor,.06)` | DIFFERENT | claro `#E6E8EA` / escuro `#2B2B2D` (medido ≈ `#E0E4E5` em home.png) |
| Header | padding / espaçamento | horizontal 8; HStack spacing 6 | `Sources/Canvas/NodeLayer/NodeShellView.swift:204,225` | `padding: 0 8px`; Box spacing 6 | MATCH | — |
| Header | ícone | SF Symbol 11 pt, cor `headerColor` | `Sources/Canvas/NodeLayer/NodeShellView.swift:206-209` | sem ícone no header genérico | MISSING | `Gtk.Image` 12 px antes do título; cor = cor do nó |
| Header | título | system 12 pt **medium**, 1 linha, truncamento no fim | `Sources/Canvas/NodeLayer/NodeShellView.swift:211-214` | Label padrão do tema (≈ 11 pt regular) | DIFFERENT | `.maestri-node-header label { font-size: 12px; font-weight: 500; }` |
| Header | cadeado | `lock.fill` 10 pt, `.secondary` | `Sources/Canvas/NodeLayer/NodeShellView.swift:219-222` | não verificado no header | não verificado | ícone outline `maestri-lock` 10 px, alpha .5 |
| Header | botão fechar | **não existe** (o `onClose` vai para o menu de contexto) | `Sources/Canvas/NodeLayer/NodeShellView.swift:48,85` (guardado, nunca desenhado) | botão `maestri-close-symbolic` | DIFFERENT | remover o X do header (`widget.py:246`); fechar fica no menu de contexto |
| Seleção | anel | systemBlue, 2 pt, tracejado `[6,4]`, recuado 3 pt para fora, raio 10+3 = 13 | `Sources/Canvas/Core/CanvasNodesView.swift:12`; `NodeShellView.swift:150-156` | `border: 2px dashed #3b82f6` **na borda** (sem recuo; o padrão do traço é do GTK) | DIFFERENT | desenhar no overlay Cairo: `rounded_rect(f.inset(-3), r=13)`, `line_width 2`, `dash [6,4]`, `#007AFF`; tirar o dashed do CSS |
| Seleção | nós desenhados (texto/forma) | mesmo anel | `NodeShellView.swift:150` | `_dashed_rect`: 1.5 px, `[5,4]`, `#3B82F6`, retângulo reto | DIFFERENT | `renderer.py:155-160`: 2 px, `[6,4]`, `#007AFF`, raio 13 |
| Drop target | borda | `.blue` 80%, 2 pt, sólida, raio 10 | `Sources/Canvas/NodeLayer/NodeShellView.swift:160-163` | `inset 0 0 0 2px #3b82f6` | DIFFERENT (cor) | `widget.py:451`: `rgba(0,122,255,.8)` |
| Redimensionar | zona de captura | faixa de 10 px de tela de cada lado da borda; cantos primeiro; desligada se o nó for < 40 px | `Sources/Canvas/Interaction/CanvasHitTesting.swift:148,153-176` | lado 10 (5 para fora), canto 16 (6 para fora) | DIFFERENT (pequeno) | `widget.py:52-53`: `SIDE, SIDE_OUT = 20, 10`; `CORNER = 20` |
| Redimensionar | alças visíveis | nenhuma (só o cursor muda) | `Sources/Canvas/Interaction/ResizeEdge.swift:10-20` | invisíveis | MATCH | — |
| Redimensionar | mínimo | 160×80 (terminal 200×100; nota 120×80) | `Sources/Canvas/Core/CanvasNodesView.swift:8-9`; `Sources/Shared/Constants.swift:24-27` | 160×80 | MATCH (genérico) | aplicar os mínimos por tipo |
| Terminal | ícone / cor padrão | `terminal`, `#007AFF` | `Sources/Workspace/Models/NodeContent.swift:132-133` | sem ícone | MISSING | ícone outline de terminal em `#007AFF` |
| Terminal | footer | altura 26; `folder.fill` 9 pt + caminho mono 10 pt `.secondary`, truncado no meio; padding h 8; spacing 5; material `.sidebar`; divisória 0.3 | `Sources/Canvas/Core/CanvasNodesView.swift:7`; `NodeShellView.swift:141-144,240-258` | não existe | MISSING | Box de 26 px abaixo do VTE com `~/caminho` (`font: 10px monospace; opacity .5`; ellipsize MIDDLE) |
| Terminal | badge de role | cápsula com a cor do role (padrão `.blue`); `person.text.rectangle` 8 pt medium + nome 9 pt medium, branco; padding 6×2; spacing 3; logo após o título | `Sources/Canvas/NodeLayer/TerminalNodeSwiftUIView.swift:108-125` | `.maestri-node-badge` 12 px, `alpha(currentColor,.1)` | DIFFERENT | `.maestri-role-badge { background: <cor do role>; color: white; font-size: 9px; font-weight: 500; padding: 2px 6px; border-radius: 999px; }` |
| Terminal | marca do Maestro | `sparkles` 9 pt `.purple` (`#AF52DE`/`#BF5AF2`) à direita | `Sources/Canvas/NodeLayer/TerminalNodeSwiftUIView.swift:95-98` | não verificado | não verificado | ícone outline `sparkles` 9 px `#AF52DE` |
| Terminal | atenção | **círculo vermelho sólido 8×8** (`.red`), transição scale+opacity, **sem pulso** | `Sources/Canvas/NodeLayer/TerminalNodeSwiftUIView.swift:100-104` | ícone laranja `#ff9f0a` pulsando a 0.8 s | DIFFERENT | `widget.py:440`: ponto 8 px `#FF3B30` (escuro `#FF453A`), sem `animation` |
| Terminal | ponto busy/idle | não existe no header do macOS | — (ausente em `TerminalNodeSwiftUIView.swift:88-105`) | `.maestri-term-status` verde/cinza | DIFFERENT (extra) | manter só se a direção quiser; não é paridade |
| Terminal | fonte | `monospacedSystemFont` (SF Mono) **13 pt** regular | `Sources/Shared/Constants.swift:9-10`; `Sources/Terminal/SwiftTermProvider.swift:8-13` | `Monospace` 11 pt (Pango pt @96 dpi ≈ 14.7 px) | DIFFERENT | `terminal_themes.py:46-47`: família `"monospace"`, tamanho absoluto 13 px: `fd.set_absolute_size(13*Pango.SCALE)` (13 pt do mac = 13 px lógicos) |
| Terminal | tema padrão | `"dark"` → `maestri-dark`; `"system"` segue a aparência (dark → maestri-dark, light → maestri-light) | `Sources/Workspace/Models/Preferences.swift:37`; `Sources/Terminal/TerminalThemeRegistry.swift:103-121` | `"dark"`; `"system"` = cores padrão do VTE | DIFFERENT | `resolve_theme("system")` → maestri-dark ou maestri-light conforme `Adw.StyleManager.get_dark()` |
| Terminal | cursor / seleção | por tema (veja §1a) | `Sources/Terminal/TerminalThemeRegistry.swift:63-64` | não define | MISSING | `term.set_color_cursor(rgba(t.cursor))`; `term.set_color_highlight(rgba(t.selection))` |
| Terminal | temas | 15 temas + `system` = 16 opções | `Sources/Terminal/TerminalThemeRegistry.swift:126-502` | 8 | MISSING 7 | colar o bloco do §1a; adicionar os ids a `maestri_core/preferences.py:16` `TERMINAL_THEMES` |
| Nota | casca | **mesma casca cinza do nó**; cor da nota só a **5% no corpo** e **18% no header**; texto sempre `labelColor` | `Sources/Canvas/NodeLayer/NodeShellView.swift:109-114,230-231`; `NoteNodeSwiftUIView.swift:23-25` | card **opaco** na cor 100%; header `shade(.93)`; texto escuro/claro por luminância | DIFFERENT (o mais visível) | `note_colors.py:66-74`: `background: mix(<fundo do nó>, cor, .05)`; header `mix(<header>, cor, .18)`; `color` = label padrão |
| Nota | ícone do header | `note.text`, na cor da nota | `Sources/Canvas/NodeLayer/NoteNodeSwiftUIView.swift:23-24` | sem ícone (botões Raw/Formatted) | DIFFERENT | ícone outline `note.text` |
| Nota | paleta (10 nomes) | yellow `#FCF7B8`, pink `#F5BFCC`, blue `#BAD6F5`, green `#BAEDCC`, orange `#FCD6AB`, purple `#DEC4F5`, white `#F7F7F7`, black `#2E2E2E`, darkgray `#405261`, darkblue `#1A2E73` | `Sources/WorkspaceCanvas/NoteContextToolbar.swift:209-223,287-303` | 7 hex próprios (`#FEFDE8`, `#FFE8CC`, …); `NAMED` com outros valores | DIFFERENT | ver o dict no §1b; `note_colors.py:9-16` |
| Nota | cor padrão | `#FEFDE8` (hex, não nome) | `Sources/Shared/Constants.swift:30`; `NodeContent.swift:202` | `#FEFDE8` | MATCH | — |
| Nota | seletor | popover: linha 1 com 7 cores, linha 2 com 3 escuras; grava o **nome** | `Sources/WorkspaceCanvas/NoteContextToolbar.swift:209-223` | 7 bolinhas no rodapé da nota; grava hex | DIFFERENT | mover para a barra contextual; gravar os nomes |
| Nota | margem do texto | `textContainerInset` 4×8 (+5 de `lineFragmentPadding` padrão) | `Sources/Note/NoteTextEditor.swift:168` | `padding: 6px 8px` | DIFFERENT | `sticky_note.py:34` `padding: 8px 9px` |
| Nota | corpo | system 13 pt (`NSFont.systemFontSize`), `labelColor` | `Sources/Note/MarkdownTextStorage.swift:11,73-74` | fonte do tema; `color: inherit` | DIFFERENT | `font-size: 13px` |
| Nota | H1 / H2 / H3 | bold, 13+10 = **23**, 13+6 = **19**, 13+3 = **16**; o `#` fica oculto fora da linha do cursor (mono bold `tertiaryLabel` na linha do cursor) | `Sources/Note/MarkdownTextStorage.swift:148-169` | scale 1.55 / 1.32 / 1.15 (= 20.2 / 17.2 / 15) | DIFFERENT | `sticky_note.py:211-213`: `scale` 1.769 / 1.462 / 1.231 (ou `size_points` absoluto) |
| Nota | H4+ | sem estilo (só reconhece `#`–`###`) | `Sources/Note/MarkdownTextStorage.swift:130-135` | não verificado | não verificado | — |
| Nota | citação `> ` | `secondaryLabelColor`, system 13 regular, **sem itálico e sem recuo**; o `> ` fica oculto | `Sources/Note/MarkdownTextStorage.swift:174-190` | itálico, recuo 16, cinza `#5A5A5A` | DIFFERENT | `sticky_note.py:219`: `style` normal, `left_margin=0`, `foreground rgba(0,0,0,.5)` |
| Nota | código inline | mono 12 pt (`fs-1`), **systemOrange**, fundo `textBackground` 30% | `Sources/Note/MarkdownTextStorage.swift:271-277` | mono, só fundo `mono_bg` | DIFFERENT | `sticky_note.py:217`: `foreground #FF9500`, `scale 12/13` |
| Nota | bloco de código | mono 12 pt, **systemTeal** | `Sources/Note/MarkdownTextStorage.swift:200-202` | mono + fundo de parágrafo | DIFFERENT | `sticky_note.py:218`: `foreground #59ADC4`, sem `paragraph_background` |
| Nota | negrito / itálico | system bold / italic 13 | `Sources/Note/MarkdownTextStorage.swift:231,241-247` | weight 700 / style 2 | MATCH | — |
| Nota | tachado | strike + `secondaryLabel` | `Sources/Note/MarkdownTextStorage.swift:259-263` | não verificado | não verificado | `strikethrough=True, foreground rgba(0,0,0,.5)` |
| Nota | `---` | `separatorColor` | `Sources/Note/MarkdownTextStorage.swift:138-139` | `#787878` | DIFFERENT | `rgba(0,0,0,.1)` |
| File tree | casca | `windowBackground` 85% + `.sidebar`; sombra preto **18% r16 y6**; borda separator 0.8 × 0.5 pt | `Sources/Canvas/NodeLayer/FileTreeNodeSwiftUIView.swift:112-121` | tema GTK escuro (roxo `#211A2E`, veja a captura 03) | DIFFERENT | claro: `rgba(236,236,236,.95)`, `box-shadow 0 6px 16px rgba(0,0,0,.18)` |
| File tree | header | altura 32+8 = 40: pílula ‹ › (28×24 cada, 12 pt medium; desabilitado = secondary 35%) com fundo `controlBackground` 90% e sombra preto 12% r3 y1; nome da raiz 13 pt medium; branch `arrow.triangle.branch` 10 pt + 11 pt | `Sources/Canvas/NodeLayer/FileTreeNodeSwiftUIView.swift:138,239-300` | título "Files" + barra separada com ‹ › ^ e alternância de modo | DIFFERENT | fundir num header só de 40 px no layout do macOS |
| File tree | linha | altura **32**; recuo por nível **20**; chevron 16×16 (`chevron.right`/`.down`, secondary); ícone 20×20 (ícone do Finder); +6; nome **13 pt**; subtítulo 10 pt secondary | `Sources/FileTree/FileTreeOutlineAdapter.swift:157-158,496-529,561-562` | `min-height 22px`; ícone 16 px | DIFFERENT | `widget.py:450` `min-height: 32px`; `file_tree_node.py:44` `ICON_PX = 20`; `TreeExpander` com `indent-for-depth` 20 px |
| File tree | status git | **sem letra**: só a cor do nome. modified = systemOrange; added/untracked = systemGreen; deleted = systemRed; resto = label | `Sources/FileTree/FileTreeOutlineAdapter.swift:569-574` | badges de letra M/A/D/R/U/? (`#f59e0b`, `#22c55e`, …) | DIFFERENT | esconder `.maestri-git-badge` (`widget.py:453-456`); colorir o label do nome com `#FF9500`/`#28CD41`/`#FF3B30` |
| File tree | busca | campo com `magnifyingglass` 13 pt; padding 10×7; fundo raio 8 `quaternarySystemFill`; margem h 10; faixa de 40 no rodapé | `Sources/Canvas/NodeLayer/FileTreeNodeSwiftUIView.swift:173-177,383-415` | não verificado | não verificado | SearchEntry no rodapé, raio 8, fundo `rgba(0,0,0,.05)` |
| File tree | painel git | altura 120, animação easeInOut 0.2 s | `Sources/Canvas/NodeLayer/FileTreeNodeSwiftUIView.swift:169,180` | não verificado | não verificado | `Gtk.Revealer` 200 ms |
| Diff | cores | `+` verde, `-` vermelho, contexto `.secondary`; fundo da linha 5% da cor; fonte `.caption` mono (10 pt) | `Sources/Git/DiffViewController.swift:14-18,28-43` | add `#23d18b` com fundo 14%; del `#f14c4c` 14%; hunk azul | DIFFERENT | `diff_view.py:24-28`: add `#28CD41`/fundo `rgba(40,205,65,.05)`; del `#FF3B30`/`.05`; meta e hunk = `rgba(0,0,0,.5)`; 10 px mono |
| Portal | header | ícone `globe`, cor `.blue` | `Sources/Canvas/NodeLayer/PortalNodeSwiftUIView.swift:300-301` | não verificado | não verificado | ícone globe `#007AFF` |
| Portal | barra de navegação | cápsula **branca** com sombra preto 6% r2 y1; botões 26×26, SF 11 pt medium; `chevron.left/right`; `arrow.clockwise` ↔ `xmark` durante o carregamento; desabilitado 35%; padding interno h 4; altura 28; padding externo 8×6 | `Sources/Canvas/NodeLayer/PortalNodeSwiftUIView.swift:83-121` | botões `flat` soltos (`go-previous-symbolic` …) | DIFFERENT | `.maestri-portal .nav { background: white; border-radius: 999px; min-height: 28px; box-shadow: 0 1px 2px rgba(0,0,0,.06); }` |
| Portal | URL | cápsula branca separada, altura 28; `magnifyingglass` 10 pt secondary 12×12; texto system 11 pt | `Sources/Canvas/NodeLayer/PortalNodeSwiftUIView.swift:137-154,174` | `Gtk.Entry` do tema | DIFFERENT | entry sem moldura dentro da cápsula branca, 11 px |
| Portal | vazio | `globe` 36 pt thin `white 0.7`; texto 13 pt `white 0.55`; fundo `white 0.97` (`#F7F7F7`) | `Sources/Canvas/NodeLayer/PortalNodeSwiftUIView.swift:264-273` | não verificado | não verificado | — |
| Corda | cor (idle) | systemGray 70% (`#8E8E93`B3) | `Sources/Canvas/ConnectionLayer/RopePathRenderer.swift:13` | `#8A8A8F` sólido | DIFFERENT (alpha) | `renderer.py:22,108`: `set_source_rgba(0.557,0.557,0.576,0.7)` |
| Corda | estados | comunicando = systemGreen; desconectada = systemRed; erro = systemOrange | `Sources/Canvas/ConnectionLayer/RopePathRenderer.swift:14-16` | não existem (só hot/normal) | MISSING | mapear o status da conexão para as cores |
| Corda | espessura | 1.5 (idle/desconectada/erro); 2.5 (comunicando e hover) | `Sources/Canvas/ConnectionLayer/RopePathRenderer.swift:20-25,80` | 2.5 sempre | DIFFERENT | `renderer.py:109`: 1.5/zoom quando idle |
| Corda | tracejado | **tracejada `[6,4]` em idle/desconectada/erro/hover**; sólida só comunicando | `Sources/Canvas/ConnectionLayer/RopePathRenderer.swift:27-31,83-91` | sólida (tracejada `[6,6]` só se pendente) | DIFFERENT (muito visível) | `renderer.py:110`: `[6/z, 4/z]` exceto quando comunicando |
| Corda | hover | systemBlue, 2.5, tracejada `[6,4]` | `Sources/Canvas/ConnectionLayer/RopePathRenderer.swift:78-87` | ACCENT `#3B82F6` sólida | DIFFERENT | `#007AFF` + `[6,4]` |
| Corda | ponta / junção | round / round | `Sources/Canvas/ConnectionLayer/RopePathRenderer.swift:81-82` | round / round | MATCH | — |
| Corda | arrastando (conectar) | systemBlue 80%, 2 pt, `[6,4]`; ponto de origem cheio; destino systemBlue 50%; borda do alvo systemBlue 40%, 1.5 | `Sources/Canvas/ConnectionLayer/ConnectionOverlayView.swift:242-256,271-272` | ACCENT 2.5, `[8,6]`; anel de 11 + ponto de 5 | DIFFERENT | `renderer.py:207-221`: 2 px, `[6,4]`, `rgba(0,122,255,.8)` |
| Corda | pino / conector | disco systemBlue raio 5 com miolo branco raio 2.5 (só durante a conexão) | `Sources/Canvas/ConnectionLayer/ConnectionOverlayView.swift:282-297` | pino CSS 14 px `#3b82f6` com borda branca 2, aparece no hover | DIFFERENT | `widget.py:449`: 10 px, `#007AFF`, miolo branco de 5 (`radial-gradient`); só em modo conectar |
| Física | pontos de controle | 21 | `Sources/Shared/Constants.swift:20` | 21 | MATCH | — |
| Física | razão de curvatura | média de 1.08 e 1.15 = 1.115; comprimento mínimo 20 | `Sources/Shared/Constants.swift:21-22`; `RopeSimulation.swift:38-39` | `_BEND` igual; `MIN_ROPE_LENGTH = 20` | MATCH | — |
| Física | gravidade / amortecimento / iterações | 0.8 / 0.98 / 5 | `Sources/Canvas/Physics/RopeSimulation.swift:85-89` | 0.8 / 0.98 / 5 (`rope.py:10-12`) | MATCH | — |
| Física | sleep / wake / tick | 0.1 / 0.5 / 1/60 s | `Sources/Canvas/Physics/RopeSimulation.swift:91-95` | 0.1 / 0.5 (`rope.py:13-14`); tick não verificado | MATCH | — |
| Física | queda inicial | `dist*(ratio-1)*1.5` | `Sources/Canvas/Physics/RopeSimulation.swift:248-249` | não verificado | não verificado | — |
| Marquee | estilo | retângulo reto; preenchimento systemBlue 8%; traço systemBlue 40%, 1 pt; só aparece se > 2 px | `Sources/Canvas/Visual/MagneticSnapGuideView.swift:60-66` | preenchimento ACCENT 12%; traço 80%, 1 px | DIFFERENT | `renderer.py:197-199`: `rgba(0,122,255,.08)` / `rgba(0,122,255,.4)` |
| Pré-visualização de desenho | retângulo | raio 4; 1.5 pt; `[4,3]`; systemBlue 60%; preenchimento 5% | `Sources/Canvas/Visual/MagneticSnapGuideView.swift:69-76` | `_dashed_rect` 1.5, `[5,4]`, ACCENT, sem preenchimento | DIFFERENT | `[4,3]`, `rgba(0,122,255,.6)`, preenchimento `.05`, raio 4 |
| Pré-visualização de traço | seta/linha/freehand | 2 pt, round, `[6,4]` **animado** (fase −0.5 a cada 1/30 s); systemBlue 80% | `Sources/Canvas/Visual/MagneticSnapGuideView.swift:40-43,125-128,152-156` | não verificado | não verificado | timer de 33 ms decrementando a fase do dash |
| Guias de snap | linha | systemBlue **70%**, 1 pt, tracejada `[4,3]`, em todo o eixo do encaixe | `Sources/Canvas/Visual/MagneticSnapGuideView.swift:92-96` | **rosa** `#EF4D75` 75%; sólida nas bordas e `[4,3]` só nos centros; cruzes de marca | DIFFERENT | `renderer.py:24-25,170-181`: `GUIDE = (0,0.478,1.0)`, alpha .7, `[4,3]` sempre, sem cruzes |
| Snap (Ctrl/ímã) | limiar / raio | 12 / 600 | `Sources/Canvas/Visual/TileSnapping.swift:10,14` | `SNAP_THRESHOLD 12`, `SNAP_PROXIMITY_RADIUS 600` (`constants.py:16-17`) | MATCH | — |
| Snap automático | existe? | só `TileSnapping`: com **⌘** ao arrastar 1 nó; **sempre** no arrasto em lote | `Sources/Canvas/Interaction/CanvasInteractionHandler.swift:357-358,409` | `alignment_guides.py` (6 px de tela, sempre ligado) | DIFFERENT (extra) | Linux: Ctrl no lugar de ⌘; aplicar sempre no arrasto em lote; guias inteligentes atrás de uma preferência, desligadas por padrão |
| Formas | cor / espessura / estilo padrão | `"blue"` (= `#BAD6F5` pastel da paleta das notas); 2.0; sólido | `Sources/Workspace/Models/NodeContent.swift:354-356` | `"blue"`, 2.0 (`drawn.py:29-31,64`) | MATCH | — |
| Formas | preenchimento | sólido = cor 30%; hachura e hachura cruzada = 10% + linhas a 45° | `Sources/Canvas/NodeLayer/ShapeNodeSwiftUIView.swift:127-142` | 0.3 / 0.1 (`drawn.py:235`) | MATCH | — |
| Formas | tracejado / pontilhado | `[8,4]` / `[2,4]` | `Sources/Canvas/NodeLayer/ShapeNodeSwiftUIView.swift:152-155`; `StrokeNodeSwiftUIView.swift:35-36` | idem (`drawn.py:252`) | MATCH | — |
| Formas | texto interno | cor misturada com 35% de preto | `Sources/Canvas/NodeLayer/ShapeNodeSwiftUIView.swift:97-101` | `rgb*0.65` (`drawn.py:257`) | MATCH | — |
| Formas | alça de rotação | 25 pt acima do topo (linha de 20 + ponto de raio 5); captura 12 | `Sources/Canvas/Interaction/CanvasHitTesting.swift:24-38` | não verificado | não verificado | — |
| Texto | padrões | 18 pt, regular, `#1a1a1a`, esquerda, sans | `Sources/Workspace/Models/NodeContent.swift:320-324` | idem (`drawn.py:61`) | MATCH | — |
| Freehand | caneta / marcador | 3.0 e opacidade 1 / 12.0 e opacidade 0.4 | `Sources/Workspace/Models/NodeContent.swift:419-421` | idem (`drawn.py:72`) | MATCH | — |
| Minimapa | popover | mapa 280×180, padding 12, fundo `white 0.96` (`#F5F5F5`) raio 8 | `Sources/WorkspaceCanvas/CanvasMinimapPopover.swift:12,25,34-36` | 280×180; fundo branco 6% sobre a cápsula escura | DIFFERENT (fundo) | `floating_bars.py:523`: `set_source_rgb(0.96,0.96,0.96)` com raio 8 |
| Minimapa | nós | raio 2; mínimo 6×4; terminal `.blue` (`#007AFF`); nota `(0.6,0.88,0.88)`; portal `(1,0.92,0.6)`; árvore `(1,0.82,0.6)`; texto `(0.7,0.7,0.9)`; forma `(0.9,0.75,0.85)`; traço `(0.59,0.51,0.94)`; freehand `(0.6,0.85,0.75)`; alpha 1 | `Sources/WorkspaceCanvas/CanvasMinimapPopover.swift:51-53,119-137` | mesmas cores; terminal `(0.04,0.52,1.0)` = `#0A85FF` (o azul do modo escuro); alpha 0.9 | DIFFERENT (pequeno) | `floating_bars.py:370`: terminal `(0.0,0.478,1.0)`; alpha 1.0 |
| Minimapa | viewport | só traço `.blue` 60%, 1.5, raio 2, **sem preenchimento** | `Sources/WorkspaceCanvas/CanvasMinimapPopover.swift:61-63` | preenchimento azul 12% + traço 75% 1.5 | DIFFERENT | `floating_bars.py:535-539`: remover o fill; stroke `rgba(0,122,255,.6)` |
| Barra contextual | posição | **fixa no topo central**, 12 pt abaixo da barra principal (que fica a 8 pt do topo); **não segue o nó** | `Sources/WorkspaceCanvas/WorkspaceCanvasView.swift:47-56`; `docs/images/agents.png` | flutua 10 px acima do nó (`context_toolbar.py:295-296,383-394`) | DIFFERENT (muito visível) | ancorar à barra principal: `halign CENTER`, `margin_top = 8 + altura_da_barra + 12` |
| Barra contextual | casca | branco; raio 10; sombra preto 8% r8 y2; borda `white 0.9` (`#E6E6E6`) 0.5 pt; padding 8×5 | `Sources/WorkspaceCanvas/NodeContextToolbar.swift:31-40` | `#fff`; raio 9; borda 1 px `#e3e3e3`; padding 3×6; sombra 0 2 8 12% | DIFFERENT (pequeno) | `context_toolbar.py:45-46`: `border-radius: 10px; padding: 5px 8px; border: 1px solid #E6E6E6; box-shadow: 0 2px 8px rgba(0,0,0,.08)` |
| Barra contextual | botão | 30×30; SF 13 pt regular; hover preto 5%, raio 5 | `Sources/WorkspaceCanvas/ToolbarButton.swift:16-22` | 26×26 mínimo; padding 0 6; hover 6%, raio 6 | DIFFERENT | `context_toolbar.py:47-49`: `min-width/height 30px; padding 0; border-radius 5px; hover rgba(0,0,0,.05)`; ícone 16 px (13 pt SF ≈ glifo de 16 px) |
| Barra contextual | apagar (hover) | vermelho 8%, raio 5 | `Sources/WorkspaceCanvas/ShapeContextToolbar.swift:210-211` | `alpha(#ef4444,.10)` + texto `#dc2626` | DIFFERENT (pequeno) | `rgba(255,59,48,.08)`; o ícone mantém a cor |
| Barra contextual | tooltip | 11 pt; padding 8×4; branco; raio 5; sombra preto 12% r4 y2; entra com opacity + scale 0.9 | `Sources/WorkspaceCanvas/ToolbarButton.swift:49-64` | tooltip padrão do GTK | DIFFERENT | popover sem seta com essas medidas |
| Barra contextual | contador de conexões | círculo `accentColor` 20×20, texto 10 pt semibold branco | `Sources/WorkspaceCanvas/NodeContextToolbar.swift:63-67` | `#0a84ff`, `min-width 12px` (`widget.py:442`) | DIFFERENT | círculo de 20 px `#007AFF`, 10 px 600 |
| Barra contextual | ícones do terminal | `square.and.pencil`, `arrow.trianglehead.branch`, [contador], `arrow.triangle.2.circlepath`, `trash` | `Sources/WorkspaceCanvas/NodeContextToolbar.swift:23-29` | não verificado um a um | não verificado | — |
| Barra contextual | ícones da árvore | `folder.badge.questionmark`, `folder.badge.gearshape`, `trash` | `Sources/WorkspaceCanvas/NodeContextToolbar.swift:162-164` | não verificado | não verificado | — |
| Barra contextual | animação | opacity + move(edge: .top), easeInOut 0.15 s | `Sources/WorkspaceCanvas/WorkspaceCanvasView.swift:177-178` | aparece sem animação | MISSING | `Gtk.Revealer(SLIDE_DOWN, 150 ms)` |
| Constantes | aparecer status de conexão / toast / animação de injeção | 0.15 s / 2.0 s / 0.5 s | `Sources/Shared/Constants.swift:32-34` | não verificado | não verificado | — |

### 1a. Temas de terminal: bloco pronto para `canvas-cairo/maestri_canvas/terminal_themes.py`

Diferenças nos temas que o port já tem:
- `dark` (= maestri-dark): ANSI 15 deve ser `#e5e5e5`→**`#ffffff`**.
- `light` (= maestri-light): fg `#333333`→**`#383a42`**; ANSI 2 `#00bc00`→**`#008000`**; ANSI 7 `#555555`→**`#e5e5e5`**.

`dracula`, `solarized-dark`, `solarized-light`, `nord`, `one-dark` e `tokyo-night` batem com o macOS em fg/bg/16 ANSI. Faltam 7 temas: `tokyo-night-day`, `catppuccin-mocha`, `catppuccin-macchiato`, `catppuccin-latte`, `gruvbox-dark`, `gruvbox-light`, `rose-pine`. O macOS guarda em preferences `"dark"`/`"light"` e resolve para `maestri-dark`/`maestri-light` (`TerminalThemeRegistry.swift:103-121`); mantenha as chaves `"dark"`/`"light"` como aliases. O comentário de cada tema traz o cursor e a seleção, para quando `TerminalTheme` ganhar esses campos.

```python
THEMES: dict[str, TerminalTheme] = {
    # Maestri Dark — Sources/Terminal/TerminalThemeRegistry.swift:129 · cursor #aeafad · selection #264f78
    "maestri-dark": TerminalTheme("#d4d4d4", "#1e1e1e", (
        "#000000", "#cd3131", "#0dbc79", "#e5e510", "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5",
        "#666666", "#f14c4c", "#23d18b", "#f5f543", "#3b8eea", "#d670d6", "#29b8db", "#ffffff")),
    # Maestri Light — Sources/Terminal/TerminalThemeRegistry.swift:154 · cursor #526fff · selection #add6ff
    "maestri-light": TerminalTheme("#383a42", "#ffffff", (
        "#000000", "#cd3131", "#008000", "#949800", "#0451a5", "#bc05bc", "#0598bc", "#e5e5e5",
        "#666666", "#cd3131", "#14ce14", "#b5ba00", "#0451a5", "#bc05bc", "#0598bc", "#a5a5a5")),
    # Dracula — Sources/Terminal/TerminalThemeRegistry.swift:179 · cursor #f8f8f2 · selection #44475a
    "dracula": TerminalTheme("#f8f8f2", "#282a36", (
        "#21222c", "#ff5555", "#50fa7b", "#f1fa8c", "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2",
        "#6272a4", "#ff6e6e", "#69ff94", "#ffffa5", "#d6acff", "#ff92df", "#a4ffff", "#ffffff")),
    # Solarized Dark — Sources/Terminal/TerminalThemeRegistry.swift:204 · cursor #839496 · selection #073642
    "solarized-dark": TerminalTheme("#839496", "#002b36", (
        "#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
        "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3")),
    # Solarized Light — Sources/Terminal/TerminalThemeRegistry.swift:229 · cursor #657b83 · selection #eee8d5
    "solarized-light": TerminalTheme("#657b83", "#fdf6e3", (
        "#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
        "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3")),
    # Nord — Sources/Terminal/TerminalThemeRegistry.swift:254 · cursor #d8dee9 · selection #434c5e
    "nord": TerminalTheme("#d8dee9", "#2e3440", (
        "#3b4252", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
        "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4")),
    # One Dark — Sources/Terminal/TerminalThemeRegistry.swift:279 · cursor #528bff · selection #3e4451
    "one-dark": TerminalTheme("#abb2bf", "#282c34", (
        "#282c34", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#abb2bf",
        "#5c6370", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#ffffff")),
    # Tokyo Night — Sources/Terminal/TerminalThemeRegistry.swift:304 · cursor #c0caf5 · selection #33467c
    "tokyo-night": TerminalTheme("#a9b1d6", "#1a1b26", (
        "#15161e", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
        "#414868", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#c0caf5")),
    # Tokyo Night Day — Sources/Terminal/TerminalThemeRegistry.swift:329 · cursor #3760bf · selection #b6bfe2
    "tokyo-night-day": TerminalTheme("#3760bf", "#e1e2e7", (
        "#e9e9ed", "#f52a65", "#587539", "#8c6c3e", "#2e7de9", "#9854f1", "#007197", "#6172b0",
        "#a1a6c5", "#f52a65", "#587539", "#8c6c3e", "#2e7de9", "#9854f1", "#007197", "#3760bf")),
    # Catppuccin Mocha — Sources/Terminal/TerminalThemeRegistry.swift:354 · cursor #f5e0dc · selection #45475a
    "catppuccin-mocha": TerminalTheme("#cdd6f4", "#1e1e2e", (
        "#45475a", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
        "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af", "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8")),
    # Catppuccin Macchiato — Sources/Terminal/TerminalThemeRegistry.swift:379 · cursor #f4dbd6 · selection #494d64
    "catppuccin-macchiato": TerminalTheme("#cad3f5", "#24273a", (
        "#494d64", "#ed8796", "#a6da95", "#eed49f", "#8aadf4", "#f5bde6", "#8bd5ca", "#b8c0e0",
        "#5b6078", "#ed8796", "#a6da95", "#eed49f", "#8aadf4", "#f5bde6", "#8bd5ca", "#a5adcb")),
    # Catppuccin Latte — Sources/Terminal/TerminalThemeRegistry.swift:404 · cursor #dc8a78 · selection #acb0be
    "catppuccin-latte": TerminalTheme("#4c4f69", "#eff1f5", (
        "#5c5f77", "#d20f39", "#40a02b", "#df8e1d", "#1e66f5", "#ea76cb", "#179299", "#acb0be",
        "#6c6f85", "#d20f39", "#40a02b", "#df8e1d", "#1e66f5", "#ea76cb", "#179299", "#bcc0cc")),
    # Gruvbox Dark — Sources/Terminal/TerminalThemeRegistry.swift:429 · cursor #ebdbb2 · selection #504945
    "gruvbox-dark": TerminalTheme("#ebdbb2", "#282828", (
        "#282828", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#a89984",
        "#928374", "#fb4934", "#b8bb26", "#fabd2f", "#83a598", "#d3869b", "#8ec07c", "#ebdbb2")),
    # Gruvbox Light — Sources/Terminal/TerminalThemeRegistry.swift:454 · cursor #3c3836 · selection #d5c4a1
    "gruvbox-light": TerminalTheme("#3c3836", "#fbf1c7", (
        "#fbf1c7", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#7c6f64",
        "#928374", "#9d0006", "#79740e", "#b57614", "#076678", "#8f3f71", "#427b58", "#3c3836")),
    # Rosé Pine — Sources/Terminal/TerminalThemeRegistry.swift:479 · cursor #524f67 · selection #2a283e
    "rose-pine": TerminalTheme("#e0def4", "#191724", (
        "#26233a", "#eb6f92", "#31748f", "#f6c177", "#9ccfd8", "#c4a7e7", "#ebbcba", "#e0def4",
        "#6e6a86", "#eb6f92", "#31748f", "#f6c177", "#9ccfd8", "#c4a7e7", "#ebbcba", "#e0def4")),
}
THEMES["dark"], THEMES["light"] = THEMES["maestri-dark"], THEMES["maestri-light"]   # prefs keys used by macOS
```

### 1b. Paleta das notas (e das formas: o macOS usa a mesma, `ShapeNodeSwiftUIView.swift:93-95`)

```python
# NoteColorPickerPopover.colorFromString — Sources/WorkspaceCanvas/NoteContextToolbar.swift:287-303
NAMED: dict[str, str] = {
    "yellow": "#FCF7B8", "pink": "#F5BFCC", "blue": "#BAD6F5", "green": "#BAEDCC",
    "orange": "#FCD6AB", "purple": "#DEC4F5", "white": "#F7F7F7",
    "black": "#2E2E2E", "darkgray": "#405261", "darkblue": "#1A2E73",
}
PRESET_ROWS = [["yellow", "pink", "blue", "green", "orange", "purple", "white"], ["black", "darkgray", "darkblue"]]
DEFAULT_COLOR = "#FEFDE8"   # Constants.noteDefaultColor (hex, not a name)
```

Nota: a ordem pedia "7 cores". O macOS tem **10** (7 claras + 3 escuras). `drawn.py:29-31` já tem as 10 corretas; `note_colors.py:9-16` está errado.

## 2. Top 25 divergências por visibilidade

1. **Nota opaca e pastel**, quando no macOS ela é a casca cinza com tinta de 5% no corpo e 18% no header (`NodeShellView.swift:109-114,230-231`; `note_colors.py:66-74`).
2. **Casca de nó escura/roxa** (segue o tema GTK). No macOS é cinza-claro translúcida, independente do tema (`NodeShellView.swift:101-105`; `widget.py:429`).
3. **Grade de pontos de 40 px em `#CCCCCC` sobre `#F2F2F2`**. No macOS são linhas de 16 px, 0.5 px, `#E6E6E6`, sobre `#FFFFFF` (`CanvasBackground.swift`; `renderer.py:20-30,77-91`).
4. **Cordas sólidas de 2.5**. No macOS são tracejadas `[6,4]` de 1.5 em systemGray 70% quando idle, e verdes sólidas quando comunicando (`RopePathRenderer.swift:11-31`).
5. **Barra contextual presa ao nó**. No macOS é fixa no topo central, abaixo da barra principal (`WorkspaceCanvasView.swift:47-56`).
6. **Anel de seleção dentro da borda**, com o dash do GTK. No macOS: 3 pt para fora, raio 13, `[6,4]`, `#007AFF`.
7. **Nós sem sombra** (macOS: preto 15% r10 y3; file tree 18% r16 y6).
8. **File tree no tema escuro, linhas de 22 px, ícones de 16 px**. No macOS: claro, 32 px, ícones de 20 px, recuo de 20.
9. **Badges de letra no git**. No macOS só o nome é colorido (laranja/verde/vermelho).
10. **Header de 28 px sem ícone**. No macOS: 32 px, ícone colorido + título 12 pt medium.
11. **X de fechar no header**, que não existe no macOS.
12. **Footer do terminal com o cwd ausente** (26 px, pasta + caminho mono 10 pt).
13. **Fonte do terminal Monospace 11 pt**. No macOS: SF Mono 13 pt (≈ 13 px).
14. **7 temas de terminal faltando**; cursor e seleção não aplicados.
15. **Paleta de notas errada** (7 hex próprios no lugar dos 10 nomes do macOS) e seletor no rodapé em vez de popover.
16. **Guias de alinhamento rosa, sólidas, com cruzes**. No macOS: azul 70%, `[4,3]`, sem cruzes, só no snap TileSnapping.
17. **Atenção = ícone laranja pulsando**. No macOS: ponto vermelho sólido de 8 px.
18. **Badge de role cinza**. No macOS: cápsula colorida na cor do role, texto branco 9 pt.
19. **Barra do portal com botões soltos**. No macOS: duas cápsulas brancas (nav e URL), 28 px.
20. **Tipografia das notas**: H1/H2/H3 menores (20/17/15 contra 23/19/16), citação em itálico com recuo, código sem laranja/teal.
21. **Marquee mais forte** (12% e 80% contra 8% e 40%).
22. **Corda sendo arrastada**: `[8,6]` 2.5, contra `[6,4]` 2 a 80%; pino de 14 px no hover, contra disco de 10 px só ao conectar.
23. **Minimapa escuro**, contra `#F5F5F5`; viewport preenchido, contra só traço a 60%.
24. **Botões da barra contextual de 26 px**, contra 30 px; padding e raio diferentes; sem tooltip customizado e sem animação de 0.15 s.
25. **Cores do diff** (`#23d18b`/`#f14c4c` com fundo de 14%, contra systemGreen/systemRed com fundo de 5%, mono 10 pt).

## 3. O que não dá para igualar em Cairo/GTK4, e a aproximação

- **Vibrancy `behindWindow`** (`.popover`/`.sidebar`; `NodeShellView.swift:104,229,257`): o GTK4 não desfoca o que está atrás da janela nem atrás de um widget. Use cores sólidas medidas: corpo `rgba(236,236,236,.92)`, header `#E6E8EA`, footer `#EEF0F1`. No escuro: `rgba(50,50,50,.92)`/`#2B2B2D`. A tinta azulada das capturas vem do papel de parede e não é reproduzível.
- **Traços de 0.5 pt** (borda do nó e grade): em tela 1× o GTK arredonda o CSS para 1 px. Na grade, desenhe com Cairo `line_width=0.5`. Na borda, use 1 px com alpha reduzido à metade (`rgba(0,0,0,.06)`).
- **Borda tracejada com padrão e recuo exatos**: o `border-style: dashed` do CSS não controla o padrão nem o recuo. Desenhe o anel no overlay Cairo.
- **SF Symbols** (`terminal`, `note.text`, `globe`, `sparkles`, `person.text.rectangle`, `arrow.triangle.branch`, …): desenhe SVGs outline equivalentes em `gtk4-ui/src/ui/icons/hicolor/scalable/actions/`. Os pesos regular/medium do SF não têm equivalente exato.
- **Fontes SF Pro / SF Mono**: use a fonte sans do sistema (Cantarell/Ubuntu/Inter) e `monospace`. Para igualar as métricas, troque pt por px: 13 pt do macOS = 13 px lógicos. Use `set_absolute_size` ou `font-size: 13px`, nunca `13pt` (que dá 17.3 px a 96 dpi).
- **Ícones de arquivo do Finder** (`NSWorkspace.icon(forFile:)`, `FileTreeOutlineAdapter.swift:579-581`): use `Gio.content_type_get_icon` do tema de ícones; a forma não fica igual.
- **Sombras do SwiftUI** (`radius` = desvio gaussiano): no `box-shadow` do CSS, o blur corresponde a cerca de 2× o desvio. Comece com blur = raio e ajuste a olho. A sombra em widgets com `overflow` cortado exige margem no pai.
- **Transições SwiftUI** (`.scale.combined(with: .opacity)`, `move(edge:)`): use `Gtk.Revealer` (slide/crossfade) ou `Adw.TimedAnimation`, sem spring.

## 4. O que NÃO verifiquei

- **Floors** (fora da área desta ordem): `FloorOverviewView.swift` e `LandingViewController.swift` não foram lidos.
- **Modo escuro do macOS**: o canvas é branco fixo pelo código. Os valores de cor de sistema no escuro vêm da tabela da Apple, não de uma execução. Não há capturas do macOS escuro.
- **Linhas marcadas "não verificado"** na tabela: cadeado, marca de Maestro e ícones da barra contextual no port; tachado, links, H4+ e listas/checkboxes das notas (o `MarkdownTextStorage` não tem estilo de lista ou link e não procurei em `MarkdownRenderer.swift`); busca e painel git do file tree; placeholder e ícone do portal; tick e queda inicial da corda no port; animação do preview de traço; alça de rotação; constantes de toast e injeção.
- **Widths das cordas**: assumi que o `ConnectionOverlayView` desenha em espaço de tela (os widths não escalam com o zoom), o que coincide com o port (`/vp.zoom`). Não confirmei a transformação.
- **`quaternarySystemFill` e `systemTeal`**: os valores são da tabela da Apple, sem medição.
- **Barras inferior e superior, sidebar, menus e diálogos**: ficam para `docs/design/macos-tokens-chrome.md`, que não foi feito.
- **Dimensões absolutas das capturas do macOS**: elas são retina 2× e foram feitas em zoom de 48–111%. Usei-as só para conferir a forma e medir cor (headers ≈ `#E0E4E5`, corpo da nota ≈ `#E3EEEE`), nunca para tamanhos.
- **Port com nós terminal e portal**: não renderizei. Nas capturas novas há só nota e file tree; o terminal foi conferido em `artifacts/e2e-screenshots/02_canvas_with_all_node_types.png`.
