"""Extract SEO-relevant facts from one page's HTML. Pure function, no network."""
import re
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup, Comment

from .. import security

TRACKING_ID = re.compile(r"\b(GTM-[A-Z0-9]{4,10}|G-[A-Z0-9]{8,12})\b")
_SPACE = re.compile(r"\s+")


def _text(el) -> str:
    return _SPACE.sub(" ", el.get_text(" ", strip=True)).strip() if el else ""


def _unique(seq):
    return list(dict.fromkeys(seq))


def normalize_url(url: str) -> str:
    """Drop fragments and a trailing slash on non-root paths; lowercase scheme and host."""
    url, _ = urldefrag(url)
    p = urlsplit(url)
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    q = f"?{p.query}" if p.query else ""
    return f"{p.scheme.lower()}://{p.netloc.lower()}{path}{q}"


def parse_page(html: str, url: str, headers: dict | None = None, *, domain: str = "arbcentrix.com",
               booking_pattern: str | None = None, build_asset_pattern: str | None = None) -> dict:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    soup = BeautifulSoup(html, "lxml")
    head = soup.head or soup

    def meta(name):
        tag = head.find("meta", attrs={"name": re.compile(f"^{name}$", re.I)})
        return tag.get("content", "").strip() if tag else None

    canonicals = _unique(urljoin(url, l.get("href", "").strip())
                         for l in soup.find_all("link", rel=lambda r: r and "canonical" in r)
                         if l.get("href"))
    robots_meta = ", ".join(filter(None, [meta("robots"), meta("googlebot")]))
    x_robots = headers.get("x-robots-tag", "")
    noindex = "noindex" in robots_meta.lower() or "noindex" in x_robots.lower()

    scripts = [s.get("src") for s in soup.find_all("script") if s.get("src")]
    preloads = [l.get("href") for l in soup.find_all("link", rel=lambda r: r and "modulepreload" in r)
                if l.get("href")]
    build_assets = sorted({u for u in scripts + preloads if build_asset_pattern and build_asset_pattern in u})
    links, internal, external = [], [], []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")) or not href:
            continue
        absolute = normalize_url(urljoin(url, href))
        host = urlsplit(absolute).netloc
        (internal if host == domain or host.endswith("." + domain) else external).append(absolute)
    internal, external = _unique(internal), _unique(external)

    # Untrusted text that could address an AI reader: flag it, never act on it.
    texts = [_text(head.find("title")), meta("description") or ""]
    texts += [c.strip() for c in soup.find_all(string=lambda x: isinstance(x, Comment))]
    texts += [i.get(a, "") for i in soup.find_all(True) for a in ("alt", "title", "aria-label") if i.get(a)]

    for t in soup(["script", "style", "noscript", "template"]):
        t.extract()
    body = soup.body
    main = soup.find(id="main")
    words = len(_text(body).split()) if body else 0
    texts += [_text(e) for e in (body.find_all(["h1", "h2", "h3", "p", "li", "div", "span"]) if body else [])
              if not e.find(["p", "li", "div"])]
    suspicious = security.flag(texts, f"page:{url}")

    return {
        "url": url,
        "title": _text(head.find("title")) or None,
        "meta_description": meta("description"),
        "canonicals": canonicals,
        "robots_meta": robots_meta or None,
        "x_robots_tag": x_robots or None,
        "noindex": noindex,
        # Framer renders one copy per breakpoint; keep distinct texts.
        "h1": _unique(_text(h) for h in soup.find_all("h1") if _text(h)),
        "h2": _unique(_text(h) for h in soup.find_all("h2") if _text(h)),
        "word_count": words,
        "empty_shell": main is not None and not _text(main) and not main.find(True),
        "internal_links": internal,
        "external_links": external,
        "script_srcs": scripts,
        "build_assets": build_assets,
        "tracking_ids": sorted(set(TRACKING_ID.findall(html))),
        "has_form": soup.find("form") is not None,
        "has_booking": bool(booking_pattern) and any(booking_pattern in u for u in external),
        "suspicious": suspicious,
    }
