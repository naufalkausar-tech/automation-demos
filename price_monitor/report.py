"""Excel gap report (Summary, Detail, History) and the CSV of approved price changes.

The Detail sheet is also the approval form: a reviewer sets Approve to yes/no (and may edit Suggested price),
then `approve` reads it back, re-checks every price against the margin floor and writes the import CSV.
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from scrape_kit.export import write_csv

from .core import FLAG_LABELS, YES, Settings, Suggestion, parse_price

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFF")
RED = PatternFill("solid", fgColor="F8CBAD")
GREEN = PatternFill("solid", fgColor="C6EFCE")
BLUE = PatternFill("solid", fgColor="BDD7EE")
AMBER = PatternFill("solid", fgColor="FFE699")
MONEY, PCT = "#,##0.00", "0.0%"

DETAIL = [("SKU", 14), ("Name", 34), ("Our price", 11), ("Cost", 9), ("Min margin", 10), ("Floor price", 11),
          ("Lowest competitor", 22), ("Competitor price", 12), ("Competitor product", 34), ("Gap %", 9),
          ("All matched offers", 44), ("Suggested price", 12), ("Change %", 9), ("Action", 9), ("Flags", 60),
          ("Approve", 9)]
HISTORY = [("Run at", 18), ("Run note", 40), ("SKU", 14), ("Source", 24), ("Product", 40), ("Price", 11),
           ("In stock", 9), ("Change", 10)]
CHANGE_COLUMNS = ["sku", "name", "current_price", "new_price", "change_pct", "reason"]


def _clean(value):
    return ILLEGAL_CHARACTERS_RE.sub("", value) if isinstance(value, str) else value


def _append(ws, values, formats=()):
    ws.append([_clean(v) for v in values])
    for i, cell in enumerate(ws[ws.max_row]):       # every cell, not only the ones with a number format
        if isinstance(cell.value, str) and cell.value.startswith("="):
            cell.data_type = "s"          # scraped names are text, never formulas
        if i < len(formats) and formats[i]:
            cell.number_format = formats[i]


def _link(cell, url):
    if url and url.lower().startswith(("http://", "https://")):     # scraped: never file:, UNC or javascript: links
        cell.hyperlink, cell.style = url, "Hyperlink"


def _header(ws, columns, freeze="A2"):
    ws.append([name for name, _ in columns])
    for cell in ws[1]:
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for i, (_, width) in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = freeze


def _stock(value):
    return "" if value is None else "yes" if value else "no"


def _offer_text(s: Suggestion) -> str:
    return "; ".join(f"{o.competitor}: {'?' if o.price is None else f'{o.price:.2f}'}"
                     f"{' (out of stock)' if o.in_stock is False else ''}" for o in s.offers)


def _rule_text(settings: Settings) -> str:
    if settings.undercut_pct:
        how = f"Undercut the lowest usable competitor price by {settings.undercut_pct:g}%"
    elif settings.undercut_amount:
        how = f"Undercut the lowest usable competitor price by {settings.undercut_amount:.2f}"
    else:
        how = "Match the lowest usable competitor price"
    return how + ", never below the margin floor"


def write_report(path: str | Path, suggestions: list[Suggestion], history: list[dict], info: dict,
                 settings: Settings) -> Path:
    wb = Workbook()
    _summary(wb.active, suggestions, info, settings)
    _detail(wb.create_sheet("Detail"), suggestions, settings)
    _history(wb.create_sheet("History"), history)
    wb.properties.title = "Competitor price gap report"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _summary(ws, suggestions, info, settings):
    ws.title = "Summary"
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 46, 90
    ws.append(["Competitor price gap report"])
    ws["A1"].font = Font(bold=True, size=14)
    _append(ws, [info.get("label", "")])
    ws["A2"].font = Font(bold=True, color="C00000")
    priced = [s for s in suggestions if s.target]
    count = lambda action: sum(1 for s in suggestions if s.action == action)
    approve = (f"changes up to {settings.auto_approve_pct:g}% without a pricing-error flag are pre-approved"
               if settings.auto_approve_pct else "none: a person approves every change")
    rows = [
        ("Generated", info.get("generated", "")),
        ("Run note", info.get("note", "")),
        ("Competitor sources", ", ".join(info.get("sources", []))),
        ("Pages read / offers collected", f"{info.get('pages', 0)} / {info.get('offers', 0)}"),
        ("SKUs in catalogue", len(suggestions)),
        ("SKUs with a usable competitor price", len(priced)),
        ("We are more expensive than the lowest competitor", sum(1 for s in priced if s.gap_pct > 0.00005)),
        (f"We are more than {settings.cheaper_pct:g}% cheaper", sum(1 for s in priced if s.gap_pct <= -settings.cheaper_pct / 100)),
        ("Suggested raises / cuts / keep / hold", f"{count('raise')} / {count('lower')} / {count('keep')} / {count('hold')}"),
        ("Pre-approved changes", sum(1 for s in suggestions if s.approve)),
        ("Pricing rule", _rule_text(settings)),
        ("Margin floor", "cost / (1 - min margin); min margin is gross margin on the selling price"),
        ("Auto-approval policy", approve),
        ("Suspect competitor prices", f"ignored when they moved {settings.error_pct:g}% since the last run "
                                      f"or sit more than {settings.error_pct:g}% below our cost"),
    ]
    ws.append([])
    for key, value in rows:
        _append(ws, [key, value])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
    ws.append([])
    ws.append(["Flag", "SKUs"])
    for cell in ws[ws.max_row]:
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
    for code, label in FLAG_LABELS.items():
        ws.append([label, sum(1 for s in suggestions if any(c == code for c, _ in s.flags))])
    ws.append([])
    ws.append(["Next step", "Set Approve to yes/no on the Detail sheet, then run: python -m price_monitor approve "
                            "gap_report.xlsx --out approved_changes.csv (every price is re-checked against the floor)"])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    for warning in info.get("warnings", []):
        _append(ws, ["Warning", warning])
        ws.cell(ws.max_row, 1).font = Font(bold=True, color="C00000")
    for row in ws.iter_rows(min_row=4):
        row[1].alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")


def _detail(ws, suggestions, settings):
    _header(ws, DETAIL, freeze="C2")
    formats = (None, None, MONEY, MONEY, PCT, MONEY, None, MONEY, None, PCT, None, MONEY, PCT)
    for s in suggestions:
        p, t = s.product, s.target
        _append(ws, [p.sku, p.name, p.our_price, p.cost, p.min_margin_pct / 100, p.floor,
                     t.competitor if t else None, t.price if t else None, t.name if t else None, s.gap_pct,
                     _offer_text(s), s.suggested, s.change_pct, s.action, "\n".join(text for _, text in s.flags),
                     "yes" if s.approve else None], formats)
        if t:
            _link(ws.cell(ws.max_row, 9), t.url)
        ws.cell(ws.max_row, 15).alignment = Alignment(wrap_text=True, vertical="top")
    last = max(ws.max_row, 2)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(DETAIL))}{last}"
    ws.conditional_formatting.add(f"J2:J{last}", CellIsRule(operator="greaterThan", formula=["0.00005"], fill=RED))
    ws.conditional_formatting.add(f"J2:J{last}", CellIsRule(operator="lessThanOrEqual",
                                                            formula=[str(-settings.cheaper_pct / 100)], fill=BLUE))
    ws.conditional_formatting.add(f"N2:N{last}", CellIsRule(operator="equal", formula=['"lower"'], font=Font(color="C00000", bold=True)))
    ws.conditional_formatting.add(f"N2:N{last}", CellIsRule(operator="equal", formula=['"raise"'], font=Font(color="006100", bold=True)))
    ws.conditional_formatting.add(f"O2:O{last}", FormulaRule(formula=["LEN($O2)>0"], fill=AMBER))
    ws.conditional_formatting.add(f"P2:P{last}", CellIsRule(operator="equal", formula=['"yes"'], fill=GREEN))
    choice = DataValidation(type="list", formula1='"yes,no"', allow_blank=True)
    ws.add_data_validation(choice)
    choice.add(f"P2:P{last}")


def _history(ws, history):
    _header(ws, HISTORY)
    for h in history:
        _append(ws, [h["at"], h["note"], h["sku"], h["source"], h["product"], h["price"], _stock(h["in_stock"]),
                     h["change"]], (None, None, None, None, None, MONEY, None, MONEY))
        _link(ws.cell(ws.max_row, 5), h["url"])
    last = max(ws.max_row, 2)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HISTORY))}{last}"
    ws.conditional_formatting.add(f"H2:H{last}", CellIsRule(operator="greaterThan", formula=["0"], fill=RED))
    ws.conditional_formatting.add(f"H2:H{last}", CellIsRule(operator="lessThan", formula=["0"], fill=GREEN))


def read_approved(path: str | Path) -> tuple[list[dict], list[str]]:
    """Rows marked Approve = yes on the Detail sheet -> price changes. Prices under the floor are refused."""
    wb = load_workbook(path, data_only=True)
    rows = wb["Detail"].iter_rows(values_only=True)
    col = {name: i for i, name in enumerate(next(rows))}
    missing = {"SKU", "Name", "Our price", "Floor price", "Lowest competitor", "Competitor price", "Suggested price",
               "Approve"} - set(col)
    if missing:
        raise ValueError(f"{path}: the Detail sheet has no {', '.join(sorted(missing))} column(s)")
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    changes, warnings = [], []
    for r in rows:
        if str(r[col["Approve"]] or "").strip().casefold() not in YES:
            continue
        sku, ours, floor = r[col["SKU"]], r[col["Our price"]], r[col["Floor price"]]
        new = r[col["Suggested price"]]
        new = parse_price(new) if isinstance(new, str) else new
        if any(c["sku"] == sku for c in changes):
            warnings.append(f"{sku}: approved on more than one row; only the first is kept")
        elif not (number(ours) and number(floor)) or ours <= 0:
            warnings.append(f"{sku}: approved but its Our price / Floor price cells are not usable numbers; skipped")
        elif not number(new):
            warnings.append(f"{sku}: approved but has no suggested price; skipped")
        elif new <= 0:
            warnings.append(f"{sku}: approved price {new:.2f} is not above zero; skipped")
        elif new < floor - 1e-9:
            warnings.append(f"{sku}: approved price {new:.2f} is below the margin floor {floor:.2f}; skipped")
        elif abs(new - ours) >= 0.005:
            competitor, their = r[col["Lowest competitor"]], r[col["Competitor price"]]
            reason = ("raise" if new > ours else "lower") + (f" to the margin floor {floor:.2f}" if abs(new - floor) < 0.005 else "")
            if competitor and number(their):
                reason += f" (lowest usable competitor {competitor} at {their:.2f})"
            changes.append({"sku": sku, "name": r[col["Name"]], "current_price": f"{ours:.2f}", "new_price": f"{new:.2f}",
                            "change_pct": f"{(new / ours - 1) * 100:.2f}", "reason": reason})
    return changes, warnings


def write_changes(path: str | Path, changes: list[dict]) -> Path:
    return write_csv(changes, path, fieldnames=CHANGE_COLUMNS)
