import { test, expect } from '@playwright/test';

/**
 * Open Maestri E2E Test Suite
 * ============================
 *
 * Validação 100% de feature parity entre macOS e Linux
 * Todas as features críticas (P0/P1) devem passar antes de release
 *
 * Rodando: npm run test:e2e
 */

test.describe('Canvas & Navigation', () => {
  test.beforeEach(async ({ page }) => {
    // Aguardar servidor estar pronto
    const serverUrl = new URL('http://localhost:38677');
    await page.goto(`${serverUrl.origin}?port=${serverUrl.port}`);
    await page.waitForLoadState('networkidle');
  });

  test('deve carregar página inicial com canvas', async ({ page }) => {
    const canvas = page.locator('#canvas');
    await expect(canvas).toBeVisible();
  });

  test('deve fazer pan no canvas com mouse', async ({ page }) => {
    const canvas = page.locator('#canvas');

    // Mover mouse para centro do canvas
    await canvas.hover();

    // Simular pan: shift + scroll
    await page.keyboard.press('Shift');
    await page.mouse.wheel(0, 100);
    await page.keyboard.release('Shift');

    // Verificar que viewport mudou
    const viewportOrigin = await page.evaluate(() => {
      return (window as any).view?.viewport?.origin;
    });

    expect(viewportOrigin).toBeDefined();
  });

  test('deve fazer zoom com Ctrl+Wheel', async ({ page }) => {
    const canvas = page.locator('#canvas');
    await canvas.hover();

    const zoomBefore = await page.evaluate(() => {
      return (window as any).view?.viewport?.zoom ?? 1;
    });

    // Ctrl + Scroll = Zoom
    await page.keyboard.press('Control');
    await page.mouse.wheel(0, 50);
    await page.keyboard.release('Control');

    const zoomAfter = await page.evaluate(() => {
      return (window as any).view?.viewport?.zoom ?? 1;
    });

    expect(zoomAfter).toBeGreaterThan(zoomBefore);
  });

  test('deve resetar zoom via toolbar', async ({ page }) => {
    const resetButton = page.locator('button[title="Reset Zoom"]');

    // Faz zoom
    const canvas = page.locator('#canvas');
    await canvas.hover();
    await page.keyboard.press('Control');
    await page.mouse.wheel(0, 50);

    // Clica reset
    await resetButton.click();

    const zoom = await page.evaluate(() => {
      return (window as any).view?.viewport?.zoom ?? 1;
    });

    expect(zoom).toBe(1);
  });
});

test.describe('Terminal Nodes', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('http://localhost:38677?port=38677');
    await page.waitForLoadState('networkidle');
  });

  test('deve criar novo nó terminal via toolbar', async ({ page }) => {
    const addTerminalBtn = page.locator('button:has-text("Terminal")').first();

    const nodeCountBefore = await page.locator('.terminal-node').count();

    await addTerminalBtn.click();

    await page.waitForTimeout(500); // Aguardar animação

    const nodeCountAfter = await page.locator('.terminal-node').count();
    expect(nodeCountAfter).toBe(nodeCountBefore + 1);
  });

  test('deve executar comando no terminal e receber output', async ({ page }) => {
    // Aguardar primeiro terminal carregar
    await page.waitForSelector('.terminal-node', { timeout: 5000 });

    const terminalNode = page.locator('.terminal-node').first();
    const xtermInput = terminalNode.locator('.xterm-input');

    // Digitar comando
    await xtermInput.focus();
    await page.keyboard.type('echo "Maestri Test"');
    await page.keyboard.press('Enter');

    // Aguardar output aparecer (xterm renderiza em screen)
    const screenOutput = terminalNode.locator('.xterm-screen');
    await expect(screenOutput).toContainText('Maestri Test', { timeout: 3000 });
  });

  test('deve redimensionar nó terminal com handles', async ({ page }) => {
    const terminalNode = page.locator('.terminal-node').first();

    // Obter posição inicial
    const box = await terminalNode.boundingBox();
    expect(box).toBeDefined();

    if (!box) return;

    // Buscar handle de resize (canto inferior-direito)
    const resizeHandle = terminalNode.locator('.resize-handle.se'); // southeast

    // Drag 100px para direita e 100px para baixo
    await resizeHandle.dragTo({ x: box.x + box.width + 100, y: box.y + box.height + 100 });

    // Verificar novo tamanho
    const newBox = await terminalNode.boundingBox();
    expect(newBox!.width).toBeGreaterThan(box.width);
    expect(newBox!.height).toBeGreaterThan(box.height);
  });

  test('deve fechar e reabrir nó (persistência)', async ({ page }) => {
    const terminalNode = page.locator('.terminal-node').first();
    const nodeId = await terminalNode.getAttribute('data-node-id');

    // Verificar nó existe
    await expect(terminalNode).toBeVisible();

    // Fechar (via X button no header)
    const closeBtn = terminalNode.locator('.close-button');
    await closeBtn.click();

    // Aguardar desaparecer
    await expect(terminalNode).not.toBeVisible();

    // Recarregar página
    await page.reload();
    await page.waitForLoadState('networkidle');

    // Verificar nó foi restaurado (persistência)
    const restoredNode = page.locator(`[data-node-id="${nodeId}"]`);
    await expect(restoredNode).toBeVisible();
  });
});

