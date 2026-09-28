import datetime as dt
import re
from pathlib import Path

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from fleet_accounts import build_workbook, dashboard_figures, preview, sample_data, summarize
from fleet_accounts.cli import main
from fleet_accounts.core import (ALL, CONTRACT_STATUSES, DASHBOARD, DEFAULT_BANKS, DEFAULT_ORGS, DUE, EXPENSE_CATEGORIES,
                                 EXPENSES, FEE_COLS, FEES, FLEET_COLS, INCOME, INCOME_CATEGORIES, LEDGER_COLS, LISTS,
                                 METRICS, PAID, PENDING, SAMPLE_TAG, VEHICLE_TYPES)

PERIOD = (dt.date(2026, 1, 1), dt.date(2026, 12, 31))
REF = re.compile(r"(\w+)\[([^\[\]]+)\]$")
PAY_STATUS_TEXT = ["مدفوع", "مستحق", "معلّق"]  # the exact spelling (with shadda) the SUMIFS criteria must match


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    data = sample_data(DEFAULT_ORGS, DEFAULT_BANKS, 2026)
    path = build_workbook(tmp_path_factory.mktemp("fleet") / "demo.xlsx", data=data, period=PERIOD, sample=True)
    return data, path


def _day(value):
    return value.date() if isinstance(value, dt.datetime) else value


def _split(text, seps):
    """Split a formula on top-level separator characters: [(separator before the piece, piece)]."""
    out, cur, sep, depth, quoted = [], "", "", 0, False
    for ch in text:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch in "[(":
            depth += 1
        elif not quoted and ch in "])":
            depth -= 1
        if ch in seps and not depth and not quoted:
            out.append((sep, cur))
            cur, sep = "", ch
        else:
            cur += ch
    return out + [(sep, cur)]


def _args(text):
    return [piece for _, piece in _split(text, ",")]


class Evaluator:
    """Evaluates the formula shapes the dashboard uses (SUMIFS over tables, SUM, +/- of terms, INDEX/MATCH)
    against the table rows read back from the saved file, because openpyxl itself cannot calculate."""

    def __init__(self, wb):
        self.ws = wb[DASHBOARD]
        self.tables = {}
        for ws in wb.worksheets:
            for table in ws.tables.values():
                head, *rows = [[c.value for c in row] for row in ws[table.ref]]
                self.tables[table.displayName] = [dict(zip(head, r)) for r in rows]
        for rows in self.tables.values():  # calculated columns: tbl[[#This Row],[a]]-tbl[[#This Row],[b]]
            for row in rows:
                for key, v in row.items():
                    if isinstance(v, str) and v.startswith("="):
                        a, b = re.findall(r"\[\[#This Row\],\[([^\]]+)\]\]", v)
                        row[key] = (row[a] or 0) - (row[b] or 0)

    def value(self, ref):
        v = self.ws[ref.replace("$", "")].value
        return self.formula(v) if isinstance(v, str) and v.startswith("=") else v

    def column(self, ref):
        name, col = REF.match(ref).groups()
        return [row[col] for row in self.tables[name]]

    def criterion(self, crit):
        if m := re.fullmatch(r'"(>=|<=|<)"&(\$?[A-Z]+\$?\d+)', crit):
            bound = _day(self.value(m[2]))
            test = {">=": lambda d: d >= bound, "<=": lambda d: d <= bound, "<": lambda d: d < bound}[m[1]]
            return lambda v: isinstance(v, dt.date) and test(_day(v))
        target = crit[1:-1] if crit.startswith('"') else self.value(crit)
        return lambda v: v == target

    def formula(self, text):
        terms = _split(text[1:], "+-")
        if len(terms) > 1:
            return sum((-1 if sep == "-" else 1) * self.formula("=" + term) for sep, term in terms)
        body = terms[0][1]
        if re.fullmatch(r"[A-Z]+\d+", body):
            return self.value(body)
        if m := re.fullmatch(r"SUMIFS\((.*)\)", body):
            args = _args(m[1])
            keep = [True] * len(self.column(args[0]))
            for rng, crit in zip(args[1::2], args[2::2]):
                test = self.criterion(crit)
                keep = [k and test(v) for k, v in zip(keep, self.column(rng))]
            return sum(v for v, k in zip(self.column(args[0]), keep) if k and isinstance(v, (int, float)))
        if m := re.fullmatch(r"SUM\((\w+\[[^\]]+\])\)", body):
            return sum(v for v in self.column(m[1]) if isinstance(v, (int, float)))
        if m := re.fullmatch(r"SUM\(([A-Z]+)(\d+):\1(\d+)\)", body):
            return sum(self.value(f"{m[1]}{r}") for r in range(int(m[2]), int(m[3]) + 1))
        if m := re.fullmatch(r"INDEX\(([A-Z]+)\$(\d+):\1\$(\d+),MATCH\((\$[A-Z]+\$\d+),\$A\$\2:\$A\$\3,0\)\)", body):
            key = self.value(m[4])
            return next(self.value(f"{m[1]}{r}") for r in range(int(m[2]), int(m[3]) + 1) if self.value(f"A{r}") == key)
        raise ValueError(f"unsupported formula {text}")


