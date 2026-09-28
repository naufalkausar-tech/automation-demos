# Fleet accounts workbook (demo)

**SAMPLE / DEMO DATA.** Every organisation, client, plate number and amount in these files was made up for the
demo. Clients are called "عميل تجريبي" (sample client) and plates start with `DEMO`.

An Arabic, right-to-left Excel workbook for a hypothetical business that runs three vehicle-rental organisations. It uses
plain Excel formulas and has no macros.

| File | What it is |
|---|---|
| `accounts_demo.xlsx` | The workbook, filled with sample rows (an opening balance and one receipt in Dec 2025, then Jan-Aug 2026; three organisations, two bank accounts) |
| `accounts_demo_dashboard.png` | The dashboard figures shown as a page |
| `accounts_demo_dashboard.md` | The same figures as Markdown tables |

## What the workbook contains

- **لوحة التحكم (dashboard):** yellow input cells for the date range (from / to) and the organisation
  (`الكل` = all). Below them are the figures for the selected organisation, a table comparing the three
  organisations, balances by bank account and a column chart.
- **One sheet per organisation, all built the same way:** client, vehicle type (dropdown), count, vehicle
  number, plate, handover date, monthly rent, maintenance deduction, net rent (`= rent - deduction`) and
  contract status (dropdown).
- **الإيرادات / المصروفات (income / expenses):** date, organisation, bank account, category (these three are
  dropdowns), amount, status (dropdown: مدفوع / مستحق / معلّق) and notes.
- **الرسوم السنوية (annual fees):** fee, organisation, total, paid, remaining (`= total - paid`).
- **lists (hidden):** the values behind every dropdown, stored as named ranges. A bank or category typed directly
  under the last entry of its column (no blank rows between) shows up in the dropdowns.

Every data block is an Excel Table. A row typed directly under a table joins it, gets the net-rent or
remaining formula, and is counted by the dashboard's `SUMIFS`.

**Bank balance rule:** actual bank balance = income with status مدفوع (received) minus expenses with status
مدفوع (paid), counting everything up to the "to" date. The dashboard shows it as رصيد أول المدة (paid rows dated
before the "from" date) + income received in the period − expenses paid in the period, so moving the "from"
date changes the period columns but not the balance. Amounts marked مستحق (due) or معلّق (pending) appear on
their own lines as receivables, payables and pending amounts. They never enter the bank balance, not even
when dated before the period.

In the demo, the Dec 2025 rows are left out of the 2026 income columns and show up in رصيد أول المدة.

## How to run

```
cd automation-demos   # repository root
python -m fleet_accounts accounts.xlsx                    # empty template for the current year
python -m fleet_accounts accounts.xlsx --orgs "اسم 1" "اسم 2" "اسم 3" --banks "حساب 1" "حساب 2"
python -m fleet_accounts demos\fleet_accounts\accounts_demo.xlsx --sample --year 2026 --preview
python -m pytest -q tests/test_fleet_accounts.py
```

`--preview` writes the `_dashboard.md` and `_dashboard.png` files. The PNG needs Google Chrome, which runs
headless.

Organisation names become sheet names (up to 31 characters, none of `[ ] : * ? / \`). Organisation and bank
names are also matched by `SUMIFS`, so they must be unique ignoring case, must not contain `* ? ~` and must
not start with `= < >`. The generator refuses names that break these rules.

## How it was checked

- The unit tests read the saved file back. They check the sheets, the RTL setting, the table ranges, the
  calculated-column formulas and the dropdown lists. A small `SUMIFS` evaluator then works out every dashboard
  formula on the table rows and compares the result with a separate Python calculation. One test adds very
  large due and pending amounts, inside and before the period, and confirms the bank balance does not change.
  Another moves the "from" date and confirms the balance stays the same.
- The demo file was also opened read-only in a separate Microsoft Excel instance and recalculated there.
  Every figure in the comparison and bank tables matched the PNG and Markdown, with no formula errors. The
  organisation picker updated the summary block, moving the "from" date to 1 July left the balance unchanged,
  and a row added under a table picked up the net-rent formula and was counted on the dashboard.

## Limitations

- Leaving the organisation cell (F4) empty shows #N/A in the summary block; pick الكل for all.
- The from/to date cells are not validated.
- The PNG/Markdown preview ignores time of day, while Excel compares full date-times.

- openpyxl saves formulas without their results. Excel and LibreOffice calculate them when the file is
  opened, but some file previewers (e-mail, phone) show those cells empty.
- There is no opening-balance field. Enter an opening balance as an income row with category `رصيد افتتاحي`
  and status `مدفوع`, dated before the first period you will look at. It then counts in رصيد أول المدة and
  not as income of the period.
- Receivables, payables and pending amounts count rows dated inside the period. An unpaid row dated before the
  "from" date is not listed, so set the "from" date early enough to see every open item.
- Net rent and vehicle count use contracts whose status is ساري now; they are not filtered by the dates.
- A status pasted into the status column, instead of picked from the dropdown, must use the exact spelling,
  including the shadda in معلّق, or the dashboard will not count that row.
- The bank-account rows on the dashboard are fixed when the workbook is generated. A bank added later on the
  `lists` sheet appears in the dropdowns but needs its own dashboard row.
- The PNG and Markdown are computed in Python from the same rules as the formulas. The .xlsx itself is always
  the source of truth.

## ملخص بالعربية

هذا ملف Excel عربي من اليمين إلى اليسار، يعمل بالصيغ فقط ولا يحتوي على ماكرو، ومخصص لثلاث مؤسسات تؤجّر المركبات. فيه ورقة
للعملاء والمركبات لكل مؤسسة بنفس التصميم، وتُحسب فيها قيمة صافي الإيجار تلقائياً (الإيجار الشهري ناقص خصم الصيانة). وفيه
ورقتان للإيرادات والمصروفات، فيهما قوائم منسدلة للمؤسسة والحساب البنكي والتصنيف والحالة (مدفوع / مستحق / معلّق)، وورقة
للرسوم السنوية يُحسب فيها المتبقي تلقائياً. أما لوحة التحكم فتعرض الأرقام حسب المؤسسة والفترة التي تختارها، وتقارن بين
المؤسسات الثلاث. الرصيد البنكي الفعلي = رصيد أول المدة + الإيرادات المستلمة ناقص المصروفات المدفوعة فقط، أي كل ما دُفع
فعلاً حتى تاريخ النهاية، فلا يتغيّر الرصيد عند تغيير تاريخ البداية. أما المبالغ المستحقة والمعلّقة فتظهر في سطور خاصة بها ولا
تدخل في الرصيد أبداً. كل البيانات في هذا العرض تجريبية وليست بيانات عميل حقيقي.
