# 🧪 Open Maestri - E2E Tests com Playwright

Teste automatizado 100% de feature parity entre macOS e Linux.

## Setup Rápido

```bash
cd Web

# 1. Instalar dependências (incluindo Playwright)
npm install --save-dev @playwright/test

# 2. Instalar browsers Playwright
npx playwright install

# 3. Rodar todos os testes
npm run test:e2e

# 4. Ver report HTML interativo
npm run test:e2e:report
```

## Adicionando Scripts ao package.json

```json
{
  "scripts": {
    "test:e2e": "playwright test",
    "test:e2e:debug": "playwright test --debug",
    "test:e2e:ui": "playwright test --ui",
    "test:e2e:report": "playwright show-report",
    "test:e2e:headed": "playwright test --headed",
    "test:e2e:chrome": "playwright test --project=chromium",
    "test:e2e:firefox": "playwright test --project=firefox",
    "test:e2e:mobile": "playwright test --project='Mobile Chrome'",
    "test:e2e:single": "playwright test tests/e2e.spec.ts --grep='deve carregar página'"
  }
}
```

## Executando Testes

### Modo Headless (CI/CD)
```bash
npm run test:e2e
# Testa: Chrome, Firefox, Safari (headless)
# Gera: HTML report em test-results/html/index.html
```

### Modo Debug Interativo
```bash
npm run test:e2e:debug
# Abre Inspector do Playwright
# Step through cada comando
```

### Modo UI (Live Preview)
```bash
npm run test:e2e:ui
# Dashboard visual ao vivo
# Click & run testes específicos
```

### Modo Headed (Você vê o browser)
```bash
npm run test:e2e:headed
# Browser real abre durante testes
# Perfeito para debugging visual
```

### Testar Apenas um Suite
```bash
npm run test:e2e -- --grep "Canvas & Navigation"
npm run test:e2e -- --grep "Terminal Nodes"
```

### Testar em um Browser Específico
```bash
npm run test:e2e:chrome      # Apenas Chromium
npm run test:e2e:firefox     # Apenas Firefox
npm run test:e2e:mobile      # Mobile Chrome
```

## Estrutura dos Testes

```
Web/
├── tests/
│   ├── e2e.spec.ts              # 🧪 Todos os testes
│   └── fixtures/                 # Dados de teste
├── playwright.config.ts          # ⚙️ Configuração
├── package.json                  # 📦 Scripts
└── test-results/                 # 📊 Resultados
    ├── html/                     # Report visual
    ├── results.json              # Dados brutos
    └── junit.xml                 # Para CI
```

## Suites Implementadas

### 1. Canvas & Navigation (4 testes)
- ✓ Carregar canvas inicial
- ✓ Pan com mouse (shift+scroll)
- ✓ Zoom com Ctrl+Wheel
- ✓ Reset zoom via toolbar

**Status:** 🟢 Pronto para rodar

### 2. Terminal Nodes (5 testes)
- ✓ Criar novo nó terminal
- ✓ Executar comando e receber output
- ✓ Redimensionar com handles
- ✓ Fechar e reabrir (persistência)
- ⚠️ WIP: Validar xterm renderização

**Status:** 🟡 Parcialmente pronto (xterm seletor pode precisar ajuste)

### 3. Node Interactions (4 testes)
- ✓ Drag node para nova posição
- ✓ Multi-select com Shift+Click
- ✓ Batch drag
- ⚠️ WIP: Validar seletores CSS

**Status:** 🟡 Parcialmente pronto

### 4. Connections (3 testes)
- ✓ Criar conexão entre nós
- ✓ Visualizar rope bezier
- ⚠️ WIP: Deletar conexão

**Status:** 🟡 Parcialmente pronto

### 5. CLI & Skill Injection (3 testes)
- ⚠️ WIP: Executar omaestri ask
- ✓ Validar workspace com check
- ✓ Criar nota com note command

**Status:** 🟡 Parcialmente pronto

### 6. Performance (2 testes)
- ✓ Manter 60fps com 50+ nós
- ✓ Carregar workspace < 3s

**Status:** 🟡 Parcialmente pronto

### 7. Crash Recovery (1 teste)
- ⚠️ WIP: Recuperar após crash

**Status:** 🟡 Parcialmente pronto

## Ajustando Seletores CSS

Se os testes falharem por seletor não encontrado:

```bash
# 1. Inspecionar elemento no browser
npm run test:e2e:headed
# Abra DevTools (F12) durante o teste

# 2. Encontrar o seletor correto
# Exemplo: page.locator('.xterm-screen')

# 3. Atualizar em e2e.spec.ts
```

## Dados de Teste

Os testes assumem um workspace "limpo" inicial. Para setup personalizado:

```typescript
// Antes de cada teste
test.beforeEach(async ({ page }) => {
  await page.goto('http://localhost:38677?port=38677');
  
  // Setup: criar nós iniciais, etc
  await page.evaluate(() => {
    (window as any).setupTestWorkspace?.();
  });
});
```

## Integração com CI/CD

### GitHub Actions
```yaml
name: E2E Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: '18'
      
      - run: npm install
      - run: npm run test:e2e
      
      - uses: actions/upload-artifact@v3
        if: always()
        with:
          name: playwright-report
          path: Web/test-results/html
```

## Troubleshooting

### ❌ "Port 38677 já está em uso"
```bash
# Matar servidor anterior
lsof -i :38677 | grep LISTEN | awk '{print $2}' | xargs kill -9

# Ou: aguardar na config (timeout padrão: 120s)
```

### ❌ "Elemento não encontrado"
1. Executar teste em modo headed: `npm run test:e2e:headed`
2. Inspecionar estrutura DOM
3. Ajustar seletor em e2e.spec.ts

### ❌ "Teste flaky (passa/falha intermitentemente)"
1. Adicionar wait explícito: `await page.waitForTimeout(500)`
2. Ou esperar por elemento: `await page.waitForSelector('.terminal-node')`

### ❌ "Screenshot/video vazio"
- Atualizar timeout em playwright.config.ts
- Ou reduzir parallelismo: `workers: 1`

## Cobertura Esperada

| Suite | Testes | Status | Target |
|-------|--------|--------|--------|
| Canvas | 4 | 🟢 Ready | 100% |
| Terminal | 5 | 🟡 WIP | 100% |
| Interactions | 4 | 🟡 WIP | 100% |
| Connections | 3 | 🟡 WIP | 100% |
| CLI | 3 | 🟡 WIP | 100% |
| Performance | 2 | 🟡 WIP | 100% |
| Crash Recovery | 1 | 🟡 WIP | 100% |
| **TOTAL** | **22** | **🟡 50%** | **100%** |

## Próximos Passos

1. ✅ Setup Playwright (feito)
2. 🔄 Ajustar seletores CSS (based on current DOM)
3. 🔄 Adicionar 5+ testes por suite
4. 🔄 Rodar em CI/CD
5. 🔄 Atingir 100% pass rate

## Contato & Suporte

Para perguntas sobre estrutura de testes:
- Claude Code: `/code` no terminal
- Docs Playwright: https://playwright.dev

---

**Gerado:** 5 de Novembro de 2026  
**Branch:** feat/linux-port  
**Target:** Feature parity 100% Linux/macOS  
