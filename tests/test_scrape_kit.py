from __future__ import annotations

import csv
import zipfile
from email.utils import formatdate

import pytest
import requests
from openpyxl import load_workbook

from scrape_kit import PoliteSession, RobotsDisallowed, extract_records, numbered_pages, write_csv, write_xlsx
from scrape_kit.cli import main as scrape_main
from scrape_kit.export import export_records
from scrape_kit.polite import parse_retry_after

SITE = "https://shop.example"

LISTING_1 = """
<html><body>
  <div class="product" data-sku="A1">
    <h2><a href="/p/widget">Blue   Widget</a></h2>
    <span class="price">$10.50</span>
    <img src="//cdn.example/w.png">
  </div>
  <div class="product" data-sku="B2">
    <h2><a href="https://other.example/gadget">Gadget</a></h2>
  </div>
  <a rel="next" href="/list?page=2">Next</a>
</body></html>
"""
LISTING_2 = """
<html><body>
  <div class="product" data-sku="C3"><h2><a href="/p/gizmo">Gizmo</a></h2><span class="price">$3</span></div>
  <a rel="next" href="/list?page=1">Back to start (loop)</a>
</body></html>
"""
ROBOTS = "User-agent: *\nDisallow: /private\n"


def polite(http, clock, **kwargs):
    options = dict(session=http, min_interval=0, sleep=clock.sleep, clock=clock, jitter=False, backoff_base=0.5)
    options.update(kwargs)
    return PoliteSession(**options)


def test_robots_disallow_blocks_and_is_cached(http, transport, clock, make_reply):
    transport.add(f"{SITE}/robots.txt", make_reply(200, ROBOTS))
    transport.add(f"{SITE}/public", make_reply(200, "ok"))
    session = polite(http, clock)
    assert session.get(f"{SITE}/public").text == "ok"
    with pytest.raises(RobotsDisallowed):
        session.get(f"{SITE}/private/data")
    assert len(transport.calls_to(f"{SITE}/robots.txt")) == 1
    assert not transport.calls_to(f"{SITE}/private/data")


def test_robots_missing_allows_everything(http, transport, clock, make_reply):
    transport.add(f"{SITE}/private/x", make_reply(200, "fine"))
    assert polite(http, clock).get(f"{SITE}/private/x").status_code == 200


def test_robots_server_error_fails_closed_unless_configured(http, transport, clock, make_reply):
    transport.add(f"{SITE}/robots.txt", make_reply(503, "down"))
    transport.add(f"{SITE}/page", make_reply(200, "ok"))
    with pytest.raises(RobotsDisallowed):
        polite(http, clock, max_retries=0).get(f"{SITE}/page")
    assert polite(http, clock, max_retries=0, robots_fail_open=True).get(f"{SITE}/page").status_code == 200


def test_ignore_robots_skips_robots_fetch(http, transport, clock, make_reply):
    transport.add(f"{SITE}/private/x", make_reply(200, "ok"))
    polite(http, clock, respect_robots=False).get(f"{SITE}/private/x")
    assert not transport.calls_to(f"{SITE}/robots.txt")


def test_user_agent_header_is_sent(http, transport, clock, make_reply):
    transport.add(f"{SITE}/a", make_reply(200, "ok"))
    polite(http, clock, user_agent="ClientBot/2.0").get(f"{SITE}/a")
    assert transport.calls_to(f"{SITE}/a")[0].headers["User-Agent"] == "ClientBot/2.0"


def test_rate_limit_is_per_host(http, transport, clock, make_reply):
    transport.add(f"{SITE}/a", make_reply(200, "a"))
    transport.add(f"{SITE}/b", make_reply(200, "b"))
    transport.add("https://other.example/c", make_reply(200, "c"))
    session = polite(http, clock, min_interval=2.0, respect_robots=False)
    session.get(f"{SITE}/a")
    session.get("https://other.example/c")
    assert clock.sleeps == []
    session.get(f"{SITE}/b")
    assert clock.sleeps == [pytest.approx(2.0)]


def test_crawl_delay_from_robots_raises_interval(http, transport, clock, make_reply):
    transport.add(f"{SITE}/robots.txt", make_reply(200, "User-agent: *\nCrawl-delay: 5\n"))
    transport.add(f"{SITE}/a", make_reply(200, "a"))
    session = polite(http, clock, min_interval=1.0)
    session.get(f"{SITE}/a")
    session.get(f"{SITE}/a")
    assert pytest.approx(5.0) in clock.sleeps


def test_retries_with_exponential_backoff(http, transport, clock, make_reply):
    transport.add(f"{SITE}/flaky", make_reply(503), make_reply(502), make_reply(200, "finally"))
    response = polite(http, clock, respect_robots=False, max_retries=3).get(f"{SITE}/flaky")
    assert response.text == "finally"
    assert clock.sleeps == [0.5, 1.0]


def test_retry_after_header_is_honoured(http, transport, clock, make_reply):
    transport.add(f"{SITE}/limited", make_reply(429, "", {"Retry-After": "7"}), make_reply(200, "ok"))
    polite(http, clock, respect_robots=False).get(f"{SITE}/limited")
    assert clock.sleeps == [7.0]


