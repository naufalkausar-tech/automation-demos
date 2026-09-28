import csv
import importlib
import json
import shutil
from pathlib import Path

import pytest
from openpyxl import load_workbook

import price_monitor
from price_monitor import (DryRunAdapter, Match, Offer, Product, Settings, Source, collect, history_rows,
                           http_fetcher, load_catalogue, load_matches, load_sources, mirror_fetcher, normalise_name,
                           open_db, parse_availability, parse_price, previous_offers, read_approved, read_changes,
                           record_run, resolve, similarity, suggest, suggest_matches, write_changes, write_report)
from price_monitor.cli import SUGGESTION_COLUMNS, main
from price_monitor.collect import mirror_path
from scrape_kit.polite import PoliteSession

collect_module = importlib.import_module("price_monitor.collect")   # the package exports a function of that name

FIX = Path(price_monitor.__file__).parent / "fixtures"
BOOKS = "https://books.toscrape.com/catalogue/category/books/mystery_3/index.html"
SOUQ = "https://souq-demo.test/ar/oud.html"
SHARP = "https://books.toscrape.com/catalogue/sharp-objects_997/index.html"
ROYAL = "https://souq-demo.test/ar/p/royal-oud-100"


def books(**kw):
    return Source("books", [BOOKS], card="article.product_pod", name="h3 a@title", price="p.price_color",
                  availability="p.availability", link="h3 a@href", next="li.next a", **kw)


def souq(**kw):
    return Source("souq", [SOUQ], card="div.product-card", name="h2.title", price="span.price-now",
                  availability="span.stock", link="a.product-link@href", next="a.next", **kw)


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


# --- reading prices ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, decimal, expected", [
    ("£51.77", ".", 51.77),
    ("Â£51.77", ".", 51.77),                  # latin-1 mis-decoded pound sign
    ("SAR 1,299.00", ".", 1299.0),
    ("١٬٢٩٩٫٠٠ ر.س", ".", 1299.0),            # Arabic-Indic digits, Arabic thousands + decimal separators
    ("٨٩ ر.س", ".", 89.0),
    ("۷۵ ر.س", ".", 75.0),                    # Extended (Persian) digits
    ("د.إ 45.5", ".", 45.5),                  # currency with a dot before the number
    ("1 299 ₽", ".", 1299.0),
    ("1.299,00 €", ".", 1299.0),              # both separators: rightmost is the decimal
    ("12,50 €", ",", 12.5),
    ("1.299 €", ",", 1299.0),
    ("1.250 KWD", ".", 1.25),
    ("1 299,00 €", ",", 1299.0),
    ("12.99 500 sold", ".", 12.99),           # no thousands group after the decimals
    ("SAR 45 100ml", ".", 45.0),              # "100ml" is a size, not a thousands group
    ("Free", ".", None),
    ("", ".", None),
])
def test_parse_price(text, decimal, expected):
    assert parse_price(text, decimal) == expected


@pytest.mark.parametrize("text, expected", [
    ("In stock", True), ("Out of stock", False), ("Sold out", False), ("متوفر", True),
    ("غير متوفر حالياً", False), ("نفدت الكمية", False), ("", None), ("Ships in 3 days", None),
    ("Not in stock", False), ("tidak tersedia", False), ("متوفر في منفذ البيع", True),   # منفذ = outlet, not نفذ
])
def test_parse_availability(text, expected):
    assert parse_availability(text) == expected


# --- collecting ---------------------------------------------------------------------------------------------------

def test_listing_uses_full_title_absolute_link_and_follows_pagination():
    got = collect([books()], mirror_fetcher(FIX))
    assert got.pages == 2 and not got.warnings
    assert [o.name for o in got.offers][:2] == ["Sharp Objects", "In a Dark, Dark Wood"]   # title attr, not "Dark ..."
    first, last = got.offers[0], got.offers[-1]
    assert (first.url, first.price, first.in_stock, first.raw_price) == (SHARP, 47.82, True, "£47.82")
    assert last.page_url.endswith("/page-2.html") and last.in_stock is False
    assert len(collect([books(max_pages=1)], mirror_fetcher(FIX)).offers) == 3


def test_arabic_store_digits_stock_and_query_pagination():
    got = collect([souq()], mirror_fetcher(FIX))
    assert [o.price for o in got.offers] == [1250.0, 450.0, 89.5, 75.0, 145.0]     # now-price, not the struck-out one
    assert [o.in_stock for o in got.offers] == [True, False, True, False, True]
    assert got.offers[0].url == ROYAL and got.offers[-1].page_url == SOUQ + "?page=2"


