# open-maestri (Linux Ubuntu Port)

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

## 🚀 1. Como Rodar os Testes Unitários do Core Swift

Todos os 27 testes do **OpenMaestriCore** (Workspace, Persistência atômica, Formato de frames, Migrações e Servidor POSIX/PTY) rodam via SwiftPM:

```bash
# Na raiz do repositório (~/open-maestri-linux)
swift test -Xswiftc -use-ld=lld
```

**Resultado esperado:**
- `Test Suite 'All tests' passed`
- `Executed 27 tests, with 0 failures (0 unexpected)`

---

## 💻 2. Como Compilar e Usar o CLI `omaestri` Nativo

O CLI `omaestri` opera sobre sockets POSIX (`AF_UNIX` em `~/.open-maestri/run/agent.sock`):

```bash
# Compilar o binário
swift build --product omaestri -Xswiftc -use-ld=lld

# Visualizar o menu de ajuda do CLI compilado
.build/x86_64-unknown-linux-gnu/debug/omaestri --help

# Testar comunicação com o daemon/servidor
.build/x86_64-unknown-linux-gnu/debug/omaestri list
```

---

## 🎨 3. Como Rodar e Testar a Web Shell (Canvas + Terminais)

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
