# Sprint 4 (GTK4): Floors, Routines e Remote SSH: spec

- **Data:** 2026-11-05 · **Branch:** `feat/linux-port` · **Autor:** Arquiteto
- **Referências:** `docs/reference/official/09-floors.md`, `11-routines.md`, `12-ssh.md`, `docs/reference/maestri-reference-index.md` §9, §11, §12
- **Implementação macOS de referência:** `Sources/Floor/`, `Sources/Routine/`, `Sources/SSH/`, `InterAgentServer.swift` (`/api/floors`)

## 0. Regras transversais (valem para as três features)

1. **O core é dono do estado; a GTK é cliente.** Toda mutação persistida passa pela REST do core (`maestri_core/client/api.py`). A GTK não escreve `workspace.json` direto.
2. **Nenhuma I/O na thread principal.** Use `Gio.Subprocess` (padrão de `maestri_core/gitops.py`, só argv, sem shell) ou uma worker thread com `GLib.idle_add`. Valide refs com `_valid_ref`.
3. **Campos do servidor não podem ser sobrescritos pelo sync** (ver R1 na §4). `floors`, `routines` e `ssh` são mutados pelo servidor. Depois de cada `POST`/`DELETE` a GTK aplica a resposta em `self.document.payload` **na main thread** antes do próximo `WorkspaceSync.flush()`.
4. **Compatibilidade de formato:** não mudar os nomes de campo do Swift (`FloorEntry`, `Routine`, `RoutinesContainer`, que escreve `payload`, e `SSHConfig`).
5. **Env vars de hook:** a referência usa `MAESTRI_*`, mas `FloorManager.runHooks` exporta `OMAESTRI_*`. No Linux exportamos **as duas** (`MAESTRI_FLOOR_NAME`, `MAESTRI_BRANCH_NAME`, `MAESTRI_FLOOR_PATH`, `MAESTRI_ROOT_PATH`, `MAESTRI_PROJECT_NAME` e os aliases `OMAESTRI_*`).

## 1. Floors (git worktree)

**Linux não tem APFS/clonefile.** Um floor é um `git worktree`, que compartilha o object store e por isso é barato. Copiar arquivos não versionados (`node_modules` etc.) fica fora do escopo. Opcionalmente usar `cp --reflink=auto` num hook Setup.

### Modelo (já existe: `FloorEntry` em `doc.payload.floors`)
`id, name, branchName, worktreePath, hooks{setup[],run[],teardown[],autoRunSetup}, createdAt`

### Caminho do worktree
O Swift usa `<wd>/.open-maestri/floors/<name>`, **dentro** do repositório. Isso faz o worktree aparecer como untracked no Ground. No Linux:
- Usar slug: `name` → `[a-z0-9-]`, sem `..` nem `/`.
- Path = `<wd>/.open-maestri/floors/<slug>`. Ao criar o primeiro floor, acrescentar `/.open-maestri/` em `.git/info/exclude` (idempotente).
- Branch padrão = `floor/<slug>`. O servidor hoje usa `floor/<name>` cru, e um nome com espaço gera ref inválida, **bug a corrigir no core**.

### API do core (completar)
| Método | Rota | Ação |
|---|---|---|
| GET | `/api/floors?workspace=` | lista (existe) |
| POST | `/api/floors` | `{name, branchName?, baseBranch?, existingBranch?: bool, cloneGroundLayout?: bool}` → `git worktree add [-b <branch>] <path> [<base>]`. Se `autoRunSetup`, roda os hooks Setup |
| DELETE | `/api/floors/{id}?deleteBranch=0\|1` | roda Teardown → `git worktree remove --force` → `git branch -D` (opcional). Se for o último floor, apaga o diretório `floors/` |
| PATCH | `/api/floors/{id}` | renomear e editar hooks |
| GET | `/api/floors/{id}/status` | `git status --porcelain`, `git diff --shortstat <target>...<branch>`, `dirty: bool` |
| GET | `/api/floors/{id}/land-preview?target=` | `git merge-tree --write-tree <target> <branch>` → `{conflicts:[paths], stat}` |
| POST | `/api/floors/{id}/land` | `{target}`: recusa se `dirty`. Mesmo branch: `fetch`. Branch diferente: merge via plumbing (`merge-tree` + `commit-tree` + `update-ref`), **sem tocar no working tree do Ground**. O Swift atual faz `git merge` no Ground, o que é divergência |
| POST | `/api/floors/{id}/hooks/{setup\|run\|teardown}` | executa os hooks em sequência e devolve um stream de saída por `/ws/events` (`floorHookOutput`) |

Os hooks rodam com `Process`/`Gio.Subprocess` **sem** `waitUntilExit` na thread do servidor, com timeout de 10 min por comando e `cwd = worktreePath`.

