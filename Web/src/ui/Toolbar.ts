export interface ToolbarActions { onAddNote(): void }

/** Floating toolbar. Only ➕ Note is live; the rest are placeholders until their nodes exist. */
export class Toolbar {
  readonly el = document.createElement("div");
  private toast = document.createElement("span");
  private noteBtn = document.createElement("button");
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(host: HTMLElement, actions: ToolbarActions) {
    this.el.className = "toolbar";
    this.noteBtn.textContent = "➕ Note";
    this.noteBtn.title = "Add a sticky note";
    this.noteBtn.addEventListener("click", () => actions.onAddNote());
    this.el.append(this.noteBtn);
    for (const label of ["➕ Terminal", "➕ Portal"]) {
      const b = document.createElement("button");
      b.textContent = label;
      b.disabled = true;
      b.title = "Not available in the web shell yet";
      this.el.append(b);
    }
    this.toast.className = "toast";
    this.el.append(this.toast);
    // Toolbar clicks must not reach the canvas underneath.
    this.el.addEventListener("pointerdown", (e) => e.stopPropagation());
    host.appendChild(this.el);
  }

  setBusy(busy: boolean): void { this.noteBtn.disabled = busy; }

  showError(msg: string): void {
    this.toast.textContent = msg;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => (this.toast.textContent = ""), 4000);
  }
}