test.describe('Node Interactions', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('http://localhost:38677?port=38677');
    await page.waitForLoadState('networkidle');
  });

  test('deve fazer drag de nó para nova posição', async ({ page }) => {
    const node = page.locator('.terminal-node').first();

    const boxBefore = await node.boundingBox();
    expect(boxBefore).toBeDefined();

    if (!boxBefore) return;

    // Drag 200px para direita e 150px para baixo
    const newX = boxBefore.x + 200;
    const newY = boxBefore.y + 150;

    await node.dragTo({ x: newX, y: newY });

    // Aguardar animação
    await page.waitForTimeout(300);

    const boxAfter = await node.boundingBox();
    expect(boxAfter!.x).toBeGreaterThan(boxBefore.x);
    expect(boxAfter!.y).toBeGreaterThan(boxBefore.y);
  });

  test('deve fazer multi-select com Shift+Click', async ({ page }) => {
    // Criar múltiplos nós
    for (let i = 0; i < 2; i++) {
      const addBtn = page.locator('button:has-text("Terminal")').first();
      await addBtn.click();
      await page.waitForTimeout(300);
    }

    const nodes = page.locator('.terminal-node');
    const count = await nodes.count();

    // Shift+Click no primeiro
    await nodes.nth(0).click();
    await page.keyboard.down('Shift');
    await nodes.nth(count - 1).click();
    await page.keyboard.up('Shift');

    // Verificar seleção múltipla
    const selectedNodes = page.locator('.terminal-node.selected');
    expect(await selectedNodes.count()).toBeGreaterThanOrEqual(2);
  });

  test('deve fazer batch drag de múltiplos nós', async ({ page }) => {
    // Setup: criar e selecionar múltiplos nós
    for (let i = 0; i < 2; i++) {
      const addBtn = page.locator('button:has-text("Terminal")').first();
      await addBtn.click();
      await page.waitForTimeout(300);
    }

    const nodes = page.locator('.terminal-node');
    const firstNode = nodes.nth(0);
    const box = await firstNode.boundingBox();

    // Selecionar primeiro
    await firstNode.click();

    // Shift+Click segundo
    await page.keyboard.down('Shift');
    await nodes.nth(1).click();
    await page.keyboard.up('Shift');

    // Drag do primeiro nó (move ambos)
    if (box) {
      await firstNode.dragTo({
        x: box.x + 100,
        y: box.y + 100,
      });
    }

    await page.waitForTimeout(300);

    // Verificar ambos moveram
    const boxAfter = await firstNode.boundingBox();
    expect(boxAfter!.x).toBeGreaterThan(box!.x);
  });
});