def _layout(ws):
    """Row numbers of the comparison table (organisations + الكل), the KPI block and the bank table."""
    heads = [r for r in range(1, ws.max_row + 1) if ws.cell(r, 1).value in ("المؤسسة", "الحساب البنكي")]
    cmp_head, bank_head = heads
    org_rows = list(range(cmp_head + 1, cmp_head + 2 + len(DEFAULT_ORGS)))
    kpi_rows = [r for r in range(1, cmp_head) if ws.cell(r, 2).value and str(ws.cell(r, 2).value).startswith("=INDEX")]
    bank_rows = list(range(bank_head + 1, bank_head + 1 + len(DEFAULT_BANKS)))
    return org_rows, kpi_rows, bank_rows


def _dv(ws):
    return {str(dv.sqref): dv.formula1 for dv in ws.data_validations.dataValidation if dv.type == "list"}


def test_sheets_are_rtl_in_order_with_hidden_lists_and_sample_stamp(demo):
    wb = load_workbook(demo[1])
    assert wb.sheetnames == [DASHBOARD, *DEFAULT_ORGS, INCOME, EXPENSES, FEES, LISTS]
    assert all(ws.sheet_view.rightToLeft for ws in wb.worksheets)
    assert wb[LISTS].sheet_state == "hidden"
    assert [ws.title for ws in wb.worksheets if ws.sheet_state == "visible"] == wb.sheetnames[:-1]
    assert all(SAMPLE_TAG in ws["A1"].value for ws in wb.worksheets if ws.title != LISTS)


def test_tables_span_all_rows_and_fleet_sheets_are_identical(demo):
    data, path = demo
    wb = load_workbook(path)
    expected = {INCOME: ("tblIncome", LEDGER_COLS, len(data["income"])),
                EXPENSES: ("tblExpenses", LEDGER_COLS, len(data["expenses"])),
                FEES: ("tblFees", FEE_COLS, len(data["fees"]))}
    for i, org in enumerate(DEFAULT_ORGS, 1):
        expected[org] = (f"tblOrg{i}", FLEET_COLS, len(data["fleet"][org]))
    for sheet, (name, cols, n) in expected.items():
        ws = wb[sheet]
        table = ws.tables[name]
        headers = [h for _, h, *_ in cols]
        assert table.ref == f"A3:{get_column_letter(len(cols))}{3 + n}"
        assert table.autoFilter.ref == table.ref
        assert [c.name for c in table.tableColumns] == headers == [ws.cell(3, c).value for c in range(1, len(cols) + 1)]

    fleet = [wb[org] for org in DEFAULT_ORGS]
    shape = [([ws.cell(3, c).value for c in range(1, 11)], _dv(ws),
              {k: d.width for k, d in ws.column_dimensions.items()}) for ws in fleet]
    assert shape[0] == shape[1] == shape[2]
    assert _dv(fleet[0]) == {"B4:B2000": "lst_vehicle_types", "J4:J2000": "lst_contract_status"}


def test_net_rent_and_remaining_are_calculated_table_columns(demo):
    wb = load_workbook(demo[1])
    for sheet, name, col, a, b in ((DEFAULT_ORGS[1], "tblOrg2", 9, "الإيجار الشهري", "خصم الصيانة"),
                                   (FEES, "tblFees", 5, "الإجمالي", "المدفوع")):
        ws = wb[sheet]
        table = ws.tables[name]
        formula = f"{name}[[#This Row],[{a}]]-{name}[[#This Row],[{b}]]"
        assert table.tableColumns[col - 1].calculatedColumnFormula.attr_text == formula  # new rows inherit it
        last = int(re.search(r"(\d+)$", table.ref)[1])
        assert all(ws.cell(r, col).value == "=" + formula for r in range(4, last + 1))


