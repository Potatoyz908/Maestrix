export interface ToolbarActions { onAddNote(): void; onAddPortal(): void; onAddFileTree(): void }

/** Floating toolbar. ➕ Note / Portal / FileTree are live; ➕ Terminal needs a core endpoint that spawns PTYs. */
export class Toolbar {
  readonly el = document.createElement("div");
  private toast = document.createElement("span");
  private noteBtn = document.createElement("button");
  private buttons: HTMLButtonElement[] = [];
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(host: HTMLElement, actions: ToolbarActions) {
    this.el.className = "toolbar";
    this.noteBtn.textContent = "➕ Note";
    this.noteBtn.title = "Add a sticky note";
    this.noteBtn.addEventListener("click", () => actions.onAddNote());
    this.el.append(this.noteBtn);
    const add = (label: string, title: string, fn: () => void) => {
      const b = document.createElement("button");
      b.textContent = label; b.title = title;
      b.addEventListener("click", fn);
      this.el.append(b);
      this.buttons.push(b);
    };
    add("➕ Portal", "Add a browser portal", () => actions.onAddPortal());
    add("➕ FileTree", "Add a file explorer", () => actions.onAddFileTree());
    const term = document.createElement("button");
    term.textContent = "➕ Terminal";
    term.disabled = true;
    term.title = "Not available in the web shell yet";
    this.el.append(term);
    this.toast.className = "toast";
    this.el.append(this.toast);
    // Toolbar clicks must not reach the canvas underneath.
    this.el.addEventListener("pointerdown", (e) => e.stopPropagation());
    host.appendChild(this.el);
  }

  setBusy(busy: boolean): void { for (const b of [this.noteBtn, ...this.buttons]) b.disabled = busy; }

  showError(msg: string): void {
    this.toast.textContent = msg;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => (this.toast.textContent = ""), 4000);
  }
}
