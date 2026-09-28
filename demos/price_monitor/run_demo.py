"""Portfolio demo for price_monitor. All "our" data here is DEMO DATA (see README.md).

1. Fetch 2 category listings from books.toscrape.com, a public sandbox built for scraping practice,
   politely (robots.txt, 3 s between requests, named User-Agent) and save each page under pages/.
   No network: reuse pages/ from an earlier run, else fall back to the package's saved test fixtures.
2. Suggest SKU <-> listing pairs for review (match_suggestions.csv). matches.csv holds the reviewed pairs.
3. Seed history.sqlite with a SYNTHETIC earlier snapshot (4 live prices edited, dated 7 days back) so the
   report can show change detection and the pricing-error guard.
4. Run the monitor on the snapshot -> gap_report.xlsx + approved_changes.csv.
5. Arabic mini demo on the synthetic Arabic fixture store -> arabic_fixture/, including a scripted
   reviewer pass (gap_report_reviewed.xlsx) and the `approve` step that re-checks the margin floor.

    python run_demo.py             # live fetch (robots.txt + 3 pages)
    python run_demo.py --offline   # reuse pages/
"""
import argparse
import dataclasses
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openpyxl import load_workbook

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # repository root, where the packages live
sys.path.insert(0, str(ROOT))

from price_monitor import (collect, http_fetcher, load_catalogue, load_matches, load_sources,  # noqa: E402
                           mirror_fetcher, open_db, record_run, resolve)
from price_monitor.cli import main  # noqa: E402
from scrape_kit.polite import PoliteSession  # noqa: E402

FIXTURES = ROOT / "price_monitor" / "fixtures"
UA = "price-monitor-demo/1.0 (portfolio demo against the books.toscrape.com scraping sandbox)"
LABEL = ("DEMO DATA: 'our catalogue' (SKUs DEMO-*, our prices, costs, margins) is synthetic. Competitor prices "
         "come from books.toscrape.com, a public sandbox built for scraping practice. The earlier history run "
         "is a synthetic snapshot.")
# synthetic prices for the earlier snapshot, by product URL fragment (the rest keep today's price)
EARLIER = {"sharp-objects_997": 49.50, "the-great-railway-bazaar_446": 34.90,
           "under-the-tuscan-sun_504": 36.00, "the-exiled_247": 95.00}


def snapshot(offline: bool) -> tuple[Path, str]:
    pages = HERE / "pages"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not offline:
        # fetch into a staging folder so a half-failed live run never mixes new pages into the saved snapshot
        fresh = HERE / "pages.new"
        shutil.rmtree(fresh, ignore_errors=True)
        polite = PoliteSession(user_agent=UA, min_interval=3.0, max_retries=1)
        got = collect(load_sources(HERE / "sources.json"), http_fetcher(polite, fresh))
        if got.pages and not got.warnings:
            shutil.rmtree(pages, ignore_errors=True)
            fresh.rename(pages)
            return pages, f"books.toscrape.com fetched live {today}; pages saved under pages/"
        shutil.rmtree(fresh, ignore_errors=True)
        print("live fetch incomplete:", *got.warnings, sep="\n  ")
    if (pages / "books.toscrape.com").is_dir():
        return pages, "books.toscrape.com replayed from the saved pages/ snapshot (no live fetch this run)"
    return FIXTURES, "FALLBACK: network unavailable, package test fixtures used (few matches resolve)"


def seed_earlier_run(mirror: Path, db: Path) -> None:
    offers = collect(load_sources(HERE / "sources.json"), mirror_fetcher(mirror)).offers
    linked, _ = resolve(load_matches(HERE / "matches.csv"), offers)
    earlier = [dataclasses.replace(o, price=next((p for k, p in EARLIER.items() if k in o.url), o.price)) for o in offers]
    at = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M UTC")
    conn = open_db(db)
    record_run(conn, earlier, load_catalogue(HERE / "demo_catalogue.csv"), linked, at=at,
               note="DEMO: synthetic earlier snapshot (4 prices edited, dated 7 days back)")
    conn.close()


def arabic_demo() -> None:
    out = HERE / "arabic_fixture"
    (out / "history_ar.sqlite").unlink(missing_ok=True)
    common = ["--catalogue", str(out / "catalogue_ar.csv"), "--sources", str(out / "sources_ar.json"),
              "--mirror", str(FIXTURES)]
    main(["suggest-matches", *common, "--min-score", "0.5", "--out", str(out / "match_suggestions_ar.csv")])
    main(["run", *common, "--matches", str(out / "matches_ar.csv"), "--db", str(out / "history_ar.sqlite"),
          "--out-dir", str(out), "--auto-approve-pct", "5", "--note", "synthetic Arabic fixture store",
          "--label", "SYNTHETIC DEMO: souq-demo.test is an invented store (test fixture); all names and prices are made up."])
    # scripted reviewer: accept AR-001, accept AR-003 at a softer 85.00, try AR-005 at 145.00 (under its floor)
    wb = load_workbook(out / "gap_report.xlsx")
    detail = wb["Detail"]
    decisions = {"DEMO-AR-001": None, "DEMO-AR-003": 85.00, "DEMO-AR-005": 145.00}
    for row in detail.iter_rows(min_row=2):
        if row[0].value in decisions:
            row[15].value = "yes"
            if decisions[row[0].value] is not None:
                row[11].value = decisions[row[0].value]
    wb.save(out / "gap_report_reviewed.xlsx")
    main(["approve", str(out / "gap_report_reviewed.xlsx"), "--out", str(out / "approved_changes.csv")])


def run(offline: bool) -> int:
    mirror, note = snapshot(offline)
    print(note)
    common = ["--catalogue", str(HERE / "demo_catalogue.csv"), "--sources", str(HERE / "sources.json"),
              "--mirror", str(mirror)]
    main(["suggest-matches", *common, "--min-score", "0.5", "--out", str(HERE / "match_suggestions.csv")])
    db = HERE / "history.sqlite"
    db.unlink(missing_ok=True)
    seed_earlier_run(mirror, db)
    code = main(["run", *common, "--matches", str(HERE / "matches.csv"), "--db", str(db), "--out-dir", str(HERE),
                 "--auto-approve-pct", "5", "--note", note, "--label", LABEL])
    arabic_demo()
    return code


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="do not fetch; reuse pages/ (or the package fixtures)")
    sys.exit(run(ap.parse_args().offline))