def test_dropdowns_use_named_lists_on_the_hidden_sheet(demo):
    wb = load_workbook(demo[1])
    lists = wb[LISTS]
    expected = {"lst_orgs": DEFAULT_ORGS, "lst_org_filter": [ALL, *DEFAULT_ORGS], "lst_banks": DEFAULT_BANKS,
                "lst_vehicle_types": VEHICLE_TYPES, "lst_contract_status": CONTRACT_STATUSES,
                "lst_income_cats": INCOME_CATEGORIES, "lst_expense_cats": EXPENSE_CATEGORIES,
                "lst_pay_status": [PAID, DUE, PENDING]}
    for name, values in expected.items():
        m = re.fullmatch(r"OFFSET\(lists!\$([A-Z])\$2,0,0,COUNTA\(lists!\$\1:\$\1\)-1,1\)", wb.defined_names[name].attr_text)
        assert [c.value for c in lists[m[1]][1:] if c.value is not None] == values
    assert PAY_STATUS_TEXT == [PAID, DUE, PENDING]
    ledger = {"B4:B2000": "lst_orgs", "C4:C2000": "lst_banks", "F4:F2000": "lst_pay_status"}
    assert _dv(wb[INCOME]) == {**ledger, "D4:D2000": "lst_income_cats"}
    assert _dv(wb[EXPENSES]) == {**ledger, "D4:D2000": "lst_expense_cats"}
    assert _dv(wb[FEES]) == {"B4:B2000": "lst_orgs"}
    assert _dv(wb[DASHBOARD]) == {"F4": "lst_org_filter"}
    assert all(dv.showErrorMessage for ws in wb.worksheets for dv in ws.data_validations.dataValidation)



def test_dashboard_formulas_reference_the_right_columns(demo):
    wb = load_workbook(demo[1])
    ws = wb[DASHBOARD]
    org_rows, kpi_rows, bank_rows = _layout(ws)
    col = {key: get_column_letter(i) for i, (key, *_) in enumerate(METRICS, 2)}
    def paid(t, org=""):
        return f'SUMIFS({t}[المبلغ],{org}{t}[الحالة],"مدفوع",{t}[التاريخ],">="&$B$4,{t}[التاريخ],"<="&$D$4)'

    def paid_before(t, org=""):
        return f'SUMIFS({t}[المبلغ],{org}{t}[الحالة],"مدفوع",{t}[التاريخ],"<"&$B$4)'

    for r in org_rows:
        f = {key: ws[f"{col[key]}{r}"].value for key in col}
        assert f["bank_balance"] == f"={col['opening']}{r}+{col['received']}{r}-{col['paid']}{r}"
        by_org = r != org_rows[-1]  # the الكل row has no organisation criterion
        org = (lambda t: f"{t}[المؤسسة],$A{r},") if by_org else (lambda t: "")
        assert f["received"] == "=" + paid("tblIncome", org("tblIncome"))
        assert f["paid"] == "=" + paid("tblExpenses", org("tblExpenses"))
        assert f["opening"] == f"={paid_before('tblIncome', org('tblIncome'))}-{paid_before('tblExpenses', org('tblExpenses'))}"
        # the balance inputs are PAID-only; due/pending have their own cells
        balance_inputs = f["opening"] + f["received"] + f["paid"]
        assert DUE not in balance_inputs and PENDING not in balance_inputs
        assert 'tblIncome[الحالة],"مستحق"' in f["receivables"] and 'tblExpenses[الحالة],"مستحق"' in f["payables"]
        assert 'tblIncome[الحالة],"معلّق"' in f["pending_income"] and 'tblExpenses[الحالة],"معلّق"' in f["pending_expense"]
    first, total = org_rows[0], org_rows[-1]
    assert ws[f"{col['net_rent']}{first}"].value == '=SUMIFS(tblOrg1[صافي الإيجار],tblOrg1[حالة العقد],"ساري")'
    assert ws[f"{col['vehicles']}{first + 1}"].value == '=SUMIFS(tblOrg2[العدد],tblOrg2[حالة العقد],"ساري")'
    assert ws[f"{col['fees_remaining']}{first}"].value == f"=SUMIFS(tblFees[المتبقي],tblFees[المؤسسة],$A{first})"
    assert [ws.cell(r, 1).value for r in org_rows] == [*DEFAULT_ORGS, ALL]
    assert [ws.cell(r, 2).value for r in kpi_rows] == [
        f"=INDEX({col[k]}${first}:{col[k]}${total},MATCH($F$4,$A${first}:$A${total},0))" for k, *_ in METRICS]
    b = bank_rows[0]
    assert [ws.cell(b, c).value for c in range(2, 6)] == [
        f"=C{b}+D{b}-E{b}",
        f"={paid_before('tblIncome', f'tblIncome[الحساب البنكي],$A{b},')}"
        f"-{paid_before('tblExpenses', f'tblExpenses[الحساب البنكي],$A{b},')}",
        "=" + paid("tblIncome", f"tblIncome[الحساب البنكي],$A{b},"),
        "=" + paid("tblExpenses", f"tblExpenses[الحساب البنكي],$A{b},")]
    assert ws["F4"].value == ALL and _day(ws["B4"].value) == PERIOD[0] and _day(ws["D4"].value) == PERIOD[1]