### UI GTK4
- **Seção FLOORS da sidebar** (`sidebar.py`): `Gtk.ListBox` com um row por floor (nome, branch, badge `+N −M`, ● se dirty) e um row fixo "Ground". Hoje o "🌿 main (active)" está hardcoded e precisa ser substituído por `client.get_floors()`.
- **Botão de floor** no canto inferior direito do canvas (`Gtk.MenuButton` em overlay sobre o `CanvasWidget`, ao lado do minimap): abre um `Gtk.Popover` com a lista, "Novo floor…", ⚡ hooks (agrupados, "Run All") e o menu de contexto (Rename, Configure Hooks…, Delete). A visão 3D da referência **não** entra: o popover substitui.
- **Novo floor:** `Gtk.Window` modal (sem libadwaita) com nome, `Gtk.DropDown` novo/existente, branch base e `Gtk.CheckButton` "Clonar layout do Ground".
- **Land:** diálogo modal com origem → ✈ → destino (`Gtk.DropDown`), preview (stat + conflitos em vermelho) e botão **Merge** desabilitado quando há conflitos ou dirty.
- **Delete:** `Gtk.AlertDialog` com os botões "Manter branch" / "Apagar branch" / Cancelar.
- **Trocar de floor** = trocar o `working_directory` efetivo: terminais novos nascem com `cwd = worktreePath` e o env `MAESTRI_FLOOR_*`. O layout de cada floor é um documento separado. *Clonar layout* copia os nós do Ground com novos UUIDs.

### Aceite
- pytest: argv builders (slug, branch, worktree add/remove, merge-tree) puros, sem git real. Integração com repo temporário: criar → commit no floor → land mesmo-branch → delete com e sem branch.
- swift test: `FloorManagerTests` cobrindo slug e env vars duplas.

## 2. Routines

### Modelo (já existe: `Routine`, arquivo `routines.json` com chave `payload`)
`id, name, prompts[], intervalSeconds, targetTerminalId, isActive, createdAt`.
**Parsing do `&&`:** a referência diz "`&&` em linha própria". O Swift faz `split("&&")` em qualquer posição, o que quebra prompts como `make && make test`. No Linux: separar só em linhas cujo `strip() == "&&"`. É preciso corrigir também `Routine.init` no Swift, com teste de regressão.

### Agendador
Fica **no core**, não na GTK, para continuar rodando com a janela fechada (`RoutineScheduler` já existe). Requisitos:
- Envio: `writeLine` para o PTY do terminal alvo. O próximo prompt só sai depois de `isIdle`, com timeout de 300 s, igual ao Swift.
- **Sem sobreposição:** se a execução anterior ainda estiver rodando quando o timer dispara, pular o tick e registrar no log. O Swift não protege contra isso.
- Se o terminal alvo não existir mais, a routine é pausada automaticamente e o evento `routineFailed` é emitido.
- Intervalo mínimo de 60 s.

### API / CLI
`GET/POST /api/routines`, `PATCH/DELETE /api/routines/{id}`, `POST /api/routines/{id}/{pause|resume|run-now}`. Eventos em `/ws/events`: `routineStarted`, `routineStep{i,n}`, `routineFinished`, `routineFailed`. A CLI `omaestri routine …` precisa ficar alinhada à skill `maestri-routines`.

### UI GTK4
- Menu **File → Routines…** (`win.routines`, acelerador `<Control><Shift>r`) abre a `RoutinesWindow` (`Gtk.Window` não modal).
- `Gtk.ListView` com nome, alvo, "a cada Xm", um `Gtk.Switch` (pause/resume), um indicador ● pulsante (CSS `@keyframes`) enquanto há execução ativa e os botões editar e apagar.
- Editor com `Gtk.TextView` para o prompt (dica sobre `&&` em linha própria), `Gtk.SpinButton` mais `Gtk.DropDown` (min/h) para o intervalo e `Gtk.DropDown` com os terminais do workspace atual.
- No canvas, o header do terminal alvo ganha um badge ⏱ via o protocolo `header_right` do `NodeFrame`.

### Aceite
pytest com o parser de `&&` (linha própria versus inline), o agendador com clock falso (sem sobreposição, auto-pause) e o round-trip `routines.json` ⇄ Swift.

## 3. Remote SSH

### Modelo
`SSHConfig{host,user,port=22,scriptPath="~/.local/bin/maestri",tunnelPort=7433,addToPath}`. A configuração global (`enabled`, `tunnelPort`) fica em `preferences.json`. A configuração por workspace ou terminal vai no conteúdo do nó terminal (campo `ssh`, opcional).

### Core (Linux)
- `ssh` resolvido via `PATH` (não `/usr/bin/ssh` fixo), com `-o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o StrictHostKeyChecking=accept-new` (a referência pede auto-accept na primeira conexão e aviso quando a chave muda, que é exatamente o comportamento do `accept-new`).
- Túnel: `-N -R 127.0.0.1:<tunnelPort>:127.0.0.1:<localPort>`, com bind explícito em loopback. **Um túnel por host**, com reconnect e backoff exponencial (1 s até 60 s).
- **Correção de segurança no instalador:** `installOmaestri` monta `mkdir -p <dir> && echo <b64> | base64 -d > <path>` com `scriptPath` sem quoting, e um path com `;` ou espaço injeta comando remoto. Corrigir com `shlex.quote` / quoting POSIX e enviar o script via stdin (`ssh … 'cat > "$p"'`). O `addToPath` deve ser idempotente (fazer `grep -q` antes de dar append).
- **Autenticação no túnel:** o script remoto hoje não manda `X-Terminal-ID` nem token. Qualquer processo remoto com acesso a `127.0.0.1:7433` fala com o core. É preciso exigir um token por conexão SSH (header `Authorization`), gravado no script com `chmod 700`.
- Terminal SSH: o VTE spawna `ssh -t user@host -p port` com env `MAESTRI_SERVER_PORT=<tunnelPort>` exportado remotamente (`SendEnv` não é confiável, então injetar via comando).

