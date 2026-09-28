from __future__ import annotations

import base64
import html
import io
import json
import random
import re
import subprocess
import threading
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from arabic_report import (Brand, ChromeNotFound, RenderError, ReportIn, UploadError, clean_image, create_app,
                           find_chrome, format_date, load_brand, new_token, render_files, render_html)
from arabic_report.app import RENDER_SLOTS
from arabic_report.chrome import PAGE_HEIGHT_PX, PAGE_WIDTH_PX, SCALE, pdf_command, png_command
from arabic_report.core import MAX_UPLOAD_BYTES, SAMPLE_BANNER, TOOLBAR_SCRIPT, esc_bidi, is_token

FORM = {
    "title": "تقرير الأداء الشهري",
    "organisation": "جهة تجريبية",
    "prepared_by": "فريق التحليل",
    "report_date": "2026-09-28",
    "summary": "السطر الأول\nالسطر الثاني",
    "recommendations": "توصية أولى\n\nتوصية ثانية",
}
FORM_ROWS = {"item_label": ["المبيعات", "", "الطلبات"], "item_value": ["١٢٬٥٠٠ ر.س", "", "340"],
             "item_note": ["", "", "شهر سبتمبر"]}
EVIL = '<script>alert("x")</script>'


def png_bytes(size=(40, 20), fmt="PNG", colour=(15, 76, 92)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, fmt)
    return out.getvalue()


def sample_report(**changes) -> ReportIn:
    data = {"title": "تقرير تجريبي", "organisation": "جهة تجريبية", "report_date": date(2026, 9, 28),
            "summary": "ملخص", "rows": [{"label": "المبيعات", "value": "100"}], "recommendations": ["توصية"]}
    return ReportIn(**{**data, **changes})


class FakeRenderer:
    def __init__(self):
        self.calls = []

    def __call__(self, html_path, pdf_path, png_path):
        self.calls.append((html_path, pdf_path, png_path))
        Path(pdf_path).write_bytes(b"%PDF-1.4 fake")
        Path(png_path).write_bytes(png_bytes())


@pytest.fixture
def renderer():
    return FakeRenderer()


@pytest.fixture
def client(tmp_path, renderer):
    brand = Brand(name="علامة تجريبية", digits="arabic")
    with TestClient(create_app(store_dir=tmp_path / "store", brand=brand, renderer=renderer)) as test_client:
        yield test_client


def post_report(client, data=None, files=None, **kwargs):
    fields = {**FORM, **FORM_ROWS, **(data or {})}
    return client.post("/report", data=fields, files=files or {"logo": ("", b"", "application/octet-stream")},
                       follow_redirects=False, **kwargs)