def test_http_collection_is_polite_and_saves_a_replayable_mirror(tmp_path, http, transport, clock):
    for name in ("index.html", "page-2.html"):
        body = (FIX / "books.toscrape.com/catalogue/category/books/mystery_3" / name).read_bytes()
        transport.add(BOOKS.replace("index.html", name), (200, {"Content-Type": "text/html"}, body))   # no charset
    polite = PoliteSession(user_agent="PriceBot/1.0", session=http, sleep=clock.sleep, clock=clock, jitter=False,
                           min_interval=2)
    got = collect([books()], http_fetcher(polite, tmp_path / "pages"))
    assert got.offers[0].raw_price == "£47.82"                                       # decoded as UTF-8
    assert transport.requests[0].url.endswith("/robots.txt") and clock.sleeps == [2, 2]
    assert {r.headers["User-Agent"] for r in transport.requests} == {"PriceBot/1.0"}
    replay = collect([books()], mirror_fetcher(tmp_path / "pages"))
    assert [(o.name, o.price) for o in replay.offers] == [(o.name, o.price) for o in got.offers]


def test_robots_disallow_is_respected(http, transport, clock, make_reply):
    transport.add("https://books.toscrape.com/robots.txt", make_reply(200, "User-agent: *\nDisallow: /catalogue/"))
    polite = PoliteSession(session=http, sleep=clock.sleep, clock=clock, jitter=False, min_interval=0)
    got = collect([books()], http_fetcher(polite))
    assert got.offers == [] and "robots.txt disallows" in got.warnings[0]
    assert [r.url for r in transport.requests] == ["https://books.toscrape.com/robots.txt"]


def test_load_sources_rejects_typos_and_unsafe_values(tmp_path):
    for extra, message in [({"pricee": "d"}, "pricee"), ({"max_pages": None}, "max_pages"),   # null = unbounded
                           ({"decimal": ";"}, "decimal"), ({"urls": ["file:///C:/x.html"]}, "http")]:
        cfg = {"sources": [{"competitor": "x", "urls": [BOOKS], "card": "a", "name": "b", "price": "c", **extra}]}
        with pytest.raises(ValueError, match=message):
            load_sources(write(tmp_path / "s.json", json.dumps(cfg)))


@pytest.mark.parametrize("url", [
    "https://shop.test/../../outside.html", "https://shop.test/a/D:/x.html", "https://shop.test/a\\..\\..\\..\\x.html",
    "https://shop.test/.../x", "https://../x", "https://shop.test/list?../../x"])
def test_mirror_path_never_leaves_its_root(tmp_path, url):
    root = tmp_path / "pages"
    assert mirror_path(root, url).resolve().is_relative_to(root.resolve())
    assert mirror_path(root, SOUQ + "?page=2") == root / "souq-demo.test/ar/oud__page_2.html"


def test_pagination_stays_on_host_and_script_links_are_dropped(tmp_path):
    card = '<div class="p"><a href="{}">{}</a><span class="c">SAR 10</span></div><a class="next" href="{}">next</a>'
    for url, html in [("https://shop.test/list.html", card.format("javascript:alert(1)", "Oud", "https://evil.test/2.html")),
                      ("https://evil.test/2.html", card.format("/musk", "Musk", ""))]:
        mirror_path(tmp_path, url).parent.mkdir(parents=True, exist_ok=True)
        write(mirror_path(tmp_path, url), html)
    src = Source("shop", ["https://shop.test/list.html"], card="div.p", name="a", price="span.c", link="a@href",
                 next="a.next")
    got = collect([src], mirror_fetcher(tmp_path))
    assert got.pages == 1 and [(o.name, o.url, o.key) for o in got.offers] == [("Oud", "", "shop|oud")]


def test_http_reads_meta_charset_and_refuses_oversized_pages(http, transport, clock, monkeypatch):
    url = "https://souq-demo.test/ar/cp1256.html"
    html = ('<html><head><meta charset="windows-1256"></head><body><div class="product-card">'
            '<h2 class="title">عطر العود</h2><span class="price-now">89 ر.س</span></div></body></html>')
    transport.add(url, (200, {"Content-Type": "text/html"}, html.encode("cp1256")))     # charset only in <meta>
    src = Source("souq", [url], card="div.product-card", name="h2.title", price="span.price-now")
    polite = PoliteSession(session=http, sleep=clock.sleep, clock=clock, jitter=False, min_interval=0)
    assert [(o.name, o.price) for o in collect([src], http_fetcher(polite)).offers] == [("عطر العود", 89.0)]
    monkeypatch.setattr(collect_module, "MAX_PAGE_BYTES", 100)
    got = collect([src], http_fetcher(polite))
    assert got.offers == [] and "larger than 100 bytes" in got.warnings[0]