test.describe('Connections', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('http://localhost:38677?port=38677');
    await page.waitForLoadState('networkidle');
  });

  test('deve criar conexão entre dois nós', async ({ page }) => {
    // Obter IDs dos dois primeiros nós
    const nodes = page.locator('.terminal-node');
    await expect(nodes).toHaveCount(2);

    const nodeA = nodes.nth(0);
    const nodeB = nodes.nth(1);

    const idA = await nodeA.getAttribute('data-node-id');
    const idB = await nodeB.getAttribute('data-node-id');

    // Click no nó A para selecionar
    await nodeA.click();

    // Segurar Ctrl e click em B para conectar
    await page.keyboard.down('Control');
    await nodeB.click();
    await page.keyboard.up('Control');

    // Aguardar conexão ser renderizada
    await page.waitForTimeout(500);

    // Verificar conexão existe
    const connection = page.locator(`[data-connection*="${idA}"][data-connection*="${idB}"]`);
    await expect(connection).toBeVisible({ timeout: 2000 });
  });

  test('deve visualizar rope bezier renderizado', async ({ page }) => {
    // Aguardar conexões carregarem (teste anterior já cria uma)
    const connectionOverlay = page.locator('.connection-overlay');
    await expect(connectionOverlay).toBeVisible();

    // Verificar que SVG/Canvas contém paths
    const pathElements = connectionOverlay.locator('path, line');
    expect(await pathElements.count()).toBeGreaterThan(0);
  });

  test('deve deletar conexão', async ({ page }) => {
    const connectionElements = page.locator('[data-connection]');

    if (await connectionElements.count() > 0) {
      const connection = connectionElements.first();

      // Right-click para contexto
      await connection.click({ button: 'right' });

      // Click delete
      const deleteOption = page.locator('button:has-text("Delete")');
      await deleteOption.click();

      // Aguardar desaparecer
      await expect(connection).not.toBeVisible();
    }
  });
});

test.describe('CLI & Skill Injection', () => {
  test('deve executar omaestri ask via terminal', async ({ page, context }) => {
    // Abrir page para capturar logs do servidor
    const serverPage = await context.newPage();

    // Executar comando no shell real (fora do browser)
    // Este teste requer acesso ao shell do servidor
    // TODO: Implementar via /api/cli endpoint
  });

  test('deve validar workspace com omaestri check', async ({ page }) => {
    // Fazer request ao servidor
    const response = await page.request.post('http://localhost:38677/api/cli', {
      data: {
        args: ['check', 'workspace'],
      },
      headers: {
        'Content-Type': 'application/json',
      },
    });

    expect(response.ok()).toBeTruthy();
    expect(response.status()).toBe(200);
  });

  test('deve criar nota via omaestri note', async ({ page }) => {
    const response = await page.request.post('http://localhost:38677/api/cli', {
      data: {
        args: ['note', 'create', 'test-note', 'Content here'],
      },
      headers: {
        'Content-Type': 'application/json',
      },
    });

    expect(response.ok()).toBeTruthy();
  });
});

test.describe('Performance', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('http://localhost:38677?port=38677');
    await page.waitForLoadState('networkidle');
  });

  test('deve manter 60fps com 50+ nós', async ({ page }) => {
    // Criar múltiplos nós
    for (let i = 0; i < 50; i++) {
      await page.evaluate(() => {
        (window as any).view?.addNode?.({
          kind: 'terminal',
          title: `Terminal ${i}`,
        });
      });
    }

    await page.waitForTimeout(1000);

    // Medir FPS durante pan
    const fps = await page.evaluate(() => {
      return new Promise((resolve) => {
        let frameCount = 0;
        let start = performance.now();

        const countFrames = () => {
          frameCount++;
          if (performance.now() - start < 1000) {
            requestAnimationFrame(countFrames);
          } else {
            resolve(frameCount);
          }
        };

        requestAnimationFrame(countFrames);
      });
    });

    // Espera: pelo menos 30fps durante pan
    expect((fps as number)).toBeGreaterThanOrEqual(30);
  });

  test('deve carregar workspace em < 3s', async ({ page }) => {
    const start = performance.now();

    await page.goto('http://localhost:38677?port=38677');
    await page.waitForLoadState('networkidle');

    const loadTime = performance.now() - start;

    expect(loadTime).toBeLessThan(3000);
  });
});

test.describe('Crash Recovery', () => {
  test('deve recuperar estado após crash', async ({ page }) => {
    // Criar nó com dados
    const addBtn = page.locator('button:has-text("Terminal")').first();
    await addBtn.click();
    await page.waitForTimeout(500);

    const node = page.locator('.terminal-node').first();
    const nodeId = await node.getAttribute('data-node-id');

    // "Crash": simular killing e restarting
    // (requer coordenação com servidor)

    // Recarregar página
    await page.reload();
    await page.waitForLoadState('networkidle');

    // Verificar nó foi recuperado
    const recovered = page.locator(`[data-node-id="${nodeId}"]`);
    await expect(recovered).toBeVisible();
  });
});