def test_form_renders_rtl_arabic(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert '<html lang="ar" dir="rtl">' in body
    assert 'enctype="multipart/form-data"' in body and 'action="report"' in body
    for name in ("title", "organisation", "report_date", "summary", "recommendations", "logo"):
        assert f'name="{name}"' in body
    assert body.count('name="item_label"') == 6
    assert "Tahoma" in body and "علامة تجريبية" in body
    assert "form-action 'self'" in response.headers["content-security-policy"]


def test_report_html_is_rtl_with_arabic_dates_and_digits():
    page = render_html(sample_report(rows=[{"label": "أ"}, {"label": "ب"}]), Brand())
    assert '<html lang="ar" dir="rtl">' in page
    assert format_date(date(2026, 9, 28)) == "٢٨ سبتمبر ٢٠٢٦"
    assert format_date(date(2026, 1, 5), "latin") == "5 يناير 2026"
    assert "٢٨ سبتمبر ٢٠٢٦" in page and '<td class="num">٢</td>' in page
    assert "<script" not in page and "script-src" not in page  # the PDF source runs no script at all


def test_user_input_is_escaped_everywhere():
    report = sample_report(title=EVIL, organisation='"><img src=x onerror=alert(1)>', prepared_by="a & b",
                           summary=f"سطر\n{EVIL}", rows=[{"label": "<b>x</b>", "value": "<i>1</i>", "note": "</td>"}],
                           recommendations=["<iframe src=//evil>"])
    for token in (None, new_token()):
        page = render_html(report, Brand(name="<svg onload=alert(1)>", footer="</style><script>x</script>"), token=token)
        assert "<script>alert" not in page and "<img src=x" not in page and "<iframe" not in page
        assert "<svg" not in page and "<b>x</b>" not in page and "</style><script>" not in page
        text = page.replace('<bdi dir="ltr">', "").replace("</bdi>", "")  # drop our own LTR isolates
        assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in text
        assert "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;" in text and "a &amp; b" in text
        scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
        assert scripts == ([TOOLBAR_SCRIPT] if token else [])


@pytest.mark.parametrize("text, expected", [
    ("مؤسسة تجريبية (Demo Co.)", 'مؤسسة تجريبية <bdi dir="ltr">(Demo Co.)</bdi>'),
    ("أعلى من المستهدف (300 SAR)", 'أعلى من المستهدف <bdi dir="ltr">(300 SAR)</bdi>'),
    ("من 1 إلى 30 ر.س", "من 1 إلى 30 ر.س"),  # digits alone already render correctly
    # one isolate per Latin phrase: splitting at "?", "=", "–", "$" or accented letters reversed the pieces
    ("الرابط https://demo.test/a?b=1&c=2 هنا", 'الرابط <bdi dir="ltr">https://demo.test/a?b=1&amp;c=2</bdi> هنا'),
    ("المعادلة A = B صحيحة", 'المعادلة <bdi dir="ltr">A = B</bdi> صحيحة'),
    ("الفترة Q3 – Q4 تقريبًا", 'الفترة <bdi dir="ltr">Q3 – Q4</bdi> تقريبًا'),
    ("مورد Café Müller الجديد", 'مورد <bdi dir="ltr">Café Müller</bdi> الجديد'),
    ("الإيرادات $5M هذا العام", 'الإيرادات <bdi dir="ltr">$5M</bdi> هذا العام'),
    # sentence punctuation and brackets opened in Arabic stay with the RTL line
    ("نستخدم نظام SAP. ثم Oracle.", 'نستخدم نظام <bdi dir="ltr">SAP</bdi>. ثم <bdi dir="ltr">Oracle</bdi>.'),
    ("المنتج Demo (نسخة تجريبية)", 'المنتج <bdi dir="ltr">Demo</bdi> (نسخة تجريبية)'),
    ("(نسخة تجريبية) Demo متاح", '(نسخة تجريبية) <bdi dir="ltr">Demo</bdi> متاح'),
    ('قال "مرحبا Demo" أمس', 'قال &quot;مرحبا <bdi dir="ltr">Demo</bdi>&quot; أمس'),
    ("R&D <x>", '<bdi dir="ltr">R&amp;D &lt;x&gt;</bdi>'),
])
def test_latin_runs_are_isolated_inside_rtl_text(text, expected):
    assert esc_bidi(text) == expected


def test_esc_bidi_keeps_every_character_and_leaks_no_markup():
    rnd = random.Random(7)  # fixed seed: the same 3000 mixed strings on every run
    alphabet = "aZé9 .,:;!?()[]\"'<>&/=$-–·\nمثال١٢٪،"
    for _ in range(3000):
        text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 30)))
        inner = esc_bidi(text).replace('<bdi dir="ltr">', "").replace("</bdi>", "")
        assert "<" not in inner and ">" not in inner and html.unescape(inner) == text


def test_escaping_through_the_web_app(client):
    response = post_report(client, {"title": EVIL})
    assert response.status_code == 303
    view = client.get("/" + response.headers["location"])
    assert "<script>alert" not in view.text
    assert "&lt;script&gt;alert" in view.text.replace('<bdi dir="ltr">', "").replace("</bdi>", "")