# --- matching -----------------------------------------------------------------------------------------------------

def test_normalise_name_folds_arabic_and_english():
    assert normalise_name("عطر العود الملكي ١٠٠ مل") == normalise_name("عطر عود ملكي 100مل") == "عطر عود ملكي 100 مل"
    assert normalise_name("بَخور مَعمول") == "بخور معمول"                            # tashkeel removed
    assert normalise_name("قهوة أصلية") == normalise_name("قهوه اصليه")               # ة/ه and أ/ا folded
    assert normalise_name("Sharp Objects (Paperback)") == "sharp objects paperback"


def test_similarity_ignores_word_order_but_not_sizes():
    n = normalise_name
    assert similarity(n("Royal Oud Perfume"), n("Oud Royal perfume")) == 1.0
    assert similarity(n("iPhone 15 Pro 256GB"), n("iPhone 14 Pro 256GB")) < 0.5
    assert similarity(n("عطر العود الملكي 50 مل"), n("عطر العود الملكي ١٠٠ مل")) < 0.5
    # strictly under 0.5, so even a loose 0.5 threshold never proposes the wrong volume of a series
    book3 = [Product("B3", "The Last Mile (Amos Decker 3)", 20, 10, 20)]
    assert suggest_matches(book3, [Offer("books", "The Last Mile (Amos Decker #2)", 20.0, True, SHARP)], min_score=0.5) == []


def test_suggested_matches_are_only_used_after_approval(tmp_path):
    offers = collect([books(), souq()], mirror_fetcher(FIX)).offers
    products = [Product("AR-1", "عطر عود ملكي 100 مل", 1300, 800, 20),
                Product("EN-1", "Sharp Objects (Paperback)", 50, 30, 20)]
    rows = suggest_matches(products, offers)
    pairs = {(r["sku"], r["competitor"]): r for r in rows}
    assert pairs[("AR-1", "souq")]["competitor_url"] == ROYAL and pairs[("AR-1", "souq")]["score"] == 1.0
    assert pairs[("EN-1", "books")]["competitor_url"] == SHARP and pairs[("EN-1", "books")]["approved"] == ""
    assert ("AR-1", "books") not in pairs                                           # nothing close enough

    path = tmp_path / "suggestions.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, SUGGESTION_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    assert load_matches(path) == []                                                 # nothing approved yet
    text = path.read_text(encoding="utf-8").replace(",1.0,\n", ",1.0,yes\n", 1)
    assert [m.sku for m in load_matches(write(path, text))] == ["AR-1"]
    assert suggest_matches(products, offers, [Match("AR-1", "souq", ROYAL)], 0.6)[0]["sku"] == "EN-1"


def test_resolve_by_url_or_name_and_report_missing():
    offers = collect([books()], mirror_fetcher(FIX)).offers
    linked, missing = resolve([Match("A", "", SHARP), Match("B", "books", "", "the past  NEVER ends"),
                               Match("C", "", "https://books.toscrape.com/catalogue/gone_1/index.html")], offers)
    assert linked["A"][0].price == 47.82 and linked["B"][0].price == 56.50
    assert [m.sku for m in missing] == ["C"]


# --- rules --------------------------------------------------------------------------------------------------------

P = Product("SKU-1", "Widget", our_price=50, cost=30, min_margin_pct=20)             # floor = 30 / 0.8 = 37.50


def offer(price, stock=True, competitor="A"):
    return Offer(competitor, "Widget", price, stock, f"https://{competitor.lower()}.test/widget")


def codes(s):
    return [c for c, _ in s.flags]


def test_margin_floor_is_gross_margin_on_price():
    assert P.floor == 37.5
    assert Product("x", "x", 20, 10, 33).floor == 14.93                              # 14.925 rounded up, never down


def test_undercut_lowest_in_stock_competitor():
    s = suggest(P, [offer(45), offer(40, stock=False, competitor="B")], {}, Settings(undercut_pct=1))
    assert (s.target.competitor, s.suggested, s.action) == ("A", 44.55, "lower")
    assert codes(s) == ["competitor_out_of_stock"] and s.approve is False           # default: a person approves


