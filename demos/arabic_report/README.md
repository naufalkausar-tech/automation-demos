# arabic_report demo: branded Arabic (RTL) report to PDF and PNG with a share link

**All content here is SAMPLE / DEMO DATA.** "مؤسسة تجريبية (Demo Co.)" and "فرع تجريبي (Demo Branch)" are made up. The logo is a placeholder geometric mark, and every figure is invented. The report itself says so twice, in the yellow banner at the top and in the footer.

## What it shows

A FastAPI web app. A user fills in a structured Arabic form (title, organisation, author, date, executive summary, a KPI table, recommendations, and an optional logo upload). The app builds a branded report from a brand config JSON (logo, colours, font stack or embedded font file, one of the approved layouts, and Arabic-Indic or Latin digits). It then exports the report to PDF and PNG with headless Chrome and gives it an unguessable share link (`/r/<token>`) with copy, share and download buttons.

| File | What it is |
|---|---|
| `sample.pdf` | The generated A4 report (vector text, real Arabic shaping, selectable) |
| `sample.png` | The same report as an image, 2x density (1588 px wide), for WhatsApp or social posts |
| `sample.html` | The exact self-contained HTML that Chrome rendered |
| `brand.json` | Brand config: name, tagline, footer, logo, colours, font stack, layout, digits |
| `logo.png` | Placeholder logo that `brand.json` refers to |
| `sample_input.json` | The form data behind the sample (same fields as the web form) |
| `form.png` | Screenshot of the RTL input form (`GET /`) |
| `view.png` | Screenshot of the share page (`GET /r/<token>`). The token comes from a throwaway local run, so the link is dead |

What the sample shows:
- Right-to-left layout with correct Arabic shaping.
- Dates generated in Arabic ("٢٨ سبتمبر ٢٠٢٦").
- Arabic-Indic and Latin digits mixed on purpose.
- Latin phrases such as "(Demo Co.)" and "(300 SAR)" keep their order inside RTL lines, because the app isolates each Latin phrase (including URLs and accented names) with `<bdi>`, while sentence punctuation stays with the Arabic sentence.

Not visible in this one-page sample: the KPI table has fixed column widths, so long values or unbroken references wrap inside their cell instead of pushing the table off the A4 page (the Chrome test checks this). Longer reports continue on new pages with a top margin and a repeated table header.

## How to run it

From the repository root (needs Google Chrome or Chromium; set `CHROME_PATH` if it is not in the default location):

```powershell
# regenerate sample.pdf / sample.png / sample.html
python -m arabic_report render demos\arabic_report\sample_input.json `
  --brand demos\arabic_report\brand.json --out demos\arabic_report --name sample

# run the web app with this brand, then open http://127.0.0.1:8000/
python -m arabic_report serve --brand demos\arabic_report\brand.json --store $env:TEMP\arabic_reports

# tests (one of them renders a real PDF + PNG with Chrome; it is skipped if Chrome is missing)
python -m pytest -q tests/test_arabic_report.py
```

## Safety built in

- All user text is HTML-escaped.
- Brand colours and fonts are pattern-validated before they reach CSS.
- The report page has a Content-Security-Policy that blocks scripts and network requests. The share page allows only one hashed script.
- Uploaded logos are checked by their content, not their filename. They are limited to 2 MB and 16 MP, only PNG, JPEG (including multi-frame MPO photos from phones) and WEBP are accepted, and each one is re-encoded to a clean PNG (this drops EXIF and GPS data and anything appended to the file). A malformed or crafted image gets a clear Arabic error, not a server error.
- Request bodies are limited to about 2.25 MB. The limit is counted while the body streams in, so it also holds for chunked uploads that send no `Content-Length`.
- At most 2 reports are rendered by Chrome at once. Further requests wait their turn without tying up the server's worker threads.
- If rendering fails, the visitor sees a short Arabic message. Server paths and Chrome's output go to the server log only.
- Share tokens are 256-bit `secrets.token_urlsafe` values, and the app checks their exact format before it builds a file path from one.
- Report pages are sent with `noindex`, `no-referrer` and `private, no-store` headers.

## Limitations

- `sample.pdf` embeds subsets of the Tahoma font, as its embedding setting permits; set `font_file` in
  `brand.json` to embed an openly licensed font instead.

- Share links never expire and cannot be revoked from the app. There is no login and no rate limit, so put it behind your own auth or proxy before exposing it publicly.
- The PNG shows at most 5 A4 pages. The PDF has no page numbers.
- Arabic fonts must be installed on the server, or embedded through `font_file` in `brand.json`.
- Chrome runs without `--no-sandbox`. Running as root inside Docker needs that flag added.

## ملخص بالعربية

يعرض هذا المثال تطبيق ويب (FastAPI) يملأ فيه المستخدم نموذجًا عربيًا منظّمًا من اليمين إلى اليسار. يشمل النموذج العنوان والجهة والتاريخ والملخص التنفيذي وجدول المؤشرات والتوصيات، مع إمكانية رفع شعار. يُنشئ التطبيق تقريرًا بهوية العلامة التجارية (الشعار والألوان والخطوط والتخطيط المعتمد) من ملف إعدادات JSON، ثم يصدّره بصيغتَي PDF وPNG عبر متصفح Chrome دون واجهة. يحصل كل تقرير على رابط مشاركة فريد يصعب تخمينه، مع أزرار للنسخ والمشاركة والتحميل. جميع البيانات في هذا المثال وهمية، و«مؤسسة تجريبية (Demo Co.)» جهة غير حقيقية أُنشئت لأغراض العرض فقط.
