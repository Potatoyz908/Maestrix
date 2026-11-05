/** Tiny modal text prompt (window.prompt is blocked in some embedded webviews). Resolves null on cancel. */
export function askText(host: HTMLElement, title: string, placeholder = "", initial = ""): Promise<string | null> {
  return new Promise((resolve) => {
    const scrim = document.createElement("div");
    scrim.className = "dialog-scrim";
    const box = document.createElement("div");
    box.className = "dialog";
    const h = document.createElement("div");
    h.textContent = title;
    const input = document.createElement("input");
    input.placeholder = placeholder;
    input.value = initial;
    input.spellcheck = false;
    const row = document.createElement("div");
    row.className = "dialog-row";
    const done = (v: string | null) => { scrim.remove(); resolve(v); };
    const cancel = document.createElement("button");
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", () => done(null));
    const ok = document.createElement("button");
    ok.textContent = "OK";
    ok.addEventListener("click", () => done(input.value.trim() || null));
    input.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") ok.click(); if (e.key === "Escape") cancel.click(); });
    scrim.addEventListener("pointerdown", (e) => { e.stopPropagation(); if (e.target === scrim) cancel.click(); });
    row.append(cancel, ok);
    box.append(h, input, row);
    scrim.append(box);
    host.append(scrim);
    input.focus();
  });
}
