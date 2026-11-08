(function () {
  "use strict";
  // --- mobile nav ---
  var btn = document.getElementById("menu-btn"), side = document.getElementById("sidebar");
  if (btn && side) btn.addEventListener("click", function () { side.classList.toggle("open"); });

  // --- copy buttons on code blocks ---
  document.querySelectorAll(".hl").forEach(function (block) {
    var b = document.createElement("button");
    b.className = "copy-btn"; b.type = "button"; b.textContent = "Copy";
    b.addEventListener("click", function () {
      var pre = block.querySelector("pre");
      navigator.clipboard.writeText(pre.innerText).then(function () {
        b.textContent = "Copied"; setTimeout(function () { b.textContent = "Copy"; }, 1400);
      }, function () { b.textContent = "Press Ctrl+C"; });
    });
    block.appendChild(b);
  });

  // --- live GitHub star count (cached for 10 min to stay under the API rate limit) ---
  var starEl = document.getElementById("gh-stars");
  if (starEl) {
    var fmt = function (n) { return n >= 1000 ? (n / 1000).toFixed(1).replace(".0", "") + "k" : String(n); };
    var cached = null;
    try { cached = JSON.parse(sessionStorage.getItem("mx-stars") || "null"); } catch (e) {}
    if (cached && Date.now() - cached.t < 600000) starEl.textContent = fmt(cached.n);
    else fetch("https://api.github.com/repos/Potatoyz908/Maestrix").then(function (r) { return r.json(); }).then(function (j) {
      if (typeof j.stargazers_count !== "number") return;
      starEl.textContent = fmt(j.stargazers_count);
      try { sessionStorage.setItem("mx-stars", JSON.stringify({ n: j.stargazers_count, t: Date.now() })); } catch (e) {}
    }).catch(function () {});
  }

  // --- table of contents: highlight the current section ---
  var tocLinks = Array.prototype.slice.call(document.querySelectorAll(".toc a"));
  if (tocLinks.length && "IntersectionObserver" in window) {
    var map = {};
    tocLinks.forEach(function (a) { map[a.getAttribute("href").slice(1)] = a; });
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting && map[e.target.id]) {
          tocLinks.forEach(function (a) { a.classList.remove("cur"); });
          map[e.target.id].classList.add("cur");
        }
      });
    }, { rootMargin: "-70px 0px -70% 0px" });
    Object.keys(map).forEach(function (id) { var el = document.getElementById(id); if (el) obs.observe(el); });
  }

  // --- search (client side over search-index.json) ---
  var input = document.getElementById("search"), box = document.getElementById("results"), index = null, sel = -1;
  function load(cb) {
    if (index) return cb();
    fetch("search-index.json").then(function (r) { return r.json(); }).then(function (j) { index = j; cb(); });
  }
  function esc(s) { return s.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function snippet(text, terms) {
    var low = text.toLowerCase(), at = -1;
    for (var i = 0; i < terms.length && at < 0; i++) at = low.indexOf(terms[i]);
    if (at < 0) return text.slice(0, 110);
    var s = Math.max(0, at - 40);
    return (s ? "…" : "") + text.slice(s, s + 130) + "…";
  }
  function run() {
    var q = input.value.trim().toLowerCase();
    if (!q) { box.hidden = true; return; }
    var terms = q.split(/\s+/);
    var hits = index.map(function (p) {
      var hay = (p.t + " " + p.h.join(" ") + " " + p.d + " " + p.x).toLowerCase(), score = 0;
      for (var i = 0; i < terms.length; i++) {
        if (hay.indexOf(terms[i]) < 0) return null;
        if (p.t.toLowerCase().indexOf(terms[i]) >= 0) score += 10;
        if (p.h.join(" ").toLowerCase().indexOf(terms[i]) >= 0) score += 4;
        score += 1;
      }
      return { p: p, score: score };
    }).filter(Boolean).sort(function (a, b) { return b.score - a.score; }).slice(0, 8);
    box.innerHTML = hits.length ? hits.map(function (h) {
      return '<a href="' + h.p.u + '">' + esc(h.p.t) + "<small>" + esc(snippet(h.p.x, terms)) + "</small></a>";
    }).join("") : '<div class="none">No results</div>';
    box.hidden = false; sel = -1;
  }
  if (input) {
    input.addEventListener("input", function () { load(run); });
    input.addEventListener("focus", function () { load(run); });
    input.addEventListener("keydown", function (e) {
      var items = box.querySelectorAll("a");
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + items.length) % (items.length || 1);
        items.forEach(function (a, i) { a.classList.toggle("sel", i === sel); });
      } else if (e.key === "Enter" && items[Math.max(sel, 0)]) { window.location = items[Math.max(sel, 0)].href; }
      else if (e.key === "Escape") { box.hidden = true; input.blur(); }
    });
    document.addEventListener("click", function (e) { if (!e.target.closest(".search")) box.hidden = true; });
    document.addEventListener("keydown", function (e) {
      if (e.key === "/" && document.activeElement !== input && !/INPUT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); input.focus(); }
    });
  }
})();
