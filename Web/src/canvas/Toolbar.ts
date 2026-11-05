export interface ToolbarCallbacks {
  onAddTerminal: () => void;
  onAddNote: () => void;
  onResetZoom: () => void;
}

export class Toolbar {
  readonly element: HTMLElement;
  private statusDot: HTMLElement;
  private statusText: HTMLElement;

  constructor(callbacks: ToolbarCallbacks) {
    this.element = document.createElement("div");
    this.element.className = "maestri-toolbar";
    this.element.style.cssText = `
      position: absolute;
      top: 16px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 1000;
      display: flex;
      align-items: center;
      gap: 10px;
      background: rgba(30, 30, 32, 0.88);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 12px;
      padding: 6px 14px;
      box-shadow: 0 4px 18px rgba(0, 0, 0, 0.28);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 13px;
      color: #fff;
      user-select: none;
    `;

    const brand = document.createElement("div");
    brand.style.cssText = "font-weight: 600; display: flex; align-items: center; gap: 6px; margin-right: 6px;";
    brand.innerHTML = `<span>🎼</span><span>Open Maestri</span>`;
    this.element.appendChild(brand);

    const divider1 = this.createDivider();
    this.element.appendChild(divider1);

    const btnTerm = this.createButton("➕ Terminal", "Criar novo terminal bash interativo", () => callbacks.onAddTerminal());
    this.element.appendChild(btnTerm);

    const btnNote = this.createButton("📝 Nota", "Criar nova nota adesiva Markdown", () => callbacks.onAddNote());
    this.element.appendChild(btnNote);

    const btnZoom = this.createButton("🔍 100%", "Restaurar zoom e enquadramento", () => callbacks.onResetZoom());
    this.element.appendChild(btnZoom);

    const divider2 = this.createDivider();
    this.element.appendChild(divider2);

    const statusContainer = document.createElement("div");
    statusContainer.style.cssText = "display: flex; align-items: center; gap: 6px; font-size: 11px; opacity: 0.85;";

    this.statusDot = document.createElement("span");
    this.statusDot.style.cssText = "width: 8px; height: 8px; border-radius: 50%; background: #999;";
    statusContainer.appendChild(this.statusDot);

    this.statusText = document.createElement("span");
    this.statusText.textContent = "Offline";
    statusContainer.appendChild(this.statusText);

    this.element.appendChild(statusContainer);
  }

  setStatus(online: boolean, text?: string) {
    this.statusDot.style.background = online ? "#2ecc71" : "#e74c3c";
    this.statusText.textContent = text ?? (online ? "Core Online" : "Offline");
  }

  private createDivider(): HTMLElement {
    const div = document.createElement("div");
    div.style.cssText = "width: 1px; height: 18px; background: rgba(255, 255, 255, 0.15); margin: 0 2px;";
    return div;
  }

  private createButton(text: string, title: string, onClick: () => void): HTMLButtonElement {
    const btn = document.createElement("button");
    btn.textContent = text;
    btn.title = title;
    btn.style.cssText = `
      background: rgba(255, 255, 255, 0.1);
      border: 1px solid rgba(255, 255, 255, 0.1);
      color: #fff;
      padding: 5px 12px;
      border-radius: 8px;
      cursor: pointer;
      font-size: 12px;
      font-weight: 500;
      transition: background 0.15s, border-color 0.15s;
    `;
    btn.onmouseover = () => {
      btn.style.background = "rgba(255, 255, 255, 0.2)";
      btn.style.borderColor = "rgba(255, 255, 255, 0.25)";
    };
    btn.onmouseout = () => {
      btn.style.background = "rgba(255, 255, 255, 0.1)";
      btn.style.borderColor = "rgba(255, 255, 255, 0.1)";
    };
    btn.onclick = onClick;
    return btn;
  }
}