def test_dashboard_formulas_evaluate_to_the_python_mirror(demo):
    data, path = demo
    wb = load_workbook(path)
    ws = wb[DASHBOARD]
    figures = dashboard_figures(data, DEFAULT_ORGS, DEFAULT_BANKS, *PERIOD)
    ev = Evaluator(wb)
    org_rows, kpi_rows, bank_rows = _layout(ws)
    for r in org_rows:
        org = ws.cell(r, 1).value
        for c, (key, *_) in enumerate(METRICS, 2):
            assert ev.value(f"{get_column_letter(c)}{r}") == pytest.approx(figures["orgs"][org][key]), (org, key)
    for r, bank in zip(bank_rows, DEFAULT_BANKS):
        assert [ev.value(f"{c}{r}") for c in "BCDE"] == pytest.approx(
            [figures["banks"][bank][k] for k in ("bank_balance", "opening", "received", "paid")])
    for choice in (ALL, *DEFAULT_ORGS):  # the selector drives the KPI block
        ws["F4"] = choice
        assert [ev.value(f"B{r}") for r in kpi_rows] == pytest.approx([figures["orgs"][choice][k] for k, *_ in METRICS])
    # the sample rows dated the December before are not period income, but they are in the bank
    early = summarize(data["income"], data["expenses"], dt.date(2025, 12, 1), PERIOD[1])
    assert early["received"] > figures["orgs"][ALL]["received"]
    assert figures["orgs"][ALL]["opening"] == early["received"] - figures["orgs"][ALL]["received"] > 0
    assert early["bank_balance"] == figures["orgs"][ALL]["bank_balance"]


def test_bank_balance_is_everything_paid_up_to_the_end_date(demo):
    """A later start date moves amounts from the period columns into the balance brought forward; the bank
    balance, which is a balance and not a period total, only depends on the end date."""
    data, path = demo
    wb = load_workbook(path)
    ws = wb[DASHBOARD]
    org_rows, _, bank_rows = _layout(ws)
    ev = Evaluator(wb)
    col = {key: get_column_letter(i) for i, (key, *_) in enumerate(METRICS, 2)}
    full = [ev.value(f"B{r}") for r in org_rows + bank_rows]
    ws["B4"] = dt.date(2026, 7, 1)
    assert [ev.value(f"B{r}") for r in org_rows + bank_rows] == pytest.approx(full)
    late = dashboard_figures(data, DEFAULT_ORGS, DEFAULT_BANKS, dt.date(2026, 7, 1), PERIOD[1])["orgs"][ALL]
    assert ev.value(f"{col['opening']}{org_rows[-1]}") == pytest.approx(late["opening"])
    assert ev.value(f"{col['received']}{org_rows[-1]}") == pytest.approx(late["received"])
    assert late["received"] < dashboard_figures(data, DEFAULT_ORGS, DEFAULT_BANKS, *PERIOD)["orgs"][ALL]["received"]
    ws["D4"] = dt.date(2026, 6, 30)  # an earlier end date is an earlier balance
    june = dashboard_figures(data, DEFAULT_ORGS, DEFAULT_BANKS, dt.date(2026, 7, 1), dt.date(2026, 6, 30))
    assert ev.value(f"B{org_rows[-1]}") == pytest.approx(june["orgs"][ALL]["bank_balance"]) != full[len(org_rows) - 1]