def test_suggestion_never_goes_below_the_floor():
    s = suggest(P, [offer(35)], {})
    assert s.suggested == 37.5 and "floor_limited" in codes(s)


def test_much_cheaper_than_competitors_suggests_a_raise():
    s = suggest(P, [offer(60)], {}, Settings(cheaper_pct=10))
    assert (s.suggested, s.action) == (60, "raise") and "we_are_cheaper" in codes(s)
    assert round(s.gap_pct, 3) == -0.167


def test_suspect_prices_are_ignored_and_block_auto_approval():
    policy = Settings(auto_approve_pct=10)
    s = suggest(P, [offer(12, competitor="A"), offer(48, competitor="B")], {}, policy)   # 12 < half our cost
    assert s.target.competitor == "B" and s.suggested == 48 and "possible_pricing_error" in codes(s)
    assert s.approve is False
    assert suggest(P, [offer(48, competitor="B")], {}, policy).approve is True      # -4% is inside the policy
    assert suggest(P, [offer(40, competitor="B")], {}, policy).approve is False     # -20% is not


def test_change_since_last_run_is_flagged():
    o = offer(48)
    s = suggest(P, [o], {o.key: (52.0, True)})
    assert ("price_changed", "A: 52.00 -> 48.00") in s.flags
    jump = suggest(P, [offer(20)], {o.key: (52.0, True)})                           # -62% overnight: suspect
    assert "possible_pricing_error" in codes(jump) and jump.action == "hold"


def test_hold_keep_and_below_floor():
    assert suggest(P, [], {}).action == "hold"
    assert suggest(P, [offer(45, stock=False)], {}).suggested is None
    assert suggest(P, [offer(50)], {}, Settings(auto_approve_pct=10)).action == "keep"
    low = suggest(Product("x", "x", 30, 30, 20), [offer(45)], {})
    assert "below_floor" in codes(low) and low.action == "raise"


def test_catalogue_accepts_arabic_digits_and_rejects_bad_margins(tmp_path):
    ok = write(tmp_path / "c.csv", "sku,name,our_price,cost,min_margin_pct\nA,قهوة,٥٠,٣٠,٢٠\n")
    assert load_catalogue(ok) == [Product("A", "قهوة", 50.0, 30.0, 20.0)]
    with pytest.raises(ValueError, match="0-99"):
        load_catalogue(write(tmp_path / "b.csv", "sku,name,our_price,cost,min_margin_pct\nA,x,5,3,100\n"))
    with pytest.raises(ValueError, match="cost"):
        load_catalogue(write(tmp_path / "m.csv", "sku,name,our_price,min_margin_pct\nA,x,5,20\n"))
    with pytest.raises(ValueError, match="duplicate sku A"):
        load_catalogue(write(tmp_path / "d.csv", "sku,name,our_price,cost,min_margin_pct\nA,x,5,3,20\nA,y,6,3,20\n"))


# --- history ------------------------------------------------------------------------------------------------------

def test_history_tracks_changes_between_runs(tmp_path):
    conn = open_db(tmp_path / "h.sqlite")
    first = offer(52)
    record_run(conn, [first], [P], {"SKU-1": [first]}, note="run 1", at="2026-09-01 08:00 UTC")
    assert previous_offers(conn) == {first.key: (52.0, True)}
    second = offer(48, stock=False)
    record_run(conn, [second], [Product("SKU-1", "Widget", 49, 30, 20)], {"SKU-1": [second]}, note="run 2")
    assert previous_offers(conn) == {first.key: (48.0, False)}
    rows = history_rows(conn)
    assert [(r["note"], r["source"], r["price"], r["change"]) for r in rows] == [
        ("run 2", "(our price)", 49.0, -1.0), ("run 2", "A", 48.0, -4.0),
        ("run 1", "(our price)", 50.0, None), ("run 1", "A", 52.0, None)]


# --- Excel report and approval -----------------------------------------------------------------------------------

