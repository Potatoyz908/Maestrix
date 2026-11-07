# Workspaces remotos via SSH — estilo VS Code "Remote-SSH" (ordem do Maestro #2)

Objetivo: o usuário escolhe um host, testa a conexão, escolhe uma pasta remota e abre um workspace cujos **terminais rodam na
máquina remota, nessa pasta** (como o terminal integrado do VS Code Remote-SSH). Referência macOS (somente leitura):
`/home/euller/open-maestri-linux/.upstream-macos-ref/Sources/SSH/` (`SSHManager`, `SSHTunnelService`) e
`.../Terminal/SSHTerminalProvider.swift`. O macOS só guarda UM host global em preferências; aqui é por workspace.

## Fases
- **Fase A (esta entrega):** lista de hosts de `~/.ssh/config` + host manual, teste de conexão, navegador de pasta remota,
  workspace `locationType: "ssh"`, terminais remotos com conexão compartilhada (ControlMaster), selo SSH na sidebar.
- **Fase B (depois, não fazer agora):** túnel reverso (`-R <tunnelPort>:127.0.0.1:<porta do servidor>`) e instalação do script
  `omaestri` no remoto (como o `SSHManager` do macOS), e File Tree remoto.

## Regras (inegociáveis)
1. Git: índice COMPARTILHADO; antes de `git commit --only <arquivo>` rode `git diff <arquivo>` e confirme que só há hunks seus;
   arquivo novo: `git add` antes; sem push; commits pequenos; trailer `Co-Authored-By: Claude <noreply@anthropic.com>`.
2. Testes: só os seus, por filtro, com `OPEN_MAESTRI_DATA_DIR=$(mktemp -d)`; GUI só em Xvfb privado. **Nunca** abrir conexão SSH
   real a um host do usuário em teste: use o executável falso (abaixo). Não tocar `~/.open-maestri`, `~/.ssh` nem processos do usuário.
3. Economia: sem subagentes, grep + trechos, relatório de no máximo 5 linhas com hash REAL (confira com `git log`).
4. Formato Maestri imutável: `workspace.json` schemaVersion 2; os dados SSH entram só como campos OPCIONAIS.

## Modelo de dados (contrato)
Workspace SSH: `locationType: "ssh"` (o macOS já tem esse campo) e um objeto opcional **`ssh`** no payload e na entrada do manifest:
```json
"ssh": { "host": "meu-servidor", "user": "euller", "port": 22, "identityFile": "~/.ssh/id_ed25519", "remotePath": "/home/euller/projeto" }
```
`host` é o alias do `~/.ssh/config` OU `hostname`. `workingDirectory` do workspace = `remotePath`. `user`, `port` e `identityFile` são
opcionais (o `ssh` resolve o resto pelo `~/.ssh/config`).

## Contrato REST (servidor = Codice; cliente = Pixel)
- `GET /api/ssh/hosts` -> `[{"alias","hostName","user","port","identityFile"}]` lido de `~/.ssh/config` (seguir `Include`, ignorar
  `Host *` e padrões com `*`/`?`). Caminho sobrescrevível por `OPEN_MAESTRI_SSH_CONFIG` (para testes).
- `POST /api/ssh/test` body `{"host","user?","port?","identityFile?"}` -> `{"ok":bool,"message":str,"needsPassword":bool}`: roda
  `ssh -o BatchMode=yes -o ConnectTimeout=8 [-p][-i] <alvo> true`; `needsPassword` quando o erro é falta de chave/senha.
- `GET /api/ssh/list-dir?host=..&user=..&port=..&identityFile=..&path=..` -> `{"path":"/abs","entries":[{"name","isDir"}]}` via
  `ssh ... 'cd -- <path> && pwd && ls -1Ap'` (BatchMode). `path` vazio = `~`. Se precisar de senha: `{"needsPassword":true}` e a UI
  cai para um campo de texto.
