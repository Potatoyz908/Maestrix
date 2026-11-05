/**
 * Minimal Markdown -> HTML for sticky notes. Input is HTML-escaped FIRST, so the output
 * can only contain tags produced here; link targets are limited to http(s)/mailto.
 * Supports: # headings, **bold**, *italic*, `code`, fenced code, lists (- / * / 1.), > quotes, ---, [t](url).
 */
export const escapeHtml = (s: string): string =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

function inline(escaped: string): string {
  const codes: string[] = [];
  let s = escaped.replace(/`([^`]+)`/g, (_m, c: string) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
  return s.replace(/\u0000(\d+)\u0000/g, (_m, i: string) => `<code>${codes[Number(i)]}</code>`);
}

export function renderMarkdown(src: string): string {
  const lines = escapeHtml(src.replace(/\u0000/g, "").replace(/\r\n?/g, "\n")).split("\n");
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (line.trim() === "") { i++; continue; }

    if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i]!.startsWith("```")) code.push(lines[i++]!);
      i++; // closing fence (or EOF)
      out.push(`<pre><code>${code.join("\n")}</code></pre>`);
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) { out.push(`<h${h[1]!.length}>${inline(h[2]!)}</h${h[1]!.length}>`); i++; continue; }
    if (/^(-{3,}|\*{3,})\s*$/.test(line)) { out.push("<hr>"); i++; continue; }

    if (line.startsWith("&gt;")) {
      const q: string[] = [];
      while (i < lines.length && lines[i]!.startsWith("&gt;")) q.push(lines[i++]!.replace(/^&gt;\s?/, ""));
      out.push(`<blockquote>${inline(q.join("<br>"))}</blockquote>`);
      continue;
    }
    const li = /^(\s*)([-*]|\d+\.)\s+/;
    if (li.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (i < lines.length && li.test(lines[i]!) && /^\s*\d+\./.test(lines[i]!) === ordered) items.push(`<li>${inline(lines[i++]!.replace(li, ""))}</li>`);
      out.push(`<${ordered ? "ol" : "ul"}>${items.join("")}</${ordered ? "ol" : "ul"}>`);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i]!.trim() !== "" && !/^(#{1,6}\s|```|&gt;|\s*([-*]|\d+\.)\s)/.test(lines[i]!)) para.push(inline(lines[i++]!));
    out.push(`<p>${para.join("<br>")}</p>`);
  }
  return out.join("\n");
}