def test_excel_report_structure_and_approval_roundtrip(tmp_path):
    settings = Settings(auto_approve_pct=10)
    suggestions = [suggest(P, [offer(48)], {}, settings),                          # pre-approved (-4%)
                   suggest(Product("SKU-2", "Gadget", 100, 50, 25), [offer(70)], {}, settings),  # -30%: review
                   suggest(Product("SKU-3", "=HYPERLINK(\"x\")", 10, 5, 10), [], {}, settings)]
    history = [{"run_id": 1, "at": "2026-09-01", "note": "n", "sku": "SKU-1", "source": "A", "product": "Widget",
                "url": "https://a.test/widget", "price": 48.0, "in_stock": True, "change": -4.0}]
    path = write_report(tmp_path / "gap_report.xlsx", suggestions, history,
                        {"label": "DEMO DATA", "sources": ["A"], "warnings": ["w1"]}, settings)

    wb = load_workbook(path)
    assert wb.sheetnames == ["Summary", "Detail", "History"]
    assert wb["Summary"]["A2"].value == "DEMO DATA"
    detail = wb["Detail"]
    assert detail.freeze_panes == "C2" and wb["History"].freeze_panes == "A2"
    header = [c.value for c in detail[1]]
    assert header[:3] == ["SKU", "Name", "Our price"] and header[-1] == "Approve"
    ranges = {str(r.sqref) for r in detail.conditional_formatting}
    assert {"J2:J4", "N2:N4", "O2:O4", "P2:P4"} <= ranges
    assert detail.data_validations.dataValidation[0].formula1 == '"yes,no"'
    assert [detail.cell(r, 16).value for r in (2, 3, 4)] == ["yes", None, None]
    assert detail.cell(4, 2).data_type == "s"                                       # scraped text never becomes a formula
    assert wb["History"].cell(2, 8).value == -4.0

    changes, refused = read_approved(path)
    assert [(c["sku"], c["new_price"], c["change_pct"]) for c in changes] == [("SKU-1", "48.00", "-4.00")]
    assert changes[0]["reason"] == "lower (lowest usable competitor A at 48.00)"

    detail.cell(3, 16).value = "yes"                                                # reviewer approves SKU-2 ...
    detail.cell(3, 12).value = 60                                                   # ... at 60: under its 66.67 floor
    detail.cell(4, 16).value = "yes"                                                # hold row: nothing to approve
    wb.save(path)
    changes, refused = read_approved(path)
    assert [c["sku"] for c in changes] == ["SKU-1"]
    assert "below the margin floor 66.67" in refused[0] and "no suggested price" in refused[1]

    csv_path = write_changes(tmp_path / "approved.csv", changes)
    assert csv_path.read_text(encoding="utf-8-sig").splitlines()[0] == "sku,name,current_price,new_price,change_pct,reason"
    adapter = DryRunAdapter()
    results = adapter.push(read_changes(csv_path))
    assert results[0].ok and "50.00 -> 48.00" in results[0].message and adapter.sent[0].sku == "SKU-1"

    detail.append([c.value for c in detail[2]])                                     # SKU-1 row pasted twice
    detail.cell(2, 8).value = "n/a"                                                 # text in Competitor price
    wb.save(path)
    changes, refused = read_approved(path)
    assert [c["sku"] for c in changes] == ["SKU-1"] and changes[0]["reason"] == "lower"
    assert "more than one row" in refused[-1]

    detail.cell(2, 3).value = None                                                  # reviewer cleared Our price
    detail.cell(3, 12).value = 0
    wb.save(path)
    changes, refused = read_approved(path)                                          # warnings, not a crash
    assert "not usable numbers" in refused[0] and "not above zero" in refused[1]


def test_report_keeps_scraped_text_and_links_inert(tmp_path):
    bad = Offer("A", "=cmd|' /C calc'!A0", 45.0, True, "file:///C:/Windows/System32/calc.exe")
    gone = Offer("=X", "Widget", 40.0, False, "https://x.test/w")
    history = [{"run_id": 1, "at": "t", "note": "=1+1", "sku": "SKU-1", "source": "A", "product": "x",
                "url": "javascript:alert(1)", "price": 45.0, "in_stock": True, "change": None}]
    path = write_report(tmp_path / "r.xlsx", [suggest(P, [bad, gone], {})], history,
                        {"label": "DEMO", "warnings": ["=SUM(1,2)"]}, Settings())
    wb = load_workbook(path)
    product, flags = wb["Detail"].cell(2, 9), wb["Detail"].cell(2, 15)
    assert product.value.startswith("=cmd") and product.data_type == "s" and product.hyperlink is None
    assert flags.value.startswith("=X is out of stock") and flags.data_type == "s"
    assert wb["History"].cell(2, 2).data_type == "s" and wb["History"].cell(2, 5).hyperlink is None
    warning = [c for row in wb["Summary"].iter_rows() for c in row if c.value == "=SUM(1,2)"]
    assert warning and warning[0].data_type == "s"


