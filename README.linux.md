# Maestrix para Linux

Guia de execução, testes e arquitetura da versão Linux do **open-maestri** (branch `feat/linux-port`).

---

## 🛠️ Pré-requisitos no Ubuntu Linux

- **Sistema:** Ubuntu 22.04, 24.04 ou 26.04 (x86_64)
- **Swift:** Swift 6.0.3+ instalado (já configurado no ambiente em `~/.local/bin/swift`)
- **Node.js:** Node.js v20+ e npm (para a Web Shell)
- **Dependências C:** `build-essential`, `libc6-dev` (fornecem `<pty.h>`, `<sys/socket.h>`)

Verifique se as ferramentas estão no PATH:
```bash
swift --version
node --version
npm --version
```

---

## 🖥️ 1. Como Executar o Maestrix como Aplicativo Desktop no Linux

O Maestrix no Linux foi empacotado como um executável de desktop nativo (GTK 4 + Cairo) integrado ao backend Swift Core e terminais PTY:

### 1.1 Instalação rápida no sistema (user space)
```bash
./scripts/install-desktop.sh
```
Isso instala:
- O executável `open-maestri` em `~/.local/bin/maestrix`
- O CLI `omaestri` em `~/.local/bin/maestrix-cli`
- O daemon `open-maestri-server` em `~/.local/bin/maestrix-server`
- O ícone oficial de alta resolução em `~/.local/share/icons/hicolor/512x512/apps/maestrix.png`
- O atalho `.desktop` em `~/.local/share/applications/maestrix.desktop` (aparece no menu de aplicativos do Ubuntu/GNOME)

### 1.2 Rodar pelo terminal ou menu
```bash
# Rodar direto pelo terminal (já no PATH):
open-maestri

# Ou buscar por "Maestrix" no Dash / menu de aplicativos do Ubuntu
```

Ao abrir, a janela nativa do Maestrix carrega o Canvas interativo, inicia o Core Server e disponibiliza o socket Unix em `~/.open-maestri/run/agent.sock`.

Pressione `F12` dentro do app a qualquer momento para abrir o Developer Tools / Inspector do WebKit.

---

## 🚀 2. Como Rodar os Testes Unitários do Core Swift

Todos os 27 testes do **OpenMaestriCore** (Workspace, Persistência atômica, Formato de frames, Migrações e Servidor POSIX/PTY) rodam via SwiftPM:

```bash
# Na raiz do repositório (~/open-maestri-linux)
swift test -Xswiftc -use-ld=lld
```

**Resultado esperado:**
- `Test Suite 'All tests' passed`
- `Executed 27 tests, with 0 failures (0 unexpected)`

---

## 💻 3. Como Compilar e Usar o CLI `omaestri` Nativo

O CLI `omaestri` opera sobre sockets POSIX (`AF_UNIX` em `~/.open-maestri/run/agent.sock`):

```bash
# Compilar o binário
swift build --product omaestri -Xswiftc -use-ld=lld

# Visualizar o menu de ajuda do CLI compilado
omaestri --help

# Testar comunicação com o daemon/servidor
omaestri list
```

---

## 🎨 4. Como Rodar e Testar a Web Shell (Canvas + Terminais)

A camada de apresentação do Linux fica dentro do diretório `Web/` e implementa o canvas com renderização via Canvas2D + emulação de terminais via `@xterm/xterm`.

### 3.1 Rodar os testes de física e geometria
```bash
cd Web
npm test
```
**Resultado esperado:**
- `Test Files 1 passed (1)`
- `Tests 6 passed (6)` (testes de física de cordas Verlet, snapping de nós e round-trip do frame `[[x, y], [w, h]]`).

### 3.2 Iniciar o servidor de desenvolvimento do Canvas
```bash
cd Web
npm run dev
```
Acesse a URL exibida no terminal (normalmente `http://localhost:5173`) no seu navegador para interagir com o canvas em tempo real.

### 3.3 Gerar o bundle de produção
```bash
cd Web
npm run build
```

---

## 🏛️ Estrutura da Arquitetura Linux

```
open-maestri-linux/
├── Sources/
│   ├── Workspace/Models/       # CanvasNode, NodeContent, schemaVersion: 2
│   ├── Workspace/Persistence/  # PersistenceManager com rename(2) atômico POSIX
│   ├── InterAgent/             # InterAgentServer (HTTP POSIX porta dinâmica + Unix Socket)
│   ├── Terminal/               # POSIXPtyProcess (<pty.h>) + LinuxTerminalManager
│   └── Shared/                 # PlatformServices, PlatformHooks, LoggingService
├── Web/                        # Frontend Web Shell (TypeScript + Vite + xterm.js)
│   ├── src/canvas/             # Renderização da grade infinita, pan/zoom e z-index
│   ├── src/geometry/           # Física das cordas (RopeSimulation) e Snapping
│   └── src/terminal/           # Componente TerminalPane integrado ao xterm.js
└── Tests/OpenMaestriTests/     # 27 testes unitários nativos para Linux
```