### UI GTK4
- `settings_window.py`, aba Geral: seção "Remote SSH" com `Gtk.Switch` "Habilitar workspaces SSH" e `Gtk.SpinButton` para a porta do túnel.
- Diálogo "Editar terminal": expander "SSH" com Host, User, Port, Script Path e "Adicionar ao PATH".
- O header do terminal mostra um badge 🔗 `user@host` com estado (🟢 túnel up, 🟡 reconectando, 🔴 falha).

### Aceite
pytest para o argv do ssh (bind em loopback, porta, flags) e o quoting do instalador com paths hostis (`a b;rm -rf ~`). Integração opcional contra `sshd` local em container, que não entra no CI padrão.

## 4. Revisão: `window.py` e `widget.py` (conflitos de edição simultânea)

Achados ordenados por severidade. **R1 bloqueia a Sprint 4.**

| # | Arquivo | Achado | Correção proposta |
|---|---|---|---|
| R1 | `window.py` + `sync.py` + `/api/floors` | **Lost update.** `POST /api/floors` faz load → append → save de `workspace.json` no servidor. Depois disso o `WorkspaceSync` da GTK envia o documento inteiro em memória, ainda com o `floors` antigo, e **apaga o floor criado**. O mesmo vale para qualquer campo que o servidor muta (routines, ssh) | Opção A: o snapshot do sync omite `floors`/`crossFloorConnections` e o servidor faz merge desses campos. Opção B: a GTK aplica a resposta do POST em `document.payload.floors` na main thread. **Recomendado: A**, porque protege também o CLI e outros clientes |
| R2 | `window.py:460` | `_on_canvas_frame_changed` chama `self.sync.flush()` sem checar `None`. No modo "Token Inválido" (`auth_failed`, commit 78ee506) arrastar um nó levanta `AttributeError`. O ramo `auth_failed` e o ramo de frame foram editados em paralelo | `if final and self.sync is not None:` |
| R3 | `window.py:585-596` | `_apply_workspace_switched` não chama `_configure_notes_backend()`, então `canvas_widget.notes_dir` continua apontando para `workspaces/<id antigo>/notes`. Imagens coladas em notas vão para o workspace errado | Chamar `self._configure_notes_backend()` antes de `_load_payload_into_canvas()` |
| R4 | `window.py:492-499` | `target_list` é capturado antes da thread. Se o workspace troca enquanto o POST está em voo, a conexão é anexada ao documento **antigo**. Trocas rápidas também podem aplicar `doc`s fora de ordem | Guardar `ws_id` e descartar o resultado em `_apply_*` se `self.document.payload.id != ws_id`. Para troca de workspace, usar um contador de geração |
| R5 | `window.py:352-363` | Código morto de merge: `"portal"` entrou na tupla do early-return (commit 147d429), mas o ramo `if node.node_type == "portal"` e a docstring ("Portal … keep a placeholder") ficaram | Remover o ramo e atualizar a docstring |
| R6 | `window.py:473-486` + `widget.py:546-552` | `plan_connection` roda duas vezes: no widget, que recusa, e de novo na janela. Inofensivo hoje, mas as duas cópias podem divergir quando alguém editar só uma | A janela confia no evento do widget e só chama `plan_connection` para obter `kind`/`list_attr`. O ramo `isinstance(plan, str)` da janela vira `assert` |
| R7 | `widget.py:442-447` | `remove_node` detecta Portal por `hasattr(content, "web")` (duck typing frágil), ao lado de `isinstance` para StickyNote | Protocolo único: `getattr(content, "dispose", None)` para todos os tipos de conteúdo |
| R8 | `widget.py:589` | `_place_nodes` indexa `self.scene.nodes[i]` para cada frame. Se alguém remover um nó só da cena (fora de `remove_node`), o tick lança `KeyError` a cada frame | `item = self.scene.nodes.get(i)` e `continue` quando for `None` |

**Coordenação para evitar novos conflitos nestes dois arquivos:** as features da Sprint 4 **não** devem crescer `window.py`, que já tem cerca de 600 linhas. Criar `gtk4-ui/src/ui/floors.py`, `routines_window.py` e `ssh_settings.py`. Em `window.py` entram só três pontos de ligação: uma action no menu, o overlay do botão de floor e o binding. Em `widget.py` só entra a API pública `add_overlay_control()`, sem lógica de feature.