def test_brand_config_applied(tmp_path):
    (tmp_path / "logo.png").write_bytes(png_bytes((900, 300)))
    (tmp_path / "brand.woff2").write_bytes(b"wOF2 not a real font, only embedded")
    (tmp_path / "brand.json").write_text(json.dumps({
        "name": "علامة الاختبار", "tagline": "شعار نصي", "footer": "تذييل العلامة", "logo": "logo.png",
        "font_file": "brand.woff2", "font_stack": "'Noto Naskh Arabic', Tahoma, serif", "layout": "minimal",
        "digits": "latin", "colors": {"primary": "#123456", "accent": "#ABCDEF"},
    }, ensure_ascii=False), encoding="utf-8")
    brand = load_brand(tmp_path / "brand.json")
    assert Image.open(io.BytesIO(brand._logo_png)).size == (600, 200)  # logo is re-encoded and bounded
    page = render_html(sample_report(), brand)
    assert "--primary: #123456;" in page and "--accent: #ABCDEF;" in page and "--bg: #FFFFFF;" in page
    assert "font-family: 'BrandFont', 'Noto Naskh Arabic', Tahoma, serif;" in page
    assert "src: url(data:font/woff2;base64," in page and "format('woff2')" in page
    assert '<body class="minimal digits-latin">' in page
    assert 'class="logo" src="data:image/png;base64,' in page
    assert "علامة الاختبار" in page and "شعار نصي" in page and "تذييل العلامة" in page
    assert "28 سبتمبر 2026" in page
    uploaded = render_html(sample_report(), brand, logo_png=png_bytes((10, 10), colour=(1, 2, 3)))
    assert uploaded != page  # an uploaded logo replaces the brand logo


@pytest.mark.parametrize("patch", [
    {"colors": {"primary": "red; } body { display: none"}},
    {"font_stack": "Arial; } * { color: red"},
    {"font_stack": "</style><script>"},
    {"font_stack": "Arial'"},  # an unclosed quote would swallow the rest of the stylesheet
    {"font_stack": "Arial,"},
    {"layout": "anything"},
    {"unknown_key": 1},
])
def test_brand_values_that_reach_css_are_validated(patch):
    with pytest.raises(ValidationError):
        Brand.model_validate(patch)


def test_sample_banner_is_explicit():
    assert SAMPLE_BANNER in render_html(sample_report(sample=True), Brand())
    assert SAMPLE_BANNER not in render_html(sample_report(), Brand())


def test_token_is_unguessable_and_strictly_formatted():
    tokens = {new_token() for _ in range(2000)}
    assert len(tokens) == 2000
    assert all(len(t) == 43 and re.fullmatch(r"[A-Za-z0-9_-]{43}", t) for t in tokens)
    assert len(set("".join(tokens))) > 60  # drawn from the whole url-safe alphabet
    for bad in ("", "abc", "../" + "a" * 40, "a" * 42 + ".", "a" * 44, "a" * 42 + "/"):
        assert not is_token(bad)


def test_create_view_and_download(client, renderer, tmp_path):
    response = post_report(client)
    assert response.status_code == 303
    token = response.headers["location"].removeprefix("r/")
    assert is_token(token) and len(renderer.calls) == 1
    folder = tmp_path / "store" / token
    assert {p.name for p in folder.iterdir()} == {"report.json", "report.html", "view.html", "report.pdf", "report.png"}
    saved = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    assert [r["label"] for r in saved["rows"]] == ["المبيعات", "الطلبات"]  # the empty middle row is dropped
    assert saved["recommendations"] == ["توصية أولى", "توصية ثانية"] and saved["sample"] is False

    view = client.get(f"/r/{token}")
    assert view.status_code == 200 and '<html lang="ar" dir="rtl">' in view.text
    assert f'href="{token}.pdf" download="report-2026-09-28.pdf"' in view.text
    assert f'href="{token}.png"' in view.text and 'id="share-url"' in view.text and 'id="copy"' in view.text
    assert view.headers["x-robots-tag"] == "noindex, nofollow" and view.headers["referrer-policy"] == "no-referrer"
    assert "script-src 'sha256-" in view.headers["content-security-policy"]

    pdf = client.get(f"/r/{token}.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF") and "private" in pdf.headers["cache-control"]
    png = client.get(f"/r/{token}.png")
    assert png.headers["content-type"] == "image/png" and Image.open(io.BytesIO(png.content)).format == "PNG"