def test_due_and_pending_never_enter_the_bank_balance(tmp_path):
    def row(amount, status, org=DEFAULT_ORGS[0], date=dt.date(2026, 3, 1)):
        return {"date": date, "org": org, "bank": DEFAULT_BANKS[0], "category": "أخرى",
                "amount": amount, "status": status}

    income = [row(1000, PAID), row(500, DUE), row(700, PENDING), row(50, PAID, DEFAULT_ORGS[1])]
    expenses = [row(300, PAID), row(200, DUE), row(90, PENDING)]
    s = summarize(income, expenses, *PERIOD, org=DEFAULT_ORGS[0])
    assert s == {"bank_balance": 700, "opening": 0, "received": 1000, "paid": 300, "receivables": 500,
                 "payables": 200, "pending_income": 700, "pending_expense": 90}
    # huge due and pending amounts, inside the period and before it (where they must not reach the opening)
    huge = [row(10 ** 6, status, date=date) for status in (DUE, PENDING) for date in (dt.date(2026, 3, 1), dt.date(2025, 6, 1))]
    assert summarize(income + huge, expenses + huge, *PERIOD, org=DEFAULT_ORGS[0])["bank_balance"] == 700

    # same rows through the real formulas: the workbook agrees
    data = {"income": income + huge, "expenses": expenses + huge}
    wb = load_workbook(build_workbook(tmp_path / "rule.xlsx", data=data, period=PERIOD))
    org_rows, _, bank_rows = _layout(wb[DASHBOARD])
    col = {key: get_column_letter(i) for i, (key, *_) in enumerate(METRICS, 2)}
    ev = Evaluator(wb)
    assert ev.value(f"B{org_rows[0]}") == 700
    assert ev.value(f"{col['opening']}{org_rows[0]}") == 0
    assert ev.value(f"B{org_rows[-1]}") == 750  # الكل adds the second organisation's receipt
    assert ev.value(f"B{bank_rows[0]}") == 750
    assert ev.value(f"{col['receivables']}{org_rows[0]}") == 500 + 10 ** 6  # due is reported, not in the balance

    expenses[1]["status"] = PAID  # settling a payable is what moves the balance
    assert summarize(income, expenses, *PERIOD, org=DEFAULT_ORGS[0])["bank_balance"] == 500
    income.append(row(40, PAID, date=dt.date(2025, 6, 1)))  # paid before the period: brought forward
    assert summarize(income, expenses, *PERIOD, org=DEFAULT_ORGS[0])["bank_balance"] == 540


def test_blank_template_has_one_empty_row_per_table_and_no_sample_stamp(tmp_path):
    wb = load_workbook(build_workbook(tmp_path / "blank.xlsx", period=PERIOD))
    assert all(t.ref.endswith("4") for ws in wb.worksheets for t in ws.tables.values())
    assert not any(SAMPLE_TAG in str(ws["A1"].value) for ws in wb.worksheets)
    org_rows, kpi_rows, _ = _layout(wb[DASHBOARD])
    ev = Evaluator(wb)
    assert all(ev.value(f"{get_column_letter(c)}{r}") == 0 for r in org_rows for c in range(2, len(METRICS) + 2))


@pytest.mark.parametrize("orgs", [["أ/ب", "ج", "د"], ["م" * 32], ["نفس", "نفس"], [INCOME], [ALL], [],
                                  ["Lists"], ["Org", "ORG"], ["=1+1"], ["أ~ب"], ["<ب"], ["ب'"]])
def test_rejects_organisation_names_that_cannot_be_sheets_or_criteria(tmp_path, orgs):
    # "Lists" would push the hidden lists sheet to "lists1" and break every dropdown; ~ and < change SUMIFS matching
    with pytest.raises(ValueError):
        build_workbook(tmp_path / "x.xlsx", orgs=orgs)


