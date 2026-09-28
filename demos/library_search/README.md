# library_search demo: Arabic + English search over a library's own stock

**SAMPLE / DEMO DATA.** `inventory_sample.csv` is a made-up stock list of well-known classic titles.
Shelf codes, copy counts and ISBNs are invented for the demo. The ISBNs use the unissued `999` prefix, so
they can't clash with real books, and every row has a `data_note` column saying it is a sample record. The
file doesn't describe any real library.

## What it shows

The library's existing catalogue file (CSV or Excel) is given to the server at start-up and re-read with
`POST /api/reload` after it is replaced (there is no upload page yet). Readers search it in Arabic or English
and only see books that are **on the shelf right now**. Each result says **why it matched**.

| File | What it is |
|---|---|
| `inventory_sample.csv` | 67 rows / 63 titles (about half Arabic, half English). 4 rows are deliberate duplicates: the same ISBN written with hyphens or spaces, the same title with tashkeel or tatweel, or different letter case. 5 titles have 0 copies. |
| `example_queries.md` | 14 captured queries with ranked results, the synonyms added to each query and the "why it matched" terms. Also shows how many matches were hidden because they are out of stock. |
| `screenshot_mobile_self_help.png` | Mobile RTL search page. `تنمية ذاتية` finds the Arabic *سر النجاح* and also the English *Self-Help*, through an editable synonym. One matching title is hidden because it's out of stock. |
| `screenshot_mobile_ibn_khaldun.png` | `مقدمه إبن خلدون` is typed with ه instead of ة, with إ, and without the shadda. It still finds `مقدّمة ابن خلدون`, plus the English *Muqaddimah* by way of the `ابن خلدون ↔ ibn khaldun` synonym. |

Things to look for in `example_queries.md`:
- **Arabic spelling variants:** `الغزالى` (alef maqsura) finds `الغزالي`, and `الف ليلة وليلة` finds `ألف ليلة وليلة`.
- **Cross-language:** `خيال علمي` finds *The Time Machine* and *Frankenstein*, and `war strategy` finds `فن الحرب`.
- **Typos:** `sherlok holms` finds *The Adventures of Sherlock Holmes* through character n-grams.
- **Stock filter:** `Dracula` returns nothing and reports "1 matching title(s) hidden because no copies are available".
- **Cleaning:** the 4 duplicate rows are merged. The *Frankenstein* copies on two shelves add up to 3, listed as `EN-NOV-02 / EN-DISPLAY`. A duplicate row on the same shelf isn't counted twice.

## How to run it

```powershell
cd automation-demos   # repository root
# search from the command line
python -m library_search search demos\library_search\inventory_sample.csv "تنمية ذاتية" "sherlok holms" --limit 5
# regenerate example_queries.md exactly (same 14 queries, same sample-data banner)
python -m library_search search demos\library_search\inventory_sample.csv "تنمية ذاتية" "self help" "مقدمه إبن خلدون" "الف ليلة وليلة" "تاريخ الأندلس" "history" "روايات نجيب محفوظ" "الغزالى" "sherlok holms" "خيال علمي" "كتب للأطفال" "war strategy" "avicenna" "Dracula" --limit 5 --markdown demos\library_search\example_queries.md --title "library_search: example queries on a SAMPLE inventory" --note "SAMPLE / DEMO DATA. The inventory is a made-up stock list of well-known classic titles (inventory_sample.csv); shelves, copy counts and ISBNs (non-issued 999 prefix) are invented for the demo and do not describe any real library. Captured with: python -m library_search search inventory_sample.csv <queries> --limit 5"
# write the cleaned, de-duplicated inventory
python -m library_search clean demos\library_search\inventory_sample.csv --out cleaned.csv
# mobile search page + JSON API on http://127.0.0.1:8000
python -m library_search serve demos\library_search\inventory_sample.csv --notice "SAMPLE DATA"
#   GET  /                         RTL search page (plain HTML/JS)
#   GET  /api/search?q=...&limit=  JSON results: in-stock books, score, why, synonyms used, hidden out-of-stock count
#   POST /api/reload               re-read the inventory + synonyms (set --reload-key to protect it)
```

The inventory can be UTF-8 CSV, a Windows-1256 CSV saved by Arabic Excel, or `.xlsx`/`.xlsm`, with English or
Arabic headers (for example `العنوان`, `المؤلف`, `النسخ المتاحة`, `الرف`). If a file has both a total `copies`
column and an `available` column, the stock filter uses `available`.

To add your own topic words, edit `library_search/synonyms.json` (or pass `--synonyms my.json`), then call
`POST /api/reload`. Each group is a list of words that should find each other, for example
`["تنمية ذاتية", "self-help", "personal development"]`.
An LLM reranker is optional and off by default (`serve --rerank-provider anthropic`). It reorders a slightly larger pool of
in-stock candidates, so the top results can change, but it can never add a book that was not a candidate or
one with no copies. When it is on, the query and the candidate titles/descriptions are sent to the LLM provider.

Tests: `python -m pytest -q tests/test_library_search.py` (from the repository root). No network is used.

## ملخص بالعربية

يعرض هذا المثال بحثاً ذكياً في مخزون المكتبة نفسها بالعربية والإنجليزية. تُرفع قائمة الكتب الحالية بصيغة CSV أو Excel، فينظّفها البرنامج
أولاً: يزيل التشكيل والتطويل، ويوحّد صور الألف والياء والتاء المربوطة، ويدمج السجلات المكررة حسب الردمك أو حسب العنوان والمؤلف.
بعد ذلك يبحث بأسلوب TF-IDF على مستوى الكلمات والحروف، ويوسّع البحث بقائمة مرادفات يمكن تعديلها، فيصل مثلاً من «تنمية ذاتية» إلى
كتاب Self-Help. لا تظهر في النتائج إلا الكتب المتوفرة فعلاً على الرفوف، ومع كل نتيجة سبب ظهورها. البيانات هنا **تجريبية بالكامل**
(قائمة كتب كلاسيكية مشهورة بأرقام رفوف ونسخ وردمك مختلقة) ولا تمثل أي مكتبة حقيقية.
