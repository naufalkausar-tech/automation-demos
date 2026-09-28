import argparse
import datetime as dt
import sys
from pathlib import Path

from .core import DEFAULT_BANKS, DEFAULT_ORGS, build_workbook, dashboard_figures
from .preview import dashboard_html, dashboard_markdown, render_png
from .sample import sample_data


def main(argv=None):
    p = argparse.ArgumentParser(prog="fleet_accounts", description="Generate the Arabic (RTL) vehicle-rental accounting "
                                "workbook: fleet sheets per organisation, income/expenses, annual fees, SUMIFS dashboard.")
    p.add_argument("out", help="output .xlsx")
    p.add_argument("--orgs", nargs="+", default=DEFAULT_ORGS, help="organisation names, one fleet sheet each (max 31 chars)")
    p.add_argument("--banks", nargs="+", default=DEFAULT_BANKS, help="bank account names for the dropdown")
    p.add_argument("--year", type=int, default=dt.date.today().year, help="dashboard period = this calendar year")
    p.add_argument("--sample", action="store_true", help="fill with clearly labelled SAMPLE rows (demo only)")
    p.add_argument("--preview", action="store_true",
                   help="also write <out>_dashboard.md and <out>_dashboard.png (PNG needs Google Chrome)")
    a = p.parse_args(argv)
    out = Path(a.out)
    if out.suffix.lower() != ".xlsx":
        p.error("the output file must end in .xlsx")
    if not 1901 <= a.year <= 9998:  # Excel dates start in 1900; the sample also writes the year before
        p.error("--year must be between 1901 and 9998")
    period = (dt.date(a.year, 1, 1), dt.date(a.year, 12, 31))
    data = sample_data(a.orgs, a.banks, a.year) if a.sample else None
    try:
        build_workbook(out, a.orgs, a.banks, data=data, period=period, sample=a.sample)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {out}")
    if a.preview:
        figures = dashboard_figures(data or {}, a.orgs, a.banks, *period)
        md = out.with_name(out.stem + "_dashboard.md")  # not with_suffix: "accounts.v2" must keep its ".v2"
        md.write_text(dashboard_markdown(figures, period, a.sample), encoding="utf-8")
        png = render_png(dashboard_html(figures, period, a.sample), out.with_name(out.stem + "_dashboard.png"))
        print(f"wrote {md}" + (f" and {png}" if png else " (no PNG: Chrome not found or failed)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
