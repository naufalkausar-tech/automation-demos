"""Arabic (right-to-left) accounting workbook for organisations that rent vehicles to clients.

Sheets: a dashboard, one identical fleet sheet per organisation (clients, vehicles, rent, maintenance deduction,
net rent), income and expense ledgers, an annual-fees tracker and a hidden 'lists' sheet feeding every dropdown.
Every data block is an Excel Table, so rows typed under it join the table and are picked up by the dashboard's
SUMIFS without editing a formula. All results are live formulas; no macros.

Bank balance rule: income received (status مدفوع) minus expenses PAID (مدفوع) only, up to the period's end date:
the balance brought forward (paid rows dated before the period) plus the period's paid income minus its paid
expenses. Due (مستحق) and pending (معلّق) amounts get their own lines and never enter the balance. openpyxl cannot
evaluate formulas, so `summarize` and `dashboard_figures` mirror the dashboard in Python; the tests check the
formula text against that mirror.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.drawing.line import LineProperties
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.filters import AutoFilter
from openpyxl.worksheet.table import Table, TableColumn, TableFormula, TableStyleInfo

PAID, DUE, PENDING = "مدفوع", "مستحق", "معلّق"
PAY_STATUSES = [PAID, DUE, PENDING]
ACTIVE = "ساري"
CONTRACT_STATUSES = [ACTIVE, "منتهي", "موقوف", "قيد التجديد"]
VEHICLE_TYPES = ["سيدان", "دفع رباعي", "بيك أب", "فان", "حافلة صغيرة", "حافلة", "شاحنة"]
INCOME_CATEGORIES = ["إيجار شهري", "رصيد افتتاحي", "غرامة تأخير", "تعويض أضرار", "أخرى"]
EXPENSE_CATEGORIES = ["صيانة", "وقود", "تأمين", "رواتب سائقين", "رسوم حكومية", "أقساط مركبات", "إيجار مكتب", "أخرى"]
ALL = "الكل"
DEFAULT_ORGS = ["المؤسسة الأولى", "المؤسسة الثانية", "المؤسسة الثالثة"]
DEFAULT_BANKS = ["الحساب البنكي 1", "الحساب البنكي 2"]
SAMPLE_TAG = "بيانات تجريبية للعرض فقط - SAMPLE / DEMO DATA"

DASHBOARD, INCOME, EXPENSES, FEES, LISTS = "لوحة التحكم", "الإيرادات", "المصروفات", "الرسوم السنوية", "lists"
T_INCOME, T_EXPENSES, T_FEES = "tblIncome", "tblExpenses", "tblFees"
HEADER_ROW = 3  # rows 1-2 hold the sheet title and a hint; tables start here
DV_LAST_ROW = 2000  # dropdowns reach well below the tables so auto-expanded rows keep them

DATE, MONEY, INT = "yyyy-mm-dd", "#,##0.00;[Red]-#,##0.00", "0"
FONT = "Arial"

# (field, header, width, number format)
FLEET_COLS = [
    ("client", "العميل / الجهة", 30, None),
    ("vehicle_type", "نوع المركبة", 14, None),
    ("count", "العدد", 8, INT),
    ("vehicle_no", "رقم المركبة", 13, "@"),
    ("plate", "رقم اللوحة", 14, "@"),
    ("handover", "تاريخ التسليم", 14, DATE),
    ("rent", "الإيجار الشهري", 15, MONEY),
    ("deduction", "خصم الصيانة", 14, MONEY),
    ("net", "صافي الإيجار", 15, MONEY),
    ("status", "حالة العقد", 13, None),
]
LEDGER_COLS = [
    ("date", "التاريخ", 12, DATE),
    ("org", "المؤسسة", 20, None),
    ("bank", "الحساب البنكي", 18, None),
    ("category", "التصنيف", 16, None),
    ("amount", "المبلغ", 14, MONEY),
    ("status", "الحالة", 10, None),
    ("notes", "ملاحظات", 40, None),
]
FEE_COLS = [
    ("fee", "الرسم", 30, None),
    ("org", "المؤسسة", 20, None),
    ("total", "الإجمالي", 14, MONEY),
    ("paid", "المدفوع", 14, MONEY),
    ("remaining", "المتبقي", 14, MONEY),
    ("notes", "ملاحظات", 40, None),
]
H = {f: h for cols in (FLEET_COLS, LEDGER_COLS, FEE_COLS) for f, h, *_ in cols}  # header by field
FLEET_H = {f: h for f, h, *_ in FLEET_COLS}

# calculated table columns: field -> (minuend, subtrahend)
FLEET_CALC = {"net": ("rent", "deduction")}
FEE_CALC = {"remaining": ("total", "paid")}

# dropdown columns: field -> defined name on the lists sheet
FLEET_DV = {"vehicle_type": "lst_vehicle_types", "status": "lst_contract_status"}
INCOME_DV = {"org": "lst_orgs", "bank": "lst_banks", "category": "lst_income_cats", "status": "lst_pay_status"}
EXPENSE_DV = {**INCOME_DV, "category": "lst_expense_cats"}
FEE_DV = {"org": "lst_orgs"}

# dashboard metrics, in column order: key, header, format. Ledger metrics are SUMIFS on (table, status).
LEDGER_METRICS = {
    "received": (T_INCOME, PAID), "paid": (T_EXPENSES, PAID),
    "receivables": (T_INCOME, DUE), "payables": (T_EXPENSES, DUE),
    "pending_income": (T_INCOME, PENDING), "pending_expense": (T_EXPENSES, PENDING),
}
METRICS = [
    ("bank_balance", "الرصيد البنكي الفعلي (حتى نهاية الفترة)", MONEY),
    ("opening", "رصيد أول المدة (مدفوع قبل الفترة)", MONEY),
    ("received", "إيرادات مستلمة (مدفوع)", MONEY),
    ("paid", "مصروفات مدفوعة", MONEY),
    ("receivables", "مستحقات لنا (إيرادات مستحقة)", MONEY),
    ("payables", "مستحقات علينا (مصروفات مستحقة)", MONEY),
    ("pending_income", "إيرادات معلّقة", MONEY),
    ("pending_expense", "مصروفات معلّقة", MONEY),
    ("net_rent", "صافي الإيجار الشهري (عقود سارية)", MONEY),
    ("vehicles", "عدد المركبات (عقود سارية)", INT),
    ("fees_remaining", "رسوم سنوية متبقية", MONEY),
]
BANK_METRICS = METRICS[:4]
RULE_NOTE = ("الرصيد البنكي الفعلي = رصيد أول المدة + الإيرادات المستلمة (مدفوع) − المصروفات المدفوعة فقط، "
             "أي كل ما دُفع فعلاً حتى تاريخ النهاية. "
             "المبالغ المستحقة والمعلّقة تظهر في سطورها ولا تدخل في الرصيد البنكي أبداً.")

# dashboard input cells
FROM_CELL, TO_CELL, ORG_CELL = "$B$4", "$D$4", "$F$4"

TITLE_FONT = Font(name=FONT, bold=True, size=14)
HINT_FONT = Font(name=FONT, italic=True, color="595959")
SAMPLE_FONT = Font(name=FONT, bold=True, size=14, color="C00000")
BOLD = Font(name=FONT, bold=True)
PLAIN = Font(name=FONT)
HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
HEAD_FONT = Font(name=FONT, bold=True, color="FFFFFF")
SECTION_FILL = PatternFill("solid", fgColor="DDEBF7")
INPUT_FILL = PatternFill("solid", fgColor="FFFF00")
CALC_FILL = PatternFill("solid", fgColor="EDEDED")
TABLE_STYLE = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
SERIES_COLORS = ["2A78D6", "EB6834", "1BAF7A"]  # validated categorical slots 1-3
INVALID_SHEET_CHARS = set('[]:*?/\\')
WILDCARDS = set("*?~")  # SUMIFS and MATCH treat these as patterns, so a name holding one matches other names


def fleet_table(index):
    return f"tblOrg{index}"


def _unique(names):
    # Excel compares sheet names and SUMIFS text case-insensitively
    return len({n.casefold() for n in names}) == len(names)


def check_names(orgs, banks):
    if not orgs or not banks:
        raise ValueError("need at least one organisation and one bank account")
    for name in orgs:
        if not name.strip() or len(name) > 31 or INVALID_SHEET_CHARS & set(name) or name[0] == "'" or name[-1] == "'":
            raise ValueError(f"organisation name {name!r} cannot be a sheet name (1-31 chars, none of []:*?/\\)")
    for name in [*orgs, *banks]:
        # names are SUMIFS criteria and are written as cell text: no patterns, no operators, no formulas
        if not name.strip() or len(name) > 255 or WILDCARDS & set(name) or name[0] in "=<>":
            raise ValueError(f"name {name!r} must be 1-255 chars without * ? ~ and must not start with = < >")
    fixed = {n.casefold() for n in (DASHBOARD, INCOME, EXPENSES, FEES, LISTS, ALL)}
    if not _unique(orgs) or {n.casefold() for n in orgs} & fixed:
        raise ValueError("organisation names must be unique and differ from the fixed sheet names and 'الكل'")
    if not _unique(banks):
        raise ValueError("bank account names must be unique")


def build_workbook(path, orgs=DEFAULT_ORGS, banks=DEFAULT_BANKS, data=None, period=None, sample=False):
    """Write the workbook. data = {"fleet": {org: [rows]}, "income": [...], "expenses": [...], "fees": [...]}
    with rows keyed by the field names in *_COLS; None writes an empty template. period = (from, to) dates for the
    dashboard (default: this calendar year). sample=True stamps every sheet as demo data."""
    orgs, banks = list(orgs), list(banks)
    check_names(orgs, banks)
    data = data or {}
    year = dt.date.today().year
    period = period or (dt.date(year, 1, 1), dt.date(year, 12, 31))

    wb = Workbook()
    dash = wb.active
    dash.title = DASHBOARD
    for i, org in enumerate(orgs, 1):
        ws = wb.create_sheet(org)
        _sheet_head(ws, f"العملاء والمركبات - {org}", "أضف العقود أسفل الجدول مباشرة وسيتمدد تلقائياً؛ العمود الرمادي محسوب.", sample)
        _write_table(ws, fleet_table(i), FLEET_COLS, data.get("fleet", {}).get(org), FLEET_DV, FLEET_CALC)
    for name, table, rows, dv in ((INCOME, T_INCOME, data.get("income"), INCOME_DV),
                                  (EXPENSES, T_EXPENSES, data.get("expenses"), EXPENSE_DV)):
        ws = wb.create_sheet(name)
        _sheet_head(ws, name, "الحالة: مدفوع = دخل/خرج من البنك فعلاً، مستحق = لم يُدفع بعد، معلّق = قيد المراجعة أو التحصيل.", sample)
        _write_table(ws, table, LEDGER_COLS, rows, dv)
    ws = wb.create_sheet(FEES)
    _sheet_head(ws, FEES, "سجّل كل دفعة رسوم أيضاً في المصروفات (تصنيف: رسوم حكومية) ليظهر أثرها على الرصيد البنكي.", sample)
    _write_table(ws, T_FEES, FEE_COLS, data.get("fees"), FEE_DV, FEE_CALC)
    _write_lists(wb, orgs, banks)
    _write_dashboard(dash, orgs, banks, period, sample)

    for ws in wb.worksheets:
        ws.sheet_view.rightToLeft = True
        ws.page_setup.orientation = "landscape"  # print one page wide
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
    wb.properties.title = "حسابات تأجير المركبات" + (f" ({SAMPLE_TAG})" if sample else "")
    path = Path(path)
    wb.save(path)
    return path


def _sheet_head(ws, title, hint, sample):
    ws["A1"] = f"{title} | {SAMPLE_TAG}" if sample else title
    ws["A1"].font = SAMPLE_FONT if sample else TITLE_FONT
    ws["A2"] = hint
    ws["A2"].font = HINT_FONT
    ws.freeze_panes = f"A{HEADER_ROW + 1}"


def row_formula(table, a, b):
    """Calculated-column formula a - b in table-row syntax (the file form of [@a]-[@b])."""
    return f"{table}[[#This Row],[{H[a]}]]-{table}[[#This Row],[{H[b]}]]"


def _write_table(ws, name, cols, rows, dropdowns, calc=None):
    rows = rows or [{}]  # an Excel Table needs at least one data row
    formulas = {field: row_formula(name, a, b) for field, (a, b) in (calc or {}).items()}
    fields = [c[0] for c in cols]
    for c, (field, header, width, fmt) in enumerate(cols, 1):
        ws.cell(HEADER_ROW, c, header)
        ws.column_dimensions[get_column_letter(c)].width = width
        for r, row in enumerate(rows, HEADER_ROW + 1):
            cell = ws.cell(r, c, "=" + formulas[field] if field in formulas else row.get(field))
            if field not in formulas and cell.data_type == "f":
                cell.data_type = "s"  # data text such as "=HYPERLINK(...)" stays text, never a live formula
            cell.font = PLAIN
            if fmt:
                cell.number_format = fmt
            if field in formulas:
                cell.fill = CALC_FILL
    ref = f"A{HEADER_ROW}:{get_column_letter(len(cols))}{HEADER_ROW + len(rows)}"
    columns = [TableColumn(id=i, name=header, calculatedColumnFormula=TableFormula(attr_text=formulas[field])
                           if field in formulas else None)
               for i, (field, header, *_) in enumerate(cols, 1)]
    ws.add_table(Table(displayName=name, ref=ref, autoFilter=AutoFilter(ref=ref),
                       tableStyleInfo=TABLE_STYLE, tableColumns=columns))

    def column(field):
        letter = get_column_letter(fields.index(field) + 1)
        return f"{letter}{HEADER_ROW + 1}:{letter}{DV_LAST_ROW}"

    for field, list_name in dropdowns.items():
        dv = DataValidation(type="list", formula1=list_name, allow_blank=True, showErrorMessage=True,
                            errorTitle="قيمة غير صحيحة", error="اختر قيمة من القائمة المنسدلة")
        dv.add(column(field))
        ws.add_data_validation(dv)
    for kind, fmt in (("decimal", MONEY), ("date", DATE)):
        targets = [f for f, _, _, f_fmt in cols if f_fmt == fmt and f not in formulas]
        if targets:
            dv = DataValidation(type=kind, operator="greaterThanOrEqual",
                                formula1="0" if kind == "decimal" else "DATE(2000,1,1)", allow_blank=True,
                                showErrorMessage=True, errorTitle="قيمة غير صحيحة",
                                error="أدخل مبلغاً موجباً" if kind == "decimal" else "أدخل تاريخاً صحيحاً")
            for f in targets:
                dv.add(column(f))
            ws.add_data_validation(dv)


def list_values(orgs, banks):
    """Defined name -> (column title on the lists sheet, values)."""
    return {
        "lst_orgs": ("المؤسسات", orgs),
        "lst_org_filter": ("فلتر لوحة التحكم", [ALL, *orgs]),
        "lst_banks": ("الحسابات البنكية", banks),
        "lst_vehicle_types": ("أنواع المركبات", VEHICLE_TYPES),
        "lst_contract_status": ("حالة العقد", CONTRACT_STATUSES),
        "lst_income_cats": ("تصنيفات الإيرادات", INCOME_CATEGORIES),
        "lst_expense_cats": ("تصنيفات المصروفات", EXPENSE_CATEGORIES),
        "lst_pay_status": ("حالة الدفع", PAY_STATUSES),
    }


def _write_lists(wb, orgs, banks):
    ws = wb.create_sheet(LISTS)
    ws.sheet_state = "hidden"
    for c, (name, (title, values)) in enumerate(list_values(orgs, banks).items(), 1):
        col = get_column_letter(c)
        ws.cell(1, c, title).font = BOLD
        for r, value in enumerate(values, 2):
            ws.cell(r, c, value)
        ws.column_dimensions[col].width = 22
        # grows with the column, so a bank or category added on this sheet appears in every dropdown
        wb.defined_names[name] = DefinedName(name, attr_text=f"OFFSET({LISTS}!${col}$2,0,0,COUNTA({LISTS}!${col}:${col})-1,1)")


def ledger_sumifs(table, status, match=None, before=False):
    """SUMIFS over a ledger table for one status inside the dashboard period (before=True: dated before the period
    start instead); match = (field, cell) narrows it to one organisation or bank account."""
    date = f'{table}[{H["date"]}]'
    crit = [f"{table}[{H[match[0]]}],{match[1]}"] if match else []
    crit.append(f'{table}[{H["status"]}],"{status}"')
    crit += [f'{date},"<"&{FROM_CELL}'] if before else [f'{date},">="&{FROM_CELL}', f'{date},"<="&{TO_CELL}']
    return f"SUMIFS({table}[{H['amount']}],{','.join(crit)})"


def ledger_values(col, r, match=None):
    """Formulas for the balance and ledger metrics of one dashboard row; col maps metric key -> column letter."""
    values = {"bank_balance": f"={col['opening']}{r}+{col['received']}{r}-{col['paid']}{r}",
              "opening": "=" + "-".join(ledger_sumifs(t, PAID, match, before=True) for t in (T_INCOME, T_EXPENSES))}
    for key, (table, status) in LEDGER_METRICS.items():
        values[key] = "=" + ledger_sumifs(table, status, match)
    return values


def _write_dashboard(ws, orgs, banks, period, sample):
    title = "لوحة التحكم المالية - تأجير المركبات"
    ws["A1"] = f"{title} | {SAMPLE_TAG}" if sample else title
    ws["A1"].font = SAMPLE_FONT if sample else Font(name=FONT, bold=True, size=16)
    ws["A2"] = "الخلايا الصفراء فقط للإدخال (الفترة والمؤسسة)؛ كل الأرقام الأخرى صيغ تُحسب من الجداول."
    ws["A2"].font = HINT_FONT
    for ref, value in (("A4", "من تاريخ"), ("C4", "إلى تاريخ"), ("E4", "المؤسسة")):
        ws[ref] = value
        ws[ref].font = BOLD
    for ref, value in ((FROM_CELL, period[0]), (TO_CELL, period[1]), (ORG_CELL, ALL)):
        cell = ws[ref.replace("$", "")]
        cell.value, cell.fill, cell.font = value, INPUT_FILL, BOLD
        if ref != ORG_CELL:
            cell.number_format = DATE
    dv = DataValidation(type="list", formula1="lst_org_filter", showErrorMessage=True,
                        errorTitle="قيمة غير صحيحة", error="اختر مؤسسة أو الكل")
    dv.add(ORG_CELL.replace("$", ""))
    ws.add_data_validation(dv)

    # comparison table: one row per organisation, then الكل (no organisation criterion)
    kpi_top = 7
    cmp_head = kpi_top + len(METRICS) + 3
    first, total = cmp_head + 1, cmp_head + 1 + len(orgs)
    col = {key: get_column_letter(i) for i, (key, *_) in enumerate(METRICS, 2)}
    _section(ws, cmp_head - 1, "مقارنة المؤسسات للفترة المحددة (الإيجار وعدد المركبات: العقود السارية حالياً)")
    _header_row(ws, cmp_head, ["المؤسسة"] + [h for _, h, _ in METRICS])
    for r in range(first, total + 1):
        i = r - first + 1
        org = orgs[i - 1] if r < total else ALL
        ws.cell(r, 1, org).font = BOLD
        values = ledger_values(col, r, ("org", f"$A{r}") if r < total else None)
        if r < total:
            t = fleet_table(i)
            active = f'{t}[{FLEET_H["status"]}],"{ACTIVE}"'
            values["net_rent"] = f"=SUMIFS({t}[{FLEET_H['net']}],{active})"
            values["vehicles"] = f"=SUMIFS({t}[{FLEET_H['count']}],{active})"
            values["fees_remaining"] = f"=SUMIFS({T_FEES}[{H['remaining']}],{T_FEES}[{H['org']}],$A{r})"
        else:
            values["net_rent"] = f"=SUM({col['net_rent']}{first}:{col['net_rent']}{total - 1})"
            values["vehicles"] = f"=SUM({col['vehicles']}{first}:{col['vehicles']}{total - 1})"
            values["fees_remaining"] = f"=SUM({T_FEES}[{H['remaining']}])"
        for key, _, fmt in METRICS:
            cell = ws[f"{col[key]}{r}"]
            cell.value, cell.number_format = values[key], fmt
            cell.font = BOLD if r == total or key == "bank_balance" else PLAIN

    # selected-organisation block: a lookup into the comparison table, so both always agree
    _section(ws, kpi_top - 1, "مؤشرات المؤسسة المختارة للفترة")
    for r, (key, header, fmt) in enumerate(METRICS, kpi_top):
        ws.row_dimensions[r].height = 20  # taller rows leave room for the chart beside this block
        ws.cell(r, 1, header).font = BOLD if key == "bank_balance" else PLAIN
        c = col[key]
        cell = ws.cell(r, 2, f"=INDEX({c}${first}:{c}${total},MATCH({ORG_CELL},$A${first}:$A${total},0))")
        cell.number_format, cell.font = fmt, BOLD if key == "bank_balance" else PLAIN
    note = ws.cell(kpi_top + len(METRICS), 1, RULE_NOTE)
    note.font = Font(name=FONT, bold=True, color="C00000")

    # bank accounts (all organisations)
    bank_head = total + 3
    _section(ws, bank_head - 1, "الرصيد حسب الحساب البنكي (كل المؤسسات)")
    _header_row(ws, bank_head, ["الحساب البنكي"] + [h for _, h, _ in BANK_METRICS])
    bank_col = {key: get_column_letter(i) for i, (key, *_) in enumerate(BANK_METRICS, 2)}
    for r, bank in enumerate(banks, bank_head + 1):
        ws.cell(r, 1, bank).font = BOLD
        values = ledger_values(bank_col, r, ("bank", f"$A{r}"))
        for key, _, fmt in BANK_METRICS:
            cell = ws[f"{bank_col[key]}{r}"]
            cell.value, cell.number_format = values[key], fmt
            cell.font = BOLD if key == "bank_balance" else PLAIN

    chart = BarChart()
    chart.type, chart.grouping, chart.title = "col", "clustered", "الرصيد والمستحقات حسب المؤسسة"
    for key, color in zip(("bank_balance", "receivables", "payables"), SERIES_COLORS):
        c = list(col).index(key) + 2
        chart.add_data(Reference(ws, min_col=c, min_row=cmp_head, max_row=total - 1), titles_from_data=True)
        chart.series[-1].graphicalProperties.solidFill = color
        chart.series[-1].graphicalProperties.line.noFill = True
    chart.set_categories(Reference(ws, min_col=1, min_row=first, max_row=total - 1))
    chart.x_axis.delete = chart.y_axis.delete = False  # openpyxl 3.1 otherwise leaves both axes hidden
    chart.x_axis.scaling.orientation = "maxMin"  # right-to-left: first organisation on the right, value axis too
    chart.y_axis.number_format = "#,##0"
    chart.y_axis.majorGridlines.spPr = GraphicalProperties(ln=LineProperties(solidFill="D9D9D9"))
    chart.legend.position, chart.legend.overlay, chart.title.overlay = "b", False, False
    chart.gapWidth, chart.width, chart.height = 80, 18, 8
    ws.add_chart(chart, f"D{kpi_top - 1}")

    ws.column_dimensions["A"].width = 36
    for c in range(2, len(METRICS) + 2):
        ws.column_dimensions[get_column_letter(c)].width = 17


def _section(ws, row, text):
    cell = ws.cell(row, 1, text)
    cell.font, cell.fill = BOLD, SECTION_FILL


def _header_row(ws, row, headers):
    for c, text in enumerate(headers, 1):
        cell = ws.cell(row, c, text)
        cell.font, cell.fill = HEAD_FONT, HEAD_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    ws.row_dimensions[row].height = 32


# ---- Python mirror of the dashboard formulas -------------------------------------------------------------

def _day(value):
    return value.date() if isinstance(value, dt.datetime) else value


def _sum(rows, status, in_range, **match):
    # SUMIFS semantics: blank dates never match a date condition; blank amounts count as 0
    return sum(r.get("amount") or 0 for r in rows
               if r.get("status") == status and r.get("date") and in_range(_day(r["date"]))
               and all(r.get(k) == v for k, v in match.items()))


def summarize(income, expenses, date_from, date_to, **match):
    """Ledger figures for the period; match=org=... or bank=... narrows them (nothing = all organisations).
    bank_balance = opening (PAID rows before the period) + received - paid: due and pending rows are reported
    but never enter it."""
    def in_period(d):
        return date_from <= d <= date_to

    def before(d):
        return d < date_from

    figures = {key: _sum(income if table == T_INCOME else expenses, status, in_period, **match)
               for key, (table, status) in LEDGER_METRICS.items()}
    opening = _sum(income, PAID, before, **match) - _sum(expenses, PAID, before, **match)
    return {"bank_balance": opening + figures["received"] - figures["paid"], "opening": opening, **figures}


def dashboard_figures(data, orgs, banks, date_from, date_to):
    """Every number on the dashboard: {"orgs": {org | ALL: {...}}, "banks": {bank: {...}}}."""
    income, expenses, fees = data.get("income") or [], data.get("expenses") or [], data.get("fees") or []
    per_org = {}
    for org in orgs:
        active = [r for r in data.get("fleet", {}).get(org) or [] if r.get("status") == ACTIVE]
        per_org[org] = {
            **summarize(income, expenses, date_from, date_to, org=org),
            "net_rent": sum((r.get("rent") or 0) - (r.get("deduction") or 0) for r in active),
            "vehicles": sum(r.get("count") or 0 for r in active),
            "fees_remaining": sum((f.get("total") or 0) - (f.get("paid") or 0) for f in fees if f.get("org") == org),
        }
    per_org[ALL] = {
        **summarize(income, expenses, date_from, date_to),
        "net_rent": sum(per_org[o]["net_rent"] for o in orgs),
        "vehicles": sum(per_org[o]["vehicles"] for o in orgs),
        "fees_remaining": sum((f.get("total") or 0) - (f.get("paid") or 0) for f in fees),
    }
    banks_fig = {b: summarize(income, expenses, date_from, date_to, bank=b) for b in banks}
    return {"orgs": per_org, "banks": banks_fig}
