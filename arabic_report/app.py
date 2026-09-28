"""FastAPI app: Arabic RTL form -> branded report (PDF + PNG) behind an unguessable share link.

Reports live in <store>/<token>/ (report.json, report.html, view.html, report.pdf, report.png). The token is the only
key: 256 random bits, checked against its exact format before it is ever joined to a path.
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import date
from itertools import zip_longest
from pathlib import Path
from string import Template
from typing import Callable

import anyio
import anyio.to_thread
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from .chrome import ChromeNotFound, RenderError, render_files
from .core import (MAX_UPLOAD_BYTES, VIEW_CSP, Brand, ReportIn, UploadError, clean_image, colour_vars, data_uri, esc,
                   esc_bidi, font_css, is_token, load_brand, localize_digits, new_token, render_html)

log = logging.getLogger("arabic_report")

MAX_BODY_BYTES = MAX_UPLOAD_BYTES + 256 * 1024
RENDER_SLOTS = 2  # Chrome instances at once; further requests wait for a slot instead of starting more browsers
FORM_ROWS = 6
FORM_CSP = ("default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'")
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
PRIVATE_HEADERS = {**SECURITY_HEADERS, "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow"}
FIELD_LABELS = {"title": "عنوان التقرير", "organisation": "الجهة / العميل", "prepared_by": "إعداد",
                "report_date": "تاريخ التقرير", "summary": "الملخص التنفيذي", "rows": "المؤشرات الرئيسية",
                "recommendations": "التوصيات"}
ERROR_TEXT = {"missing": "حقل مطلوب", "string_too_short": "حقل مطلوب", "string_too_long": "النص أطول من الحد المسموح",
              "too_long": "عدد العناصر أكبر من الحد المسموح", "date_from_datetime_parsing": "تاريخ غير صالح",
              "date_parsing": "تاريخ غير صالح", "date_type": "تاريخ غير صالح"}

Renderer = Callable[[Path, Path, Path], None]

PAGE_CSS = """
* { box-sizing: border-box; }
body { margin: 0; background: #EEF1F4; color: var(--text); line-height: 1.6; }
main { max-width: 820px; margin: 0 auto; padding: 16px; }
.head { display: flex; align-items: center; gap: 14px; padding: 18px; border-radius: 10px 10px 0 0;
        background: var(--primary); color: var(--on-primary); border-bottom: 4px solid var(--accent); }
.head img { width: 56px; height: 56px; object-fit: contain; background: #FFFFFF; border-radius: 8px; padding: 4px; }
.head h1 { margin: 0; font-size: 22px; }
.head p { margin: 0; opacity: 0.9; }
form, .box { background: #FFFFFF; padding: 18px; border-radius: 0 0 10px 10px; }
fieldset { margin: 0 0 16px; padding: 12px 14px; border: 1px solid #D9DEE3; border-radius: 8px; }
legend { padding: 0 6px; color: var(--primary); font-weight: bold; }
label { display: block; margin-bottom: 10px; font-weight: bold; font-size: 14px; }
input, textarea { display: block; width: 100%; margin-top: 4px; padding: 8px 10px; border: 1px solid #C5CCD3;
                  border-radius: 6px; font: inherit; font-weight: normal; background: #FFFFFF; color: inherit; }
input:focus, textarea:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.rows { display: grid; grid-template-columns: 2fr 1fr 2fr; gap: 6px; font-size: 13px; }
.rows span { font-weight: bold; color: var(--primary); }
.rows input { margin: 0; }
small { color: #5B6770; }
.check { display: flex; align-items: center; gap: 8px; font-weight: normal; }
.check input { width: auto; margin: 0; }
button { padding: 10px 22px; border: 0; border-radius: 6px; background: var(--primary); color: var(--on-primary);
         font: inherit; font-weight: bold; cursor: pointer; }
.errors { color: #9B1C1C; }
@media (max-width: 600px) { .two, .rows { grid-template-columns: 1fr; } .rows span { display: none; } }
"""

PAGE = Template("""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title</title>
<style>
$font_face$colours
body { font-family: $font_family; }
$css</style>
</head>
<body>
<main>
<div class="head">$logo<div><h1>$title</h1>$brand_name</div></div>
$body
</main>
</body>
</html>
""")

FORM_BODY = Template("""<form method="post" action="report" enctype="multipart/form-data">
<fieldset><legend>بيانات التقرير</legend>
<label>عنوان التقرير <input name="title" required maxlength="200" dir="auto"></label>
<label>الجهة / العميل <input name="organisation" required maxlength="200" dir="auto"></label>
<div class="two">
<label>إعداد <input name="prepared_by" maxlength="120" dir="auto"></label>
<label>تاريخ التقرير <input type="date" name="report_date" required value="$today"></label>
</div>
</fieldset>
<fieldset><legend>الملخص التنفيذي</legend>
<textarea name="summary" rows="6" maxlength="5000" dir="auto" aria-label="الملخص التنفيذي"
 placeholder="فقرة أو أكثر؛ كل سطر يصبح فقرة مستقلة"></textarea>
</fieldset>
<fieldset><legend>المؤشرات الرئيسية</legend>
<div class="rows"><span>البند</span><span>القيمة</span><span>ملاحظات</span>
$rows</div>
<small>اترك الصفوف غير المستخدمة فارغة.</small>
</fieldset>
<fieldset><legend>التوصيات</legend>
<textarea name="recommendations" rows="4" dir="auto" aria-label="التوصيات" placeholder="توصية واحدة في كل سطر"></textarea>
</fieldset>
<fieldset><legend>الشعار (اختياري)</legend>
<input type="file" name="logo" accept="image/png,image/jpeg,image/webp" aria-label="الشعار">
<small>PNG أو JPEG أو WEBP بحجم أقصى $max_mb ميغابايت؛ يحل محل شعار العلامة في هذا التقرير فقط.</small>
</fieldset>
<label class="check"><input type="checkbox" name="sample"> وسم التقرير كنموذج تجريبي (بيانات وهمية)</label>
<button type="submit">إنشاء التقرير</button>
</form>""")


def page(brand: Brand, title: str, body: str) -> str:
    face, family = font_css(brand)
    logo = f'<img src="{data_uri("image/png", brand._logo_png)}" alt="">' if brand._logo_png else ""
    return PAGE.substitute(title=esc(title), font_face=face, colours=colour_vars(brand), font_family=family,
                           css=PAGE_CSS, logo=logo, body=body,
                           brand_name=f'<p dir="auto">{esc_bidi(brand.name)}</p>' if brand.name else "")


def form_page(brand: Brand, today: date | None = None) -> str:
    rows = "".join(f'<input name="item_label" maxlength="200" dir="auto" aria-label="البند {n}">'
                   f'<input name="item_value" maxlength="100" dir="auto" aria-label="القيمة {n}">'
                   f'<input name="item_note" maxlength="300" dir="auto" aria-label="ملاحظات {n}">\n'
                   for n in range(1, FORM_ROWS + 1))
    body = FORM_BODY.substitute(today=(today or date.today()).isoformat(), rows=rows,
                                max_mb=localize_digits(str(MAX_UPLOAD_BYTES // (1024 * 1024)), brand.digits))
    return page(brand, "إنشاء تقرير", body)


def error_page(brand: Brand, messages: list[str]) -> str:
    items = "".join(f'<li dir="auto">{esc_bidi(m)}</li>' for m in messages)
    body = (f'<div class="box"><ul class="errors">{items}</ul>'
            '<p>استخدم زر الرجوع في المتصفح لتعديل البيانات دون فقدانها، أو <a href="./">ابدأ نموذجًا جديدًا</a>.</p></div>')
    return page(brand, "تعذّر إنشاء التقرير", body)


class FormError(Exception):
    def __init__(self, messages: list[str]):
        super().__init__(messages)
        self.messages = messages


def validation_messages(exc: ValidationError, numbers: dict[str, list[int]] | None = None) -> list[str]:
    """Arabic messages; `numbers` maps a list field to the form row/line number of each item that was kept."""
    messages = []
    for err in exc.errors():
        loc = err["loc"]
        where = FIELD_LABELS.get(str(loc[0]), str(loc[0]))
        if loc[0] in ("rows", "recommendations") and len(loc) > 1 and isinstance(loc[1], int):
            shown = (numbers or {}).get(loc[0], [])
            where += f" (السطر {shown[loc[1]] if loc[1] < len(shown) else loc[1] + 1})"
        messages.append(f"{where}: {ERROR_TEXT.get(err['type'], err['msg'])}")
    return messages


def report_from_form(form) -> ReportIn:
    """Build the report from the posted form. Raises FormError with Arabic messages."""
    def text(name: str) -> str:
        value = form.get(name)
        return value if isinstance(value, str) else ""

    def texts(name: str) -> list[str]:
        return [v if isinstance(v, str) else "" for v in form.getlist(name)]

    # empty rows and blank lines are dropped, but errors must name the row/line the user actually sees
    cells = zip_longest(texts("item_label"), texts("item_value"), texts("item_note"), fillvalue="")
    rows = [(n, {"label": label, "value": value, "note": note})
            for n, (label, value, note) in enumerate(cells, 1) if (label + value + note).strip()]
    lines = [(n, line) for n, line in enumerate(text("recommendations").splitlines(), 1) if line.strip()]
    try:
        return ReportIn(title=text("title"), organisation=text("organisation"), prepared_by=text("prepared_by"),
                        report_date=text("report_date") or None, summary=text("summary"),
                        rows=[row for _, row in rows], recommendations=[line for _, line in lines],
                        sample=bool(text("sample")))
    except ValidationError as exc:
        numbers = {"rows": [n for n, _ in rows], "recommendations": [n for n, _ in lines]}
        raise FormError(validation_messages(exc, numbers)) from exc


class _BodyTooLarge(Exception):
    pass


class BodyLimit:
    """ASGI middleware: answers 413 once a request body passes `limit` bytes. The body is counted as it streams in,
    so a chunked upload (no Content-Length) or a false Content-Length cannot get past the limit."""

    def __init__(self, app, limit: int, too_large: Callable[[], HTMLResponse]):
        self.app, self.limit, self.too_large = app, limit, too_large

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and int(length) > self.limit:
            return await self.too_large()(scope, receive, send)
        seen = 0

        async def counted_receive():
            nonlocal seen
            message = await receive()
            seen += len(message.get("body", b""))
            if seen > self.limit:
                raise _BodyTooLarge
            return message

        try:
            await self.app(scope, counted_receive, send)
        except _BodyTooLarge:  # only POST /report reads a body, and it does so before it starts a response
            await self.too_large()(scope, receive, send)


def save_report(store: Path, report: ReportIn, brand: Brand, logo_png: bytes | None, renderer: Renderer) -> str:
    token = new_token()
    folder = store / token
    folder.mkdir(parents=True)
    try:
        (folder / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (folder / "report.html").write_text(render_html(report, brand, logo_png), encoding="utf-8")
        (folder / "view.html").write_text(render_html(report, brand, logo_png, token=token), encoding="utf-8")
        renderer(folder / "report.html", folder / "report.pdf", folder / "report.png")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return token


def create_app(store_dir: str | Path | None = None, brand: Brand | str | Path | None = None,
               renderer: Renderer | None = None) -> FastAPI:
    store = Path(store_dir or os.environ.get("ARABIC_REPORT_STORE", "arabic_reports"))
    store.mkdir(parents=True, exist_ok=True)
    brand = brand or os.environ.get("ARABIC_REPORT_BRAND") or Brand()
    brand = brand if isinstance(brand, Brand) else load_brand(brand)
    renderer = renderer or render_files
    # its own thread limiter: waiting requests queue without holding a worker thread the other routes need
    render_slots = anyio.CapacityLimiter(RENDER_SLOTS)

    app = FastAPI(title="Arabic branded reports", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)

    def html_response(content: str, status_code: int = 200) -> HTMLResponse:
        return HTMLResponse(content, status_code, headers={**SECURITY_HEADERS, "Content-Security-Policy": FORM_CSP})

    app.add_middleware(BodyLimit, limit=MAX_BODY_BYTES,
                       too_large=lambda: html_response(error_page(brand, ["حجم الطلب أكبر من الحد المسموح"]), 413))

    def stored(token: str, name: str) -> Path:
        if not is_token(token):
            raise HTTPException(status_code=404, detail="التقرير غير موجود")
        path = store / token / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="التقرير غير موجود")
        return path

    @app.get("/", response_class=HTMLResponse)
    def form() -> HTMLResponse:
        return html_response(form_page(brand))

    @app.post("/report")
    async def create_report(request: Request):
        form_data = await request.form(max_files=1, max_fields=200)
        try:
            report = report_from_form(form_data)
            upload, logo = form_data.get("logo"), None
            if isinstance(upload, UploadFile) and upload.filename:
                logo = clean_image(await upload.read(MAX_UPLOAD_BYTES + 1))
        except FormError as exc:
            return html_response(error_page(brand, exc.messages), 400)
        except UploadError as exc:
            return html_response(error_page(brand, [f"الشعار: {exc}"]), 400)
        try:
            token = await anyio.to_thread.run_sync(save_report, store, report, brand, logo, renderer,
                                                   limiter=render_slots)
        except (ChromeNotFound, RenderError) as exc:
            log.error("render failed: %s", exc)  # server paths and Chrome's output stay in the log, not the page
            hint = ("لم يُعثر على Google Chrome على الخادم؛ ثبّته أو اضبط المتغير CHROME_PATH."
                    if isinstance(exc, ChromeNotFound) else "راجع سجل الخادم للتفاصيل ثم أعد المحاولة.")
            return html_response(error_page(brand, ["تعذّر تحويل التقرير إلى PDF/PNG على الخادم.", hint]), 503)
        return RedirectResponse(f"r/{token}", status_code=303)

    # the .pdf/.png routes come first: "/r/{token}" alone would also match "<token>.pdf"
    @app.get("/r/{token}.pdf")
    def report_pdf(token: str) -> FileResponse:
        return FileResponse(stored(token, "report.pdf"), media_type="application/pdf", filename="report.pdf",
                            content_disposition_type="inline", headers=PRIVATE_HEADERS)

    @app.get("/r/{token}.png")
    def report_png(token: str) -> FileResponse:
        return FileResponse(stored(token, "report.png"), media_type="image/png", filename="report.png",
                            content_disposition_type="inline", headers=PRIVATE_HEADERS)

    @app.get("/r/{token}", response_class=HTMLResponse)
    def report_view(token: str) -> HTMLResponse:
        content = stored(token, "view.html").read_text(encoding="utf-8")
        return HTMLResponse(content, headers={**PRIVATE_HEADERS,
                                              "Content-Security-Policy": f"{VIEW_CSP}; frame-ancestors 'none'"})

    return app
