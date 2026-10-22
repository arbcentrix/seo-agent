"""Sitemap reader (urlset and sitemapindex)."""
from lxml import etree

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class SitemapError(Exception):
    pass


def parse_sitemap(xml: str | bytes) -> tuple[str, list[str]]:
    """Return (kind, locs) where kind is 'urlset' or 'sitemapindex'."""
    if isinstance(xml, str):
        xml = xml.encode()
    try:
        root = etree.fromstring(xml, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError as e:
        raise SitemapError(f"invalid sitemap XML: {e}")
    kind = etree.QName(root).localname
    if kind not in ("urlset", "sitemapindex"):
        raise SitemapError(f"unexpected root element <{kind}>")
    locs = [loc.text.strip() for loc in root.iterfind(".//sm:loc", NS) if loc.text and loc.text.strip()]
    return kind, locs


def read_sitemap(fetcher, url: str) -> list[str]:
    """Fetch a sitemap (following one level of sitemapindex) and return page URLs."""
    r = fetcher.get(url)
    if r.status != 200:
        raise SitemapError(f"sitemap {url}: {r.error or r.status}")
    kind, locs = parse_sitemap(r.text)
    if kind == "urlset":
        return locs
    urls = []
    for child in locs:
        urls.extend(read_sitemap(fetcher, child))
    return urls
