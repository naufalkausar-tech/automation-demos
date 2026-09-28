# automation-demos

Four small, working, tested Python tools. Each one comes with sample output you can open without running anything.
Every "business" record in the samples is **demo data**, marked inside each file: a SAMPLE/DEMO banner or
footer, a `data_note` column, or `DEMO-` prefixes on SKUs and plates.

| Demo | What it does | Sample output |
|---|---|---|
| [`arabic_report`](demos/arabic_report/) | A web form (RTL) fills a report in an organisation's own brand (logo, colours, fonts), exported as PDF and PNG behind an unguessable share link. Mixed Arabic/English text and Arabic-Indic digits render correctly. | `demos/arabic_report/sample.pdf`, `sample.png` |
| [`price_monitor`](demos/price_monitor/) | Reads public competitor listing pages politely (robots.txt, per-host delay). A person approves the product matches. The tool then suggests prices that never go below a margin floor, keeps the price history in SQLite, and writes an Excel gap report plus a CSV of approved changes. | `demos/price_monitor/gap_report.xlsx`, `approved_changes.csv` |
| [`library_search`](demos/library_search/) | Arabic/English search over a library's own stock. It handles spelling variants (hamza, ta marbuta, diacritics) and cross-language synonyms, returns only books with copies available, and says why each result matched. | `demos/library_search/example_queries.md`, screenshots |
| [`fleet_accounts`](demos/fleet_accounts/) | Generates an RTL Excel workbook (formulas, no macros) for three vehicle-rental organisations. The bank balance counts paid rows only; due and pending amounts are shown separately and never enter it. | `demos/fleet_accounts/accounts_demo.xlsx`, dashboard PNG |

`scrape_kit` (polite HTTP session, pagination, field extraction, CSV export) and `llm_extract` (optional LLM
client used by the library search rerank hook, off by default) are small shared helpers.

## Run

```bash
pip install -e ".[dev,llm]"
python -m pytest -q   # offline: sockets outside localhost are refused in tests; one test runs local Chrome on a local file
python -m arabic_report serve            # needs Google Chrome or Chromium (set CHROME_PATH if not found)
python -m library_search serve demos/library_search/inventory_sample.csv
python -m fleet_accounts accounts.xlsx --sample --preview
python demos/price_monitor/run_demo.py --offline   # rewrites the price_monitor samples
```

Each demo folder has its own README with details, limitations and the exact commands used to produce the samples.

## How this was built

Written with Claude Code (Anthropic's AI coding agent) for Naufal Kausar's portfolio: the AI wrote the code,
tests and sample files. Each of the four demo packages then went through a separate AI code-review pass looking
for bugs and security problems (HTML injection, path traversal, unbounded uploads, spreadsheet formula injection).
That is not an independent security audit. These are portfolio demos, not client work or production systems.

## Third-party material

`price_monitor/fixtures/books.toscrape.com/` holds trimmed, edited copies of listing markup from
books.toscrape.com, a public web-scraping practice sandbox (published by Zyte, formerly Scrapinghub). They exist
only so the tests and the offline demo run without network access. They are not covered by this repository's
MIT licence; all rights stay with their owners. The live demo run respected the site's robots.txt and waited
3 s between requests.

## ملخص بالعربية

أربع أدوات صغيرة عاملة ومختبرة بلغة Python، ومع كل أداة أمثلة جاهزة للاطلاع عليها:
تقرير عربي بهوية الجهة يُصدَّر PDF وصورة من نموذج على الويب، ومتابعة أسعار المنافسين مع تقرير فروقات في Excel
واقتراح أسعار لا تنزل عن حد الربح، وبحث عربي/إنجليزي في مخزون مكتبة يُظهر الكتب المتوفرة فقط مع سبب الترشيح،
وملف Excel محاسبي لثلاث مؤسسات تأجير مركبات لا يدخل فيه المعلّق أو المستحق في الرصيد البنكي.
جميع البيانات في الأمثلة وهمية للعرض فقط. كتب الكودَ والاختباراتِ والأمثلةَ وكيلُ البرمجة Claude Code، ثم خضعت كل أداة
لمراجعة آلية إضافية بحثًا عن الأخطاء ومشكلات الأمان، وهي ليست تدقيقًا أمنيًا مستقلًا ولا أعمالًا لعملاء.

## License

MIT, see [LICENSE](LICENSE).
