# price_monitor demo: competitor prices, gap report, approved price changes

**All "our shop" data here is DEMO DATA.** The catalogue (`DEMO-*` SKUs, our prices, costs, margins) is
synthetic. Competitor prices come from [books.toscrape.com](https://books.toscrape.com), a public sandbox
built for scraping practice. The earlier run in the price history is a synthetic snapshot. The Arabic store
in `arabic_fixture/` (`souq-demo.test`) is an invented test fixture.

## What it shows

| Step | Output |
|---|---|
| Polite collection of 2 category listings (Mystery with 2 pages of pagination, Travel): robots.txt checked, 3 s between requests, named User-Agent. (Full page snapshots are not published; see Third-party material in the root README.) | `pages/` (not published; 3 pages, 43 offers) |
| Fuzzy match suggestions (difflib on normalised names) for a person to approve. At a 0.5 threshold two wrong pairs appear and are rejected in review. *The Last Mile (Amos Decker 3)* is never paired with #2: differing numbers halve the score, which keeps such a pair under the 0.5 threshold. | `match_suggestions.csv` then the reviewed `matches.csv` |
| Rules: match the lowest in-stock competitor price, never below the margin floor `cost / (1 - min margin)`. Flags: we are >10% cheaper, competitor out of stock, price changed since the last run, possible pricing error, target below floor, our price below floor. | `gap_report.xlsx` |
| History in SQLite: the synthetic earlier snapshot (4 prices edited, dated 7 days back) makes the change flags and the pricing-error guard visible. *The Exiled* "moved -54%" and is held for review, not chased. | `history.sqlite`, History sheet |
| Changes within the 5% auto-approval policy, ready for store import (sku, name, current_price, new_price, change_pct, reason) | `approved_changes.csv` (4 rows) |
| Arabic mini demo: Arabic names with and without diacritics, Arabic-Indic digits (`١٬٢٥٠٫٠٠ ر.س`, `۷۵`), out-of-stock text (`نفدت الكمية`, `غير متوفر`), `?page=2` pagination. A scripted reviewer approves 2 rows and tries a price under the floor, which `approve` refuses. | `arabic_fixture/gap_report.xlsx`, `gap_report_reviewed.xlsx`, `approved_changes.csv` |

The workbook has three sheets. **Summary** holds the demo label, KPIs, flag counts and the next step.
**Detail** has one row per SKU, a frozen header and SKU/name columns, conditional formatting (red = we
are dearer, blue = we are much cheaper, amber = flags, green = approved), a yes/no Approve dropdown and
links to competitor products. **History** has our price and every matched competitor price per run, with
the change since the previous run.

## Run it

```bash
cd demos\price_monitor
python run_demo.py             # live: robots.txt + 3 listing pages, 3 s apart
python run_demo.py --offline   # no network: replays the trimmed package fixtures (overwrites the samples here)
```

The files in this folder come from a live run on 2026-09-28. The Summary sheet's *Run note* shows which
source a run used. The full page snapshots from that run are not published. Without network, `run_demo.py` uses the
package's trimmed test fixtures, and the note then starts with `FALLBACK`. Running it rewrites the sample files
in this folder (they contain run timestamps).

The same steps with the CLI, on your own catalogue:

```bash
python -m price_monitor suggest-matches --catalogue our.csv --sources sources.json --out match_suggestions.csv
#   review: set approved=yes/no, keep the file as matches.csv
python -m price_monitor run --catalogue our.csv --sources sources.json --matches matches.csv \
    --db history.sqlite --out-dir report --save-pages report/pages [--undercut-pct 1] [--auto-approve-pct 5]
#   review the Approve column in report/gap_report.xlsx, then
python -m price_monitor approve report/gap_report.xlsx --out approved_changes.csv
```

## Limits worth knowing

- Reads server-rendered HTML only. A shop that builds its listing with JavaScript needs a browser fetcher.
- Matching compares names in the same language. An Arabic listing will not match an English catalogue name.
- It does not convert currencies. All sources are assumed to price in our currency.
- It reads one price per product card, so size or colour variants need their own cards or URLs.
- Pushing prices to a store (Shopify, WooCommerce, Salla, Zid) is only an adapter interface
  (`price_monitor/adapter.py`, with a dry-run implementation). It makes no live store calls.

## ملخص بالعربية

يعرض هذا المثال أداة لمراقبة أسعار المنافسين واقتراح أسعار جديدة. تقرأ الأداة صفحات المنتجات العامة بأسلوب مهذّب: تحترم ملف robots.txt، وتترك فاصلًا زمنيًا بين الطلبات، وتحفظ نسخة من كل صفحة كدليل. وتفهم الأسعار المكتوبة بالأرقام العربية الهندية والفواصل العربية ورموز العملات مثل «ر.س». تقترح الأداة مطابقة منتجاتنا مع منتجات المنافسين بمقارنة الأسماء العربية والإنجليزية بعد توحيد كتابتها (حذف التشكيل و«ال» التعريف، وتوحيد الألف والتاء المربوطة)، ولا تُستخدم أي مطابقة قبل أن يوافق عليها شخص. بعد ذلك تقترح سعرًا يساوي أقل سعر منافس متوفر أو يقل عنه، دون النزول أبدًا عن الحد الأدنى لهامش الربح. وتنبّه عند نفاد مخزون المنافس، أو تغيّر سعره منذ آخر تشغيل، أو احتمال وجود خطأ في تسعيره. الناتج تقرير Excel من ثلاث أوراق (ملخص، تفاصيل، سجل الأسعار)، وملف CSV جاهز للاستيراد في المتجر لا يضم إلا التغييرات المعتمدة. جميع بيانات «متجرنا» في هذا المثال تجريبية، وأسعار المنافس مأخوذة من الموقع التدريبي books.toscrape.com. أما المتجر العربي في مجلد arabic_fixture فهو متجر وهمي مخصّص للاختبار.