def test_exhausted_status_retries_return_last_response(http, transport, clock, make_reply):
    transport.add(f"{SITE}/down", make_reply(503))
    session = polite(http, clock, respect_robots=False, max_retries=2)
    assert session.get(f"{SITE}/down").status_code == 503
    assert len(transport.calls_to(f"{SITE}/down")) == 3
    with pytest.raises(requests.HTTPError):
        session.get_ok(f"{SITE}/down")


def test_connection_errors_retry_then_raise(http, transport, clock):
    transport.add(f"{SITE}/dead", requests.ConnectionError("boom"))
    with pytest.raises(requests.ConnectionError):
        polite(http, clock, respect_robots=False, max_retries=2).get(f"{SITE}/dead")
    assert len(transport.calls_to(f"{SITE}/dead")) == 3


def test_parse_retry_after_http_date():
    assert parse_retry_after(formatdate(1_000_030, usegmt=True), now=1_000_000) == pytest.approx(30)
    assert parse_retry_after("garbage") is None
    assert parse_retry_after(None) is None


def test_paginate_follows_next_links_and_stops_on_loops(http, transport, clock, make_reply):
    transport.add(f"{SITE}/list", make_reply(200, LISTING_1))
    transport.add(f"{SITE}/list?page=2", make_reply(200, LISTING_2))
    transport.add(f"{SITE}/list?page=1", make_reply(200, LISTING_1))
    pages = list(polite(http, clock, respect_robots=False).paginate(f"{SITE}/list"))
    assert [p.url for p in pages] == [f"{SITE}/list", f"{SITE}/list?page=2", f"{SITE}/list?page=1"]
    limited = list(polite(http, clock, respect_robots=False).paginate(f"{SITE}/list", max_pages=1))
    assert len(limited) == 1


def test_numbered_pages():
    assert list(numbered_pages("https://x/p/{page}", first=2, last=4)) == ["https://x/p/2", "https://x/p/3", "https://x/p/4"]


def test_extract_records_text_attributes_and_urls():
    fields = {
        "sku": "@data-sku",
        "name": "h2 a",
        "url": "h2 a@href",
        "price": "span.price",
        "image": "img@src",
        "sku_lower": lambda node: node["data-sku"].lower(),
    }
    records = extract_records(LISTING_1, "div.product", fields, base_url=f"{SITE}/list")
    assert records[0] == {
        "sku": "A1",
        "name": "Blue Widget",
        "url": f"{SITE}/p/widget",
        "price": "$10.50",
        "image": "https://cdn.example/w.png",
        "sku_lower": "a1",
    }
    assert records[1]["url"] == "https://other.example/gadget"
    assert records[1]["price"] is None and records[1]["image"] is None


def test_write_csv_and_xlsx_round_trip(tmp_path):
    records = [
        {"name": "Widget", "tags": ["a", "b"], "price": 10.5},
        {"name": "=HYPERLINK(\"http://evil\")", "extra": "only here", "price": None},
        {"name": "bad\x07char", "price": 3},
    ]
    csv_path = write_csv(records, tmp_path / "out.csv")
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == ["name", "tags", "price", "extra"]
    assert rows[0]["tags"] == "a | b" and rows[1]["extra"] == "only here" and rows[1]["price"] == ""

    escaped = write_csv(records, tmp_path / "escaped.csv", escape_formulas=True)
    assert "'=HYPERLINK" in escaped.read_text(encoding="utf-8-sig")

    xlsx_path = write_xlsx(records, tmp_path / "out.xlsx")
    sheet = load_workbook(xlsx_path).active
    assert [c.value for c in sheet[1]] == ["name", "tags", "price", "extra"]
    assert sheet["A4"].value == "badchar"
    assert sheet.freeze_panes == "A2"
    with zipfile.ZipFile(xlsx_path) as archive:
        sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode()
    assert "<f>" not in sheet_xml


def test_export_records_rejects_unknown_format(tmp_path):
    with pytest.raises(ValueError):
        export_records([{"a": 1}], tmp_path / "out.parquet")


def test_cli_scrape_end_to_end(http, transport, make_reply, tmp_path, capsys):
    transport.add(f"{SITE}/robots.txt", make_reply(200, ROBOTS))
    transport.add(f"{SITE}/list", make_reply(200, LISTING_1))
    transport.add(f"{SITE}/list?page=2", make_reply(200, LISTING_2))
    out = tmp_path / "products.xlsx"
    code = scrape_main(
        [
            "scrape", f"{SITE}/list",
            "--item", "div.product",
            "--field", "name=h2 a",
            "--field", "url=h2 a@href",
            "--field", "price=.price",
            "--next", "a[rel=next]",
            "--max-pages", "2",
            "--delay", "0",
            "--page-url-column",
            "--out", str(out),
        ],
        session=http,
    )
    assert code == 0
    rows = list(load_workbook(out).active.iter_rows(values_only=True))
    assert rows[0] == ("name", "url", "price", "_page_url")
    assert [r[0] for r in rows[1:]] == ["Blue Widget", "Gadget", "Gizmo"]
    assert "wrote 3 records from 2 page(s)" in capsys.readouterr().out


def test_cli_scrape_reports_robots_block(http, transport, make_reply, tmp_path):
    transport.add(f"{SITE}/robots.txt", make_reply(200, "User-agent: *\nDisallow: /\n"))
    code = scrape_main(["scrape", f"{SITE}/list", "--item", "div", "--field", "x=a", "--out", str(tmp_path / "o.csv")], session=http)
    assert code == 3