def test_unknown_or_malformed_tokens_are_404(client):
    assert client.get(f"/r/{new_token()}").status_code == 404
    assert client.get(f"/r/{new_token()}.pdf").status_code == 404
    for bad in ("short", "..%2F..%2Fsecret", "a" * 44):
        assert client.get(f"/r/{bad}").status_code == 404
        assert client.get(f"/r/{bad}.png").status_code == 404


def test_form_validation_errors_are_arabic_html(client, renderer):
    response = post_report(client, {"title": "  ", "report_date": ""})
    assert response.status_code == 400 and "text/html" in response.headers["content-type"]
    assert "عنوان التقرير: حقل مطلوب" in response.text and "تاريخ التقرير: تاريخ غير صالح" in response.text
    assert renderer.calls == []


def test_clean_image_accepts_and_reencodes_real_images():
    for fmt in ("PNG", "JPEG", "WEBP"):
        cleaned = clean_image(png_bytes((1200, 400), fmt) + b"trailing junk")
        img = Image.open(io.BytesIO(cleaned))
        assert img.format == "PNG" and img.size == (600, 200) and not cleaned.endswith(b"trailing junk")
    # phones save "Ultra HDR" photos as JPEG + gain map, which Pillow reports as MPO
    out = io.BytesIO()
    Image.new("RGB", (80, 40), "teal").save(out, "MPO", save_all=True, append_images=[Image.new("RGB", (80, 40))])
    assert Image.open(io.BytesIO(out.getvalue())).format == "MPO"
    assert Image.open(io.BytesIO(clean_image(out.getvalue()))).size == (80, 40)


# a 60x40 WEBP whose EXIF "Make" tag was retyped (found by fuzzing): Pillow raised struct.error -> HTTP 500
CRAFTED_EXIF_WEBP = base64.b64decode(
    "UklGRpYAAABXRUJQVlA4WAoAAAAIAAAAOwAAJwAAVlA4IEQAAADQAwCdASo8ACgAPm02mEkkIyKhIqgAgA2JZwB2APwAADPfXxEH0gAA/q4X"
    "//5sCua5+lv//2d36M36d398xnRqsFAAAEVYSUYsAAAATU0AKgAAAAgAAgEYAAIAAAAFAAAAJgESAAMAAAABAAYAAAAAAABEZW1vAAA=")


@pytest.mark.parametrize("data, message", [
    (b"", "فارغ"),
    (b"%PDF-1.4 not an image", "ليس صورة"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "ليس صورة"),
    (png_bytes(fmt="GIF"), "غير مدعوم"),
    (png_bytes(fmt="BMP"), "غير مدعوم"),
    (png_bytes((4001, 4001)), "كبيرة"),
    (CRAFTED_EXIF_WEBP, "ليس صورة"),
], ids=["empty", "pdf", "svg", "gif", "bmp", "too-many-pixels", "crafted-exif"])
def test_clean_image_rejects_bad_uploads(data, message):
    with pytest.raises(UploadError, match=message):
        clean_image(data)


def test_clean_image_rejects_oversize_files():
    with pytest.raises(UploadError, match="حجم"):
        clean_image(png_bytes() + b"\0" * MAX_UPLOAD_BYTES)


def test_upload_is_checked_by_content_not_filename(client, renderer):
    fake = post_report(client, files={"logo": ("logo.png", b"MZ\x90\x00 not really a png", "image/png")})
    assert fake.status_code == 400 and "الشعار: الملف ليس صورة صالحة" in fake.text
    big = post_report(client, files={"logo": ("logo.png", png_bytes() + b"\0" * MAX_UPLOAD_BYTES, "image/png")})
    assert big.status_code == 400 and "حجم الصورة" in big.text
    huge = post_report(client, files={"logo": ("logo.png", b"\0" * (MAX_UPLOAD_BYTES + 300_000), "image/png")})
    assert huge.status_code == 413
    ok = post_report(client, files={"logo": ("../../evil.exe", png_bytes(), "application/x-msdownload")})
    assert ok.status_code == 303 and renderer.calls
    html_path = renderer.calls[-1][0]
    assert "data:image/png;base64," in html_path.read_text(encoding="utf-8")
    assert not any("evil" in p.name for p in html_path.parent.parent.rglob("*"))


