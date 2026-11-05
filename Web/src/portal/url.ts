import type { PortalContent } from "../net/protocol";

export type UrlResult = { ok: true; url: string } | { ok: false; reason: string };

const LOCAL = /^(localhost|127\.\d+\.\d+\.\d+|\[::1\]|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|[^/]+\.local)(:\d+)?([/?#]|$)/i;

/**
 * Normalizes what the user typed into an http(s) URL, or rejects it.
 * Only http/https may be embedded (no javascript:, data:, file:, blob:, ...).
 * `blockOrigin`: the shell's own origin is refused, because a same-origin iframe running with
 * allow-scripts + allow-same-origin could reach the shell (and its session token).
 */
export function normalizeUrl(input: string, blockOrigin?: string): UrlResult {
  const raw = input.trim();
  if (!raw) return { ok: false, reason: "empty" };
  if (/\s/.test(raw)) return { ok: false, reason: "not a URL" };

  let candidate = raw;
  const hasScheme = /^[a-z][a-z0-9+.-]*:/i.test(raw) && !/^[^/:]+:\d+([/?#]|$)/.test(raw); // "localhost:3000" is host:port
  if (hasScheme) {
    if (!/^https?:\/\//i.test(raw)) return { ok: false, reason: "only http(s) is allowed" };
  } else {
    const hostLike = LOCAL.test(raw) || /^[^/\s]+\.[a-z]{2,}([:/?#]|$)/i.test(raw) || /^\d+\.\d+\.\d+\.\d+([:/?#]|$)/.test(raw);
    if (!hostLike) return { ok: false, reason: "not a URL" };
    candidate = `${LOCAL.test(raw) ? "http" : "https"}://${raw}`;
  }
  let u: URL;
  try { u = new URL(candidate); } catch { return { ok: false, reason: "invalid URL" }; }
  if (u.protocol !== "http:" && u.protocol !== "https:") return { ok: false, reason: "only http(s) is allowed" };
  if (blockOrigin && u.origin === blockOrigin) return { ok: false, reason: "cannot embed the shell itself" };
  return { ok: true, url: u.href };
}

/** Back/forward over URL-bar navigations. In-page link clicks inside a cross-origin iframe are invisible to us. */
export class PortalHistory {
  private entries: string[] = [];
  private i = -1;
  get current(): string | undefined { return this.entries[this.i]; }
  get canBack(): boolean { return this.i > 0; }
  get canForward(): boolean { return this.i >= 0 && this.i < this.entries.length - 1; }
  visit(url: string): void {
    if (this.current === url) return;
    this.entries = this.entries.slice(0, this.i + 1);
    this.entries.push(url);
    this.i++;
  }
  back(): string | undefined { return this.canBack ? this.entries[--this.i] : undefined; }
  forward(): string | undefined { return this.canForward ? this.entries[++this.i] : undefined; }
}

/** Navigating keeps currentURL and source.url._0 coherent (both Maestri fields). Never writes url/isLoading. */
export function navigatePortal(c: PortalContent, url: string): PortalContent {
  return { ...c, currentURL: url, source: { url: { _0: url } } };
}
export const isLoading = (c: PortalContent): boolean => c.status === "loading";

export function newPortalContent(name: string, id: string = crypto.randomUUID().toUpperCase()): PortalContent {
  return { id, name, currentURL: "", source: {}, status: "idle", chromeHidden: false, storageScope: "isolated" };
}
