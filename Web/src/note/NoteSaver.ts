import type { NotesApi } from "../net/api";

export type SaveState = "idle" | "dirty" | "saving" | "saved" | "error";

export interface NoteSaverOptions {
  debounceMs?: number;
  onState?: (s: SaveState, err?: unknown) => void;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (h: unknown) => void;
}

/**
 * Debounced PUT of a note. Only the latest text is ever sent; if the user types while a
 * PUT is in flight, a follow-up PUT is made afterwards. flush() forces a save now
 * (blur / close) and resolves when nothing is pending.
 */
export class NoteSaver {
  private pending: string | null = null;
  private timer: unknown = null;
  private inFlight: Promise<void> | null = null;
  private readonly ms: number;
  private readonly set: (fn: () => void, ms: number) => unknown;
  private readonly clear: (h: unknown) => void;

  constructor(private name: string, private api: NotesApi, private opts: NoteSaverOptions = {}) {
    this.ms = opts.debounceMs ?? 600;
    this.set = opts.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clear = opts.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>));
  }

  schedule(text: string): void {
    this.pending = text;
    this.opts.onState?.("dirty");
    if (this.timer !== null) this.clear(this.timer);
    this.timer = this.set(() => { this.timer = null; void this.run(); }, this.ms);
  }

  async flush(): Promise<void> {
    if (this.timer !== null) { this.clear(this.timer); this.timer = null; }
    await this.run();
  }

  private async run(): Promise<void> {
    if (this.inFlight) { await this.inFlight; } // serialize PUTs
    while (this.pending !== null) {
      const text = this.pending;
      this.pending = null;
      this.opts.onState?.("saving");
      this.inFlight = this.api.put(this.name, text);
      try {
        await this.inFlight;
        this.opts.onState?.(this.pending === null ? "saved" : "dirty");
      } catch (err) {
        if (this.pending === null) this.pending = text; // retry with the next edit/flush
        this.opts.onState?.("error", err);
        this.inFlight = null;
        return;
      }
      this.inFlight = null;
    }
  }
}
