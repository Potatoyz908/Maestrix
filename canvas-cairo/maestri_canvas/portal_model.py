"""Pure (no GTK) portal helpers: lossless PortalContent <-> dict, URL-bar normalisation, the page snapshot script.

Schema (Arquiteto/Swift): PortalContent {id, name, currentURL, source {"url":{"_0":u}} | {}, status "idle"|"loading",
chromeHidden, storageScope "isolated"|"shared"}. Unknown keys survive a round trip (`extra`)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urlsplit

SEARCH_URL = "https://duckduckgo.com/?q={}"
SCOPES = ("isolated", "shared")
_KNOWN = {"id", "name", "currentURL", "source", "status", "chromeHidden", "storageScope"}
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_ALLOWED = {"http", "https", "file", "about"}   # javascript:/data:/custom schemes are refused (URL bar and API alike)
_HOSTISH = re.compile(r"^(localhost|\d{1,3}(\.\d{1,3}){3}|\[[0-9a-fA-F:]+\]|[^\s/:?#]+\.[^\s/:?#]+)(:\d+)?([/?#].*)?$")

# Same shape as the Swift PortalSnapshotService: first 50 interactive elements as "@eN [tag] text".
SNAPSHOT_JS = """(function(){const els=document.querySelectorAll('a,button,input,select,textarea,[role]');
return Array.from(els).slice(0,50).map((el,i)=>{const tag=el.tagName.toLowerCase();
const text=(el.textContent||el.value||el.placeholder||el.getAttribute('aria-label')||'').trim().slice(0,40);
return `@e${i+1} [${tag}] ${text}`;}).join('\\n');})()"""


@dataclass
class PortalMeta:
    name: str = "Portal"
    current_url: str = ""
    status: str = "idle"
    chrome_hidden: bool = False
    storage_scope: str = "isolated"
    portal_id: str = ""
    source: dict | None = None
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "PortalMeta":
        scope = d.get("storageScope", "isolated")
        return cls(name=d.get("name", "Portal"), current_url=d.get("currentURL", ""), status=d.get("status", "idle"),
                   chrome_hidden=bool(d.get("chromeHidden", False)), storage_scope=scope if scope in SCOPES else "isolated",
                   portal_id=str(d.get("id", "")).upper(), source=d.get("source"),
                   extra={k: v for k, v in d.items() if k not in _KNOWN})

    def to_dict(self) -> dict:
        src = self.source if self.source is not None else ({"url": {"_0": self.current_url}} if self.current_url else {})
        d = dict(self.extra)
        d.update({"id": self.portal_id, "name": self.name, "currentURL": self.current_url, "source": src, "status": self.status,
                  "chromeHidden": self.chrome_hidden, "storageScope": self.storage_scope})
        return d

    @property
    def initial_url(self) -> str:
        """What to load on creation: the saved page, else the source URL."""
        if self.current_url:
            return self.current_url
        u = ((self.source or {}).get("url") or {}).get("_0")
        return u if isinstance(u, str) else ""


def normalize_url(text: str) -> str:
    """URL-bar input -> loadable URL. '' stays ''. Bare hosts get https (http for localhost/IPs), anything else becomes a search.
    javascript:/vbscript: and other unknown schemes are refused ('') — typing them must not run code in the page."""
    t = text.strip()
    if not t:
        return ""
    m = _SCHEME.match(t)
    if m and not re.match(r"^[^/\s:]+:\d+([/?#]|$)", t):   # "localhost:3000" looks like a scheme but is host:port
        return t if t[: m.end() - 1].lower() in _ALLOWED else ""
    if _HOSTISH.match(t):
        host = urlsplit("//" + t).hostname or ""
        local = host == "localhost" or re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host) is not None
        return ("http://" if local else "https://") + t
    return SEARCH_URL.format(quote_plus(t))


def display_host(url: str) -> str:
    return urlsplit(url).hostname or url