def test_chunked_upload_without_content_length_is_still_limited(client, renderer):
    fields = "".join(f'--b\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in FORM.items())
    head = (fields + '--b\r\nContent-Disposition: form-data; name="logo"; filename="a.png"\r\n'
            "Content-Type: image/png\r\n\r\n").encode()

    def body():  # a generator makes httpx send Transfer-Encoding: chunked, so there is no Content-Length to check
        yield head
        for _ in range(3):
            yield b"\0" * MAX_UPLOAD_BYTES
        yield b"\r\n--b--\r\n"

    response = client.post("/report", content=body(), headers={"Content-Type": "multipart/form-data; boundary=b"},
                           follow_redirects=False)
    assert response.status_code == 413 and "حجم الطلب" in response.text and renderer.calls == []


def test_validation_errors_name_the_row_the_user_filled(client):
    rows = {"item_label": ["المبيعات", "", ""], "item_value": ["100", "", "200"], "item_note": ["", "", ""]}
    response = post_report(client, {**rows, "recommendations": "أولى\n\n" + "س" * 501})
    assert response.status_code == 400
    assert "المؤشرات الرئيسية (السطر 3): حقل مطلوب" in response.text  # row 2 was empty and dropped
    assert "التوصيات (السطر 3): النص أطول من الحد المسموح" in response.text


def test_render_failure_returns_clear_error_and_keeps_no_folder(tmp_path):
    def broken(html_path, pdf_path, png_path):
        raise ChromeNotFound(r"CHROME_PATH points to 'C:\secret-dir\chrome.exe', which does not exist")

    with TestClient(create_app(store_dir=tmp_path / "s", brand=Brand(), renderer=broken)) as test_client:
        response = post_report(test_client)
    assert response.status_code == 503 and "CHROME_PATH" in response.text
    assert "secret-dir" not in response.text  # server paths go to the log, not to the visitor
    assert list((tmp_path / "s").iterdir()) == []


def test_concurrent_requests_share_a_few_chrome_slots(tmp_path):
    running, peak, lock = 0, 0, threading.Lock()

    def slow(html_path, pdf_path, png_path):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.15)
        with lock:
            running -= 1
        FakeRenderer()(html_path, pdf_path, png_path)

    with TestClient(create_app(store_dir=tmp_path / "s", brand=Brand(), renderer=slow)) as test_client:
        results = []
        threads = [threading.Thread(target=lambda: results.append(post_report(test_client).status_code))
                   for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert results == [303] * 6 and peak <= RENDER_SLOTS


def test_find_chrome_uses_chrome_path(tmp_path):
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")
    assert find_chrome({"CHROME_PATH": str(exe)}) == str(exe)
    with pytest.raises(ChromeNotFound, match="CHROME_PATH"):
        find_chrome({"CHROME_PATH": str(tmp_path / "missing.exe")})


def test_find_chrome_fails_clearly_when_nothing_is_installed(monkeypatch, tmp_path):
    monkeypatch.setattr("arabic_report.chrome.CANDIDATES", (str(tmp_path / "no-chrome.exe"),))
    monkeypatch.setattr("arabic_report.chrome.shutil.which", lambda name: None)
    with pytest.raises(ChromeNotFound, match="set CHROME_PATH"):
        find_chrome({})


def test_chrome_commands_are_built_correctly(tmp_path):
    html_path = tmp_path / "r.html"
    pdf = pdf_command("chrome", html_path, tmp_path / "r.pdf", "PROFILE")
    png = png_command("chrome", html_path, tmp_path / "r.png", "PROFILE", pages=2)
    for cmd in (pdf, png):
        assert cmd[0] == "chrome" and "--headless=new" in cmd and "--disable-gpu" in cmd
        assert "--user-data-dir=PROFILE" in cmd and cmd[-1] == html_path.resolve().as_uri()
    assert "--no-pdf-header-footer" in pdf and f"--print-to-pdf={tmp_path / 'r.pdf'}" in pdf
    assert f"--screenshot={tmp_path / 'r.png'}" in png
    assert f"--window-size={PAGE_WIDTH_PX},{PAGE_HEIGHT_PX * 2}" in png and f"--force-device-scale-factor={SCALE}" in png


def test_render_files_runs_chrome_twice_with_timeout(tmp_path):
    calls = []

    def fake_run(cmd, capture_output, timeout):
        calls.append((cmd, timeout))
        for arg in cmd:
            if arg.startswith("--print-to-pdf="):
                Path(arg.split("=", 1)[1]).write_bytes(b"%PDF-1.4 /Type /Pages /Type /Page x /Type /Page x")
            if arg.startswith("--screenshot="):
                Path(arg.split("=", 1)[1]).write_bytes(png_bytes())
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    render_files(tmp_path / "r.html", tmp_path / "r.pdf", tmp_path / "r.png", chrome="C:/chrome.exe",
                 timeout=12, run=fake_run)
    (pdf_cmd, t1), (png_cmd, t2) = calls
    assert t1 == t2 == 12 and pdf_cmd[0] == png_cmd[0] == "C:/chrome.exe"
    assert any(a.startswith("--print-to-pdf=") for a in pdf_cmd)
    assert f"--window-size={PAGE_WIDTH_PX},{PAGE_HEIGHT_PX * 2}" in png_cmd  # 2 pages in the PDF -> 2 A4 heights


def test_render_files_reports_chrome_failures(tmp_path):
    def exits_1(cmd, capture_output, timeout):
        return subprocess.CompletedProcess(cmd, 1, b"", b"boom: sandbox error")

    def hangs(cmd, capture_output, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    with pytest.raises(RenderError, match="boom: sandbox error"):
        render_files(tmp_path / "r.html", tmp_path / "r.pdf", tmp_path / "r.png", chrome="c", run=exits_1)
    with pytest.raises(RenderError, match="within 5s"):
        render_files(tmp_path / "r.html", tmp_path / "r.pdf", tmp_path / "r.png", chrome="c", timeout=5, run=hangs)


def test_real_chrome_renders_pdf_and_png(tmp_path):
    try:
        chrome = find_chrome()
    except ChromeNotFound:
        pytest.skip("Chrome not installed")
    report = sample_report(sample=True, prepared_by="Demo Team", rows=[
        {"label": "المبيعات", "value": "زيادة ملحوظة في الإيرادات مقارنة بالعام السابق وبنسبة كبيرة"},
        {"label": "مرجع", "value": "1", "note": "REF" + "0123456789" * 10},  # no break opportunity at all
    ])
    html_path = tmp_path / "r.html"
    html_path.write_text(render_html(report, Brand(name="علامة تجريبية")), encoding="utf-8")
    render_files(html_path, tmp_path / "r.pdf", tmp_path / "r.png", chrome=chrome)
    assert (tmp_path / "r.pdf").read_bytes().startswith(b"%PDF-")
    with Image.open(tmp_path / "r.png") as img:
        img.load()
        assert img.format == "PNG" and img.size == (PAGE_WIDTH_PX * SCALE, PAGE_HEIGHT_PX * SCALE)
        # long values and references wrap inside the table: an over-wide RTL table shifted the whole page sideways,
        # cutting off the header band on one side and spilling text into the 16 mm (121 px) side margins
        rgb = img.convert("RGB")
        assert {rgb.getpixel((x, 200)) for x in (4, img.width // 2, img.width - 5)} == {(15, 76, 92)}
        for side in ((0, 500, 110, img.height), (img.width - 110, 500, img.width, img.height)):
            assert rgb.crop(side).getextrema() == ((255, 255), (255, 255), (255, 255))
