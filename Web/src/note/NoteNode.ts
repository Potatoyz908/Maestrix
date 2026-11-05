import type { Rect } from "../geometry/types";
import { DomNode, type DomNodeHost } from "../canvas/DomNode";
import type { NotesApi } from "../net/api";
import { noteNameFromFile } from "../net/api";
import type { StickyNoteContent } from "../net/protocol";
import { NoteSaver, type SaveState } from "./NoteSaver";
import { renderMarkdown } from "./markdown";

export const NOTE_COLORS = ["#FFF3A3", "#FFD6A5", "#FFC8DD", "#CDE7FF", "#CFF5D0"] as const;
const SAVE_LABEL: Record<SaveState, string> = { idle: "", dirty: "●", saving: "saving…", saved: "saved", error: "save failed" };

/** Sticky-note card: color picker, live Markdown editor (debounced PUT /api/notes/:name), preview toggle. */
export class NoteNode extends DomNode {
  private editor = document.createElement("textarea");
  private preview = document.createElement("div");
  private statusEl = document.createElement("span");
  private toggle = document.createElement("button");
  private saver: NoteSaver;
  private loaded = false;
  readonly name: string;

  constructor(
    id: string, private meta: StickyNoteContent, private notes: NotesApi, host: DomNodeHost, getFrame: () => Rect,
    /** Called when color/preview change, so the core can persist the StickyNoteContent. */
    private onMetaChange: (meta: StickyNoteContent) => void,
  ) {
    super(id, noteNameFromFile(meta.fileName), host, getFrame, "nnode");
    // The core appends ".md" itself: send the name WITHOUT the extension.
    this.name = noteNameFromFile(meta.fileName);
    this.saver = new NoteSaver(this.name, notes, { onState: (s, err) => this.onSave(s, err) });

    for (const c of NOTE_COLORS) {
      const b = document.createElement("button");
      b.className = "nnode-swatch dnode-nodrag";
      b.style.background = c;
      b.title = c;
      b.addEventListener("pointerdown", (e) => e.stopPropagation());
      b.addEventListener("click", () => this.setColor(c));
      this.headerLeft.append(b);
    }
    this.toggle.className = "nnode-toggle dnode-nodrag";
    this.toggle.title = "Toggle Markdown preview";
    this.toggle.addEventListener("pointerdown", (e) => e.stopPropagation());
    this.toggle.addEventListener("click", () => this.setPreviewing(!this.meta.isPreviewing));
    this.statusEl.className = "nnode-status";
    this.headerRight.append(this.statusEl, this.toggle);

    this.editor.className = "nnode-editor";
    this.editor.placeholder = "Write Markdown…";
    this.editor.spellcheck = false;
    this.editor.disabled = true; // until the saved text has loaded, so we never overwrite it
    this.preview.className = "nnode-preview";
    this.body.append(this.editor, this.preview);

    this.editor.addEventListener("input", () => { if (this.loaded) this.saver.schedule(this.editor.value); });
    this.editor.addEventListener("blur", () => void this.saver.flush());
    this.editor.addEventListener("keydown", (e) => e.stopPropagation());
    this.applyMeta();
  }

  mount(): void {
    this.notes.get(this.name).then(
      (text) => { this.editor.value = text; this.loaded = true; this.editor.disabled = false; this.refreshPreview(); },
      (err) => { this.statusEl.textContent = "load failed"; console.error("note load", this.name, err); },
    );
  }

  private applyMeta(): void {
    this.el.style.setProperty("--note-color", this.meta.color);
    this.el.style.setProperty("--note-size", `${this.meta.fontSize}px`);
    this.el.classList.toggle("previewing", this.meta.isPreviewing);
    this.toggle.textContent = this.meta.isPreviewing ? "✎" : "👁";
  }

  private setColor(c: string): void { this.meta = { ...this.meta, color: c }; this.applyMeta(); this.onMetaChange(this.meta); }

  private setPreviewing(on: boolean): void {
    this.meta = { ...this.meta, isPreviewing: on };
    if (on) this.refreshPreview();
    this.applyMeta();
    this.onMetaChange(this.meta);
    if (!on) this.editor.focus();
  }

  private refreshPreview(): void { this.preview.innerHTML = renderMarkdown(this.editor.value); } // renderMarkdown escapes HTML first

  private onSave(s: SaveState, err?: unknown): void {
    this.statusEl.textContent = SAVE_LABEL[s];
    this.statusEl.classList.toggle("err", s === "error");
    if (err) console.error("note save", this.name, err);
  }

  override focus(): void { if (!this.meta.isPreviewing) this.editor.focus(); }

  /** Flush pending edits before the node goes away (close / unload). */
  flush(): Promise<void> { return this.saver.flush(); }

  protected onDispose(): void { void this.saver.flush(); }
}