- `POST /api/workspaces` aceita `locationType:"ssh"` + `ssh:{...}` e persiste (manifest e workspace.json); `GET` devolve o `ssh`.
- `POST /api/terminals` em workspace SSH: o servidor NÃO abre shell local; monta e executa:
  `ssh -tt -o ControlMaster=auto -o ControlPath=<dataDir>/run/ssh-%C -o ControlPersist=10m -o ServerAliveInterval=30 [-p N] [-i F] -- <user@host> <comando remoto>`
  onde o comando remoto é `cd <remotePath> && exec "${SHELL:-bash}" -l` ou, se o nó tiver `command`, `cd <remotePath> && <command>`.
  Sem BatchMode aqui: pedidos de senha/passphrase aparecem no próprio terminal (PTY). A primeira conexão cria o mestre; as seguintes reutilizam.
- Executável: `OPEN_MAESTRI_SSH_BIN` (padrão `ssh`) para o teste usar um falso.

## SEGURANÇA (obrigatório, com teste)
- `host`, `user`, `port`, `identityFile` são validados ANTES de montar o comando: host/user só `[A-Za-z0-9._@:-]` e **nunca começando
  com `-`** (senão `-oProxyCommand=...` vira injeção); `port` inteiro 1-65535; `identityFile` caminho existente, sem começar com `-`.
- O alvo vai depois de `--`. `remotePath` e `command` são escapados com aspas simples (`'` -> `'\''`); nada é passado por `sh -c` local.
- Nenhum segredo (senha) é guardado ou logado.

## Workstreams
### Codice — servidor (Sources/**, hunks mínimos em InterAgentServer/CLIRouter/main.swift; novo `Sources/SSH/` no `coreSources`)
`SSHConfigParser` (hosts), validação, `SSHCommandBuilder` (argv; sem shell local), rotas `/api/ssh/*`, persistência do `ssh` em
workspaces, e o ramo SSH em `POST /api/terminals` (LinuxTerminalManager/POSIXPtyProcess). Teste Swift e/ou e2e com `ssh` FALSO:
um script em `OPEN_MAESTRI_SSH_BIN` que grava o argv recebido; verificar `-tt`, ControlMaster, `--`, `cd`, escape e **que hosts
maliciosos (`-oProxyCommand=x`, `a b`, `;rm`) são rejeitados sem executar nada**. Se houver `sshd` local com chave sem senha,
um teste opcional real em 127.0.0.1 (pular com mensagem clara se não houver).
**Aceite:** `GET /api/ssh/hosts` devolve os hosts de um config de teste; `POST /api/ssh/test` reflete sucesso/falha do ssh falso;
um terminal de workspace SSH executa o argv esperado; entradas inválidas dão erro 400 e não chamam o ssh.

### Pixel — interface (gtk4-ui/src/ui/**; novo `ssh_dialog.py`)
Diálogo **"Connect to Host…"** em 2 passos, estilo VS Code, texto em INGLÊS, ícones outline (`iconography.py`):
1. Lista dos hosts (de `/api/ssh/hosts`) + entrada "Add New SSH Host…" (campo `user@host[:port]`, arquivo de identidade opcional).
   Botão **Test connection** (mostra OK/erro e, se `needsPassword`, avisa que a senha será pedida no terminal).
2. Pasta remota: campo de texto + lista navegável (de `/api/ssh/list-dir`, entra com duplo clique, "Up", "Home"); se
   `needsPassword`, só o campo de texto. Botão **Open**, que cria o workspace SSH e o abre.
Pontos de entrada: menu do "+" da sidebar, menu File e o menu Add. Card do workspace SSH na sidebar com selo/ícone de rede e
`user@host` no lugar do caminho local. Aba/seção "Remote SSH" em Settings só com o que o macOS tem (`sshEnabled`, `sshTunnelPort`
7433, `sshScriptPath`), sem inventar campos. Sem servidor: o item fica desabilitado com mensagem clara (SSH precisa do core).
Modal GTK: antes de `super().close()` faça `set_visible(False); set_modal(False); set_transient_for(None)`.
**Aceite:** teste com cliente falso (MagicMock) em Xvfb: lista os hosts, o teste de conexão mostra o resultado, o passo 2 cria o
workspace com o `ssh` certo e o card mostra o selo; um host com `needsPassword` mostra o aviso.
