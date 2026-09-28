import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from scrape_kit.export import write_csv
from scrape_kit.polite import PoliteSession

from .collect import collect, http_fetcher, load_sources, mirror_fetcher
from .core import Settings, load_catalogue, load_matches, resolve, suggest, suggest_matches
from .history import history_rows, open_db, previous_offers, record_run
from .report import read_approved, write_changes, write_report

DEFAULT_UA = "price-monitor/1.0 (reads public listing pages politely; set --user-agent)"
SUGGESTION_COLUMNS = ["sku", "our_name", "competitor", "competitor_name", "competitor_url", "score", "approved"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="price_monitor", description="Competitor price monitoring + repricing suggestions")
    sub = p.add_subparsers(dest="command", required=True)
    fetching = argparse.ArgumentParser(add_help=False)
    fetching.add_argument("--catalogue", required=True, help="our catalogue CSV: sku,name,our_price,cost,min_margin_pct")
    fetching.add_argument("--sources", required=True, help="competitor source config (JSON)")
    fetching.add_argument("--mirror", help="read pages from this saved-pages folder instead of the network")
    fetching.add_argument("--save-pages", help="save every fetched page here (evidence + offline mirror)")
    fetching.add_argument("--min-interval", type=float, default=2.0, help="seconds between requests per host")
    fetching.add_argument("--user-agent", default=DEFAULT_UA)

    s = sub.add_parser("suggest-matches", parents=[fetching], help="propose SKU <-> competitor product pairs to approve")
    s.add_argument("--matches", help="existing match table; pairs already matched are skipped")
    s.add_argument("--min-score", type=float, default=0.6)
    s.add_argument("--out", required=True)

    r = sub.add_parser("run", parents=[fetching], help="collect prices, compare, write gap report + approved CSV")
    r.add_argument("--matches", required=True, help="approved match table CSV")
    r.add_argument("--db", required=True, help="SQLite price history (created if missing)")
    r.add_argument("--out-dir", required=True)
    r.add_argument("--undercut-pct", type=float, default=0.0)
    r.add_argument("--undercut-amount", type=float, default=0.0)
    r.add_argument("--cheaper-pct", type=float, default=10.0, help="flag when we are this %% below the lowest competitor")
    r.add_argument("--error-pct", type=float, default=50.0, help="treat competitor prices off by this %% as suspect")
    r.add_argument("--auto-approve-pct", type=float, default=0.0,
                   help="pre-approve changes up to this %% (default 0: a person approves every change)")
    r.add_argument("--note", default="", help="stored with this run in the history")
    r.add_argument("--label", default="", help="banner on the Summary sheet, e.g. 'DEMO DATA'")

    a = sub.add_parser("approve", help="read a reviewed gap_report.xlsx and write the approved-changes CSV")
    a.add_argument("report")
    a.add_argument("--out", required=True)
    return p


def main(argv=None, polite: PoliteSession | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "approve":
        changes, refused = read_approved(args.report)
        for warning in refused:
            print(f"warning: {warning}", file=sys.stderr)
        write_changes(args.out, changes)
        print(f"{len(changes)} approved change(s) -> {args.out}")
        return 0

    products = load_catalogue(args.catalogue)
    sources = load_sources(args.sources)
    if args.mirror:
        fetch = mirror_fetcher(args.mirror)
    else:
        polite = polite or PoliteSession(user_agent=args.user_agent, min_interval=args.min_interval, max_retries=2)
        fetch = http_fetcher(polite, args.save_pages)
    collected = collect(sources, fetch)
    for warning in collected.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if not collected.pages:
        print("no competitor page could be read; nothing recorded", file=sys.stderr)
        return 1

    if args.command == "suggest-matches":
        existing = load_matches(args.matches) if args.matches else []
        rows = suggest_matches(products, collected.offers, existing, args.min_score)
        # the reviewer opens this in Excel and competitor_name is scraped text: '=...' must not run as a formula
        write_csv(rows, args.out, fieldnames=SUGGESTION_COLUMNS, escape_formulas=True)
        print(f"{len(rows)} suggested pair(s) from {len(collected.offers)} offers -> {args.out}; "
              "set approved=yes on the pairs you accept, then use the file as --matches")
        return 0

    matches = load_matches(args.matches)
    linked, missing = resolve(matches, collected.offers)
    warnings = collected.warnings + [f"match {m.sku} -> {m.url or m.name} not found this run (delisted, renamed or beyond max_pages)"
                                     for m in missing]
    warnings += [f"match table SKU {sku} is not in the catalogue" for sku in sorted({m.sku for m in matches} - {p.sku for p in products})]
    settings = Settings(args.undercut_pct, args.undercut_amount, args.cheaper_pct, args.error_pct, args.auto_approve_pct)
    at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    conn = open_db(args.db)
    try:
        previous = previous_offers(conn)
        suggestions = [suggest(p, linked.get(p.sku, []), previous, settings) for p in products]
        record_run(conn, collected.offers, products, linked, note=args.note, at=at)
        history = history_rows(conn)
    finally:
        conn.close()

    out = Path(args.out_dir)
    info = {"generated": at, "note": args.note, "label": args.label, "sources": [s.competitor for s in sources],
            "pages": collected.pages, "offers": len(collected.offers), "warnings": warnings}
    report = write_report(out / "gap_report.xlsx", suggestions, history, info, settings)
    changes, _ = read_approved(report)
    write_changes(out / "approved_changes.csv", changes)
    priced = sum(1 for s in suggestions if s.target)
    print(f"{len(products)} SKUs, {priced} with a competitor price, {len(changes)} pre-approved change(s) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