# --- command line -------------------------------------------------------------------------------------------------

def test_cli_suggest_run_twice_and_approve(tmp_path):
    mirror = tmp_path / "pages"
    shutil.copytree(FIX, mirror)
    cat = write(tmp_path / "catalogue.csv",
                "sku,name,our_price,cost,min_margin_pct\n"
                "DEMO-1,Sharp Objects,49.99,30,25\n"
                "DEMO-2,In a Dark Dark Wood,24.00,12,20\n"
                "DEMO-3,عطر عود ملكي 100 مل,1300,900,20\n"
                "DEMO-4,Not sold by anyone,10,5,20\n")
    sources = {"sources": [vars(books()), vars(souq())]}
    src = write(tmp_path / "sources.json", json.dumps(sources, ensure_ascii=False))
    common = ["--catalogue", str(cat), "--sources", str(src), "--mirror", str(mirror)]

    assert main(["suggest-matches", *common, "--out", str(tmp_path / "s.csv")]) == 0
    with (tmp_path / "s.csv").open(encoding="utf-8-sig") as f:
        suggested = list(csv.DictReader(f))
    assert {r["sku"] for r in suggested} == {"DEMO-1", "DEMO-2", "DEMO-3"}
    for r in suggested:
        r["approved"] = "yes"
    with (tmp_path / "matches.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, SUGGESTION_COLUMNS)
        w.writeheader()
        w.writerows(suggested)

    run = ["run", *common, "--matches", str(tmp_path / "matches.csv"), "--db", str(tmp_path / "h.sqlite"),
           "--auto-approve-pct", "10", "--label", "DEMO DATA"]
    assert main(run + ["--out-dir", str(tmp_path / "r1"), "--note", "first"]) == 0
    page = mirror / "books.toscrape.com/catalogue/category/books/mystery_3/index.html"
    write(page, page.read_text(encoding="utf-8").replace("£47.82", "£45.00"))
    assert main(run + ["--out-dir", str(tmp_path / "r2"), "--note", "second"]) == 0

    detail = {r[0]: r for r in load_workbook(tmp_path / "r2/gap_report.xlsx")["Detail"].iter_rows(min_row=2, values_only=True)}
    assert "books: 47.82 -> 45.00" in detail["DEMO-1"][14]
    assert detail["DEMO-1"][11] == 45.0 and detail["DEMO-4"][13] == "hold"
    with (tmp_path / "r2/approved_changes.csv").open(encoding="utf-8-sig") as f:
        approved = {r["sku"]: r for r in csv.DictReader(f)}
    assert approved["DEMO-1"]["new_price"] == "45.00" and "DEMO-2" not in approved   # 24 -> 19.63 is -18%: review
    history = load_workbook(tmp_path / "r2/gap_report.xlsx")["History"]
    assert {row[1] for row in history.iter_rows(min_row=2, values_only=True)} == {"first", "second"}

    out = tmp_path / "manual.csv"
    assert main(["approve", str(tmp_path / "r2/gap_report.xlsx"), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8-sig") == (tmp_path / "r2/approved_changes.csv").read_text(encoding="utf-8-sig")


def test_match_suggestions_csv_cannot_carry_formulas(tmp_path):
    page = mirror_path(tmp_path / "pages", "https://shop.test/list.html")
    page.parent.mkdir(parents=True)
    write(page, '<div class="p"><a href="/oud">=HYPERLINK("https://evil.test","Royal Oud")</a><span class="c">10</span></div>')
    cfg = {"sources": [{"competitor": "shop", "urls": ["https://shop.test/list.html"], "card": "div.p", "name": "a",
                        "price": "span.c", "link": "a@href"}]}
    common = ["--catalogue", str(write(tmp_path / "c.csv", "sku,name,our_price,cost,min_margin_pct\nS1,Royal Oud,12,5,20\n")),
              "--sources", str(write(tmp_path / "s.json", json.dumps(cfg))), "--mirror", str(tmp_path / "pages")]
    assert main(["suggest-matches", *common, "--min-score", "0", "--out", str(tmp_path / "out.csv")]) == 0
    with (tmp_path / "out.csv").open(encoding="utf-8-sig") as f:
        row = next(csv.DictReader(f))
    assert row["competitor_name"].startswith("'=HYPERLINK") and row["competitor_url"] == "https://shop.test/oud"
