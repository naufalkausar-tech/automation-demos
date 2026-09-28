"""Collector: read competitor listing pages (with pagination) into Offers.

Live pages go through scrape_kit.PoliteSession (robots.txt, per-host delay, retries, a named User-Agent).
Pagination only follows next links on the start URL's host, and a page over MAX_PAGE_BYTES is not read.
Pages can be saved as they are fetched (evidence of what the competitor showed, and an offline mirror)
and read back from that mirror instead of the network.

Source config (JSON):
    {"sources": [{"competitor": "Shop A", "urls": ["https://shop-a.example/c/perfume"],
                  "card": "div.product", "name": "h3 a@title", "price": ".price", "availability": ".stock",
                  "link": "h3 a@href", "next": "a.next", "max_pages": 5, "decimal": "."}]}
Field specs use scrape_kit's "selector@attribute" form; without @ the element text is used.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Callable, NamedTuple
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from scrape_kit.extract import extract_field
from scrape_kit.polite import PoliteSession, RobotsDisallowed, iter_pages, next_link

from .core import Offer, parse_availability, parse_price

MAX_PAGE_BYTES = 10_000_000
_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)


class Page(NamedTuple):
    text: str
    url: str


@dataclass
class Source:
    competitor: str
    urls: list[str]
    card: str
    name: str
    price: str
    availability: str = ""
    link: str = ""
    next: str = ""
    max_pages: int = 5
    decimal: str = "."            # "," for sites that write 1.299,00


@dataclass
class Collected:
    offers: list[Offer] = field(default_factory=list)
    pages: int = 0
    warnings: list[str] = field(default_factory=list)


def load_sources(path: str | Path) -> list[Source]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    known = {f.name for f in fields(Source)}
    sources = []
    for i, raw in enumerate(data.get("sources", []), start=1):
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"{path}: source {i} has unknown key(s) {', '.join(sorted(unknown))}")
        if isinstance(raw.get("urls"), str):
            raw = {**raw, "urls": [raw["urls"]]}
        try:
            source = Source(**raw)
        except TypeError as exc:
            raise ValueError(f"{path}: source {i}: {exc}") from None
        if not isinstance(source.max_pages, int) or source.max_pages < 1:     # null would mean "no limit"
            raise ValueError(f"{path}: source {i}: max_pages must be a whole number >= 1")
        if source.decimal not in (".", ","):
            raise ValueError(f"{path}: source {i}: decimal must be '.' or ','")
        bad = [u for u in source.urls if urlsplit(u).scheme not in ("http", "https")]
        if bad:
            raise ValueError(f"{path}: source {i}: urls must be http(s) addresses, got {bad[0]!r}")
        sources.append(source)
    if not sources:
        raise ValueError(f"{path}: no sources configured")
    return sources


def parse_listing(html: str, page_url: str, source: Source) -> list[Offer]:
    soup = BeautifulSoup(html, "html.parser")
    offers = []
    for card in soup.select(source.card):
        name = extract_field(card, source.name, page_url)
        if not name:
            continue
        raw_price = extract_field(card, source.price, page_url) or ""
        availability = extract_field(card, source.availability, page_url) if source.availability else None
        url = extract_field(card, source.link, page_url) if source.link else ""
        if url and urlsplit(url).scheme not in ("http", "https"):
            url = ""                      # javascript:/file: links are not product pages and must not become Excel links
        offers.append(Offer(source.competitor, name, parse_price(raw_price, source.decimal),
                            parse_availability(availability), url or "", page_url, raw_price))
    return offers


def _safe(part: str) -> str:
    """A URL piece as one file name: no separators, drive colons or dot-only names ('..' would climb out of root)."""
    part = re.sub(r"[^\w.-]", "_", part)
    return part if part.strip(".") else "_"


def mirror_path(root: str | Path, url: str) -> Path:
    """https://host/a/b.html?page=2 -> root/host/a/b__page_2.html (used both to save and to replay pages).

    Next links come from competitor HTML, so every piece is sanitised and the result always stays under root.
    """
    parts = urlsplit(url)
    path = parts.path if parts.path and not parts.path.endswith("/") else parts.path + "index.html"
    target = Path(root, _safe(parts.netloc), *(_safe(p) for p in path.split("/") if p))
    if parts.query:
        target = target.with_name(f"{target.stem}__{_safe(parts.query)}{target.suffix}")
    return target


def _decode(body: bytes, content_type: str) -> str:
    """Header charset, else the page's <meta charset> (older Arabic shops use windows-1256), else UTF-8.
    requests alone would assume latin-1 for text/html without a header charset ('£' -> 'Â£')."""
    found = re.search(r"charset=[\"']?([\w-]+)", content_type, re.I)
    charset = found.group(1) if found else None
    if not charset:
        meta = _META_CHARSET.search(body[:4096])
        charset = meta.group(1).decode("ascii") if meta else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:                   # unknown charset name
        return body.decode("utf-8", errors="replace")


def http_fetcher(polite: PoliteSession, save_dir: str | Path | None = None) -> Callable[[str], Page]:
    def fetch(url: str) -> Page:
        response = polite.get_ok(url, stream=True)
        body = bytearray()
        for chunk in response.iter_content(64 * 1024):
            body.extend(chunk)
            if len(body) > MAX_PAGE_BYTES:
                response.close()
                raise requests.RequestException(f"page is larger than {MAX_PAGE_BYTES:,} bytes; not read")
        text = _decode(bytes(body), response.headers.get("Content-Type", ""))
        if save_dir:
            target = mirror_path(save_dir, url)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return Page(text, response.url or url)
    return fetch


def mirror_fetcher(root: str | Path) -> Callable[[str], Page]:
    def fetch(url: str) -> Page:
        return Page(mirror_path(root, url).read_text(encoding="utf-8"), url)
    return fetch


def _same_host(finder: Callable[[Page], str | None]) -> Callable[[Page], str | None]:
    """A next link to another host than the page it sits on (or a non-http scheme) ends pagination unfetched."""
    def find(page: Page) -> str | None:
        url = finder(page)
        parts = urlsplit(url or "")
        same = parts.netloc.lower() == urlsplit(page.url).netloc.lower()
        return url if same and parts.scheme in ("http", "https") else None
    return find


def collect(sources: list[Source], fetch: Callable[[str], Page]) -> Collected:
    result = Collected()
    seen: set[str] = set()
    for source in sources:
        finder = _same_host(next_link(source.next)) if source.next else (lambda page: None)
        for start in source.urls:
            try:
                for page in iter_pages(fetch, start, finder, source.max_pages):
                    result.pages += 1
                    found = parse_listing(page.text, page.url, source)
                    if not found:
                        result.warnings.append(f"{source.competitor}: no product cards matched '{source.card}' on {page.url}")
                    unpriced = sum(1 for o in found if o.price is None)
                    if unpriced:
                        result.warnings.append(f"{source.competitor}: {unpriced} card(s) without a readable price on {page.url}")
                    for offer in found:
                        if offer.key not in seen:      # same product listed on two pages/categories
                            seen.add(offer.key)
                            result.offers.append(offer)
            except RobotsDisallowed as exc:
                result.warnings.append(f"{source.competitor}: skipped, {exc}")
            except (requests.RequestException, OSError) as exc:
                result.warnings.append(f"{source.competitor}: {start} failed ({exc.__class__.__name__}: {exc})")
    return result