@pytest.mark.parametrize("banks", [["حساب*"], ["حساب؟?"], ["ح", "ح"], ["Bank A", "bank a"], ["=cmd"], [" "], []])
def test_rejects_bank_names_that_would_change_sumifs_matching(tmp_path, banks):
    with pytest.raises(ValueError):
        build_workbook(tmp_path / "x.xlsx", banks=banks)


def test_data_text_starting_with_equals_is_stored_as_text(tmp_path):
    row = {"date": dt.date(2026, 3, 1), "org": DEFAULT_ORGS[0], "bank": DEFAULT_BANKS[0], "category": "أخرى",
           "amount": 10, "status": PAID, "notes": '=HYPERLINK("http://example.invalid","x")'}
    fleet = {DEFAULT_ORGS[0]: [{"client": "=1+1", "rent": 100, "deduction": 10, "status": "ساري"}]}
    wb = load_workbook(build_workbook(tmp_path / "t.xlsx", data={"income": [row], "fleet": fleet}, period=PERIOD))
    assert wb[INCOME]["G4"].data_type == "s" and wb[DEFAULT_ORGS[0]]["A4"].data_type == "s"
    assert wb[DEFAULT_ORGS[0]]["I4"].data_type == "f"  # the calculated column is still a formula


def test_cli_writes_workbook_and_preview(tmp_path, monkeypatch):
    calls = []

    def fake_chrome(cmd, **kwargs):
        calls.append(cmd)
        Path(next(a for a in cmd if a.startswith("--screenshot="))[13:]).write_bytes(b"\x89PNG")

    monkeypatch.setattr(preview, "find_chrome", lambda: "chrome.exe")
    monkeypatch.setattr(preview.subprocess, "run", fake_chrome)
    assert main([str(tmp_path / "d.xlsx"), "--sample", "--year", "2026", "--preview"]) == 0
    figures = dashboard_figures(sample_data(DEFAULT_ORGS, DEFAULT_BANKS, 2026), DEFAULT_ORGS, DEFAULT_BANKS, *PERIOD)
    md = (tmp_path / "d_dashboard.md").read_text(encoding="utf-8")
    assert SAMPLE_TAG in md and f"{figures['orgs'][ALL]['bank_balance']:,.2f}" in md
    assert (tmp_path / "d_dashboard.png").exists() and "--headless=new" in calls[0]
    assert load_workbook(tmp_path / "d.xlsx").sheetnames[0] == DASHBOARD
    assert main([str(tmp_path / "bad.xlsx"), "--orgs", "أ:ب"]) == 2
    # a dotted name keeps its full stem for the preview files
    assert main([str(tmp_path / "acc.v2.xlsx"), "--preview"]) == 0
    assert (tmp_path / "acc.v2_dashboard.md").exists() and (tmp_path / "acc.v2_dashboard.png").exists()
    assert not (tmp_path / "acc.md").exists()
    for bad in (["out.csv"], ["o.xlsx", "--year", "0"]):
        with pytest.raises(SystemExit):
            main([str(tmp_path / bad[0]), *bad[1:]])


def test_render_png_never_reports_a_stale_file(tmp_path, monkeypatch):
    png = tmp_path / "old.png"
    png.write_bytes(b"\x89PNG from an earlier run")
    monkeypatch.setattr(preview.subprocess, "run", lambda cmd, **kw: None)  # Chrome ran but wrote nothing
    assert preview.render_png("<p>x</p>", png, chrome="chrome.exe") is None and not png.exists()

    def hang(cmd, **kw):
        raise preview.subprocess.TimeoutExpired(cmd, 90)

    monkeypatch.setattr(preview.subprocess, "run", hang)
    assert preview.render_png("<p>x</p>", png, chrome="chrome.exe") is None


def test_preview_escapes_names(tmp_path):
    figures = dashboard_figures({}, ["أ|ب", "<b>ج</b>"], ["ح"], *PERIOD)
    md = preview.dashboard_markdown(figures, PERIOD)
    row = next(line for line in md.splitlines() if line.startswith("| أ"))
    assert "أ\\|ب" in row and row.replace("\\|", "").count("|") == len(METRICS) + 2
    html = preview.dashboard_html(figures, PERIOD)
    assert "&lt;b&gt;ج&lt;/b&gt;" in html and "<b>ج</b>" not in html
