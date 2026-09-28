"""Branded Arabic (RTL) report: validated form data + brand config -> one self-contained HTML page.

Everything a user or brand file supplies as text goes through html.escape before it reaches a template; colours and
the font stack are pattern-checked because they land in CSS. Logo and brand font are embedded as data: URIs and the
page carries a Content-Security-Policy that blocks scripts and all network fetches, so headless Chrome renders only
inline content. With a share token the same page becomes the web view (toolbar with share + download buttons).
"""
from __future__ import annotations

import base64
import hashlib
import html
import io
import re
import secrets
from datetime import date
from pathlib import Path
from string import Template
from typing import Annotated, Literal

from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StringConstraints

TOKEN_BYTES = 32  # 256 bits -> 43 url-safe characters
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_PIXELS = 4000 * 4000  # checked before decoding, so a tiny file cannot expand into a huge bitmap
IMAGE_FORMATS = {"PNG", "JPEG", "MPO", "WEBP"}  # MPO: a JPEG with extra frames (phone "Ultra HDR" photos)
LOGO_MAX_SIDE = 600
MAX_BRAND_FILE_BYTES = 5 * 1024 * 1024
FONT_FORMATS = {".ttf": ("font/ttf", "truetype"), ".otf": ("font/otf", "opentype"),
                ".woff": ("font/woff", "woff"), ".woff2": ("font/woff2", "woff2")}
DEFAULT_FONT_STACK = "Tahoma, 'Segoe UI', Arial, 'Noto Naskh Arabic', 'Noto Sans Arabic', sans-serif"
HEX = r"^#[0-9A-Fa-f]{6}$"
# comma-separated family names, bare or in matching quotes: no ; { } < or stray quote can break out of the CSS rule
_FAMILY = r"""\s*(?:[A-Za-z0-9 -]+|'[A-Za-z0-9 -]+'|"[A-Za-z0-9 -]+")\s*"""
FONT_STACK_RE = rf"^{_FAMILY}(?:,{_FAMILY})*$"
# Bidi isolation: a stretch without RTL letters (or Arabic digits/punctuation) that contains an LTR letter is shown
# as one LTR unit, minus the sentence punctuation and unmatched brackets/quotes at its edges, which belong to the
# surrounding RTL line (so "SAP." ends an Arabic sentence correctly while "(Demo Co.)" stays whole).
NON_RTL_RUN = re.compile("[^\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFC\n]+")
LTR_LETTER = re.compile("[A-Za-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F\u0370-\u03FF\u0400-\u04FF\u1E00-\u1EFF]")
EDGE_PUNCT = ".,:;!?\u00B7\u2022\u2013\u2014-"  # incl. middle dot, bullet, en/em dash
BRACKET_PAIRS = {"(": ")", ")": "(", "[": "]", "]": "["}
ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
MONTHS = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
          "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")
SAMPLE_BANNER = "نموذج تجريبي — جميع البيانات وهمية لأغراض العرض فقط · SAMPLE / DEMO DATA"

REPORT_CSP = ("default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'; "
              "base-uri 'none'; form-action 'none'")

# The only script ever served: fills and copies the share link. Allowed by its hash, not by 'unsafe-inline'.
TOOLBAR_SCRIPT = """
const box = document.getElementById("share-url");
box.value = location.href.split("#")[0];
document.getElementById("copy").addEventListener("click", async (event) => {
  box.select();
  try { await navigator.clipboard.writeText(box.value); } catch (err) { document.execCommand("copy"); }
  event.target.textContent = "تم نسخ الرابط";
});
const share = document.getElementById("share");
if (navigator.share) {
  share.hidden = false;
  share.addEventListener("click", () => navigator.share({ title: document.title, url: box.value }).catch(() => {}));
}
"""
SCRIPT_HASH = base64.b64encode(hashlib.sha256(TOOLBAR_SCRIPT.encode()).digest()).decode()
VIEW_CSP = f"{REPORT_CSP}; script-src 'sha256-{SCRIPT_HASH}'"


class UploadError(Exception):
    """Rejected image. The message is Arabic because it is shown to the person filling the form."""


class Colors(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: str = Field("#0F4C5C", pattern=HEX)
    on_primary: str = Field("#FFFFFF", pattern=HEX)  # text on the primary colour (header band, table head)
    accent: str = Field("#E36414", pattern=HEX)
    text: str = Field("#1F2933", pattern=HEX)
    background: str = Field("#FFFFFF", pattern=HEX)
    surface: str = Field("#F3F5F7", pattern=HEX)  # info box and zebra rows


class Brand(BaseModel):
    """Brand config JSON: identity, colours, fonts and one of the approved layouts."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field("", max_length=120)
    tagline: str = Field("", max_length=200)
    footer: str = Field("", max_length=300)
    logo: str | None = None  # image path, relative to the brand JSON
    font_file: str | None = None  # .ttf/.otf/.woff/.woff2 embedded in the report, for servers without the font
    font_stack: str = Field(DEFAULT_FONT_STACK, pattern=FONT_STACK_RE, max_length=300)
    colors: Colors = Field(default_factory=Colors)
    layout: Literal["band", "minimal"] = "band"
    digits: Literal["arabic", "latin"] = "arabic"  # digits used in generated dates and row numbers
    _logo_png: bytes | None = PrivateAttr(None)
    _font: tuple[str, bytes] | None = PrivateAttr(None)


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=200)
    value: str = Field("", max_length=100)
    note: str = Field("", max_length=300)


class ReportIn(BaseModel):
    """The structured form. Same field names in the web form and in the CLI's JSON input."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    organisation: str = Field(min_length=1, max_length=200)
    prepared_by: str = Field("", max_length=120)
    report_date: date
    summary: str = Field("", max_length=5000)
    rows: list[Row] = Field(default_factory=list, max_length=30)
    recommendations: list[Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]] = \
        Field(default_factory=list, max_length=20)
    sample: bool = False  # adds the "sample / demo data" banner


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def is_token(value: str) -> bool:
    return bool(TOKEN_RE.fullmatch(value))


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def _unmatched(text: str, char: str) -> bool:
    if char in BRACKET_PAIRS:
        return text.count(char) != text.count(BRACKET_PAIRS[char])
    return char == '"' and text.count('"') % 2 == 1


def esc_bidi(text: str) -> str:
    """Escape, and isolate Latin runs as LTR so "(Demo Co.)", "(300 SAR)" or a URL keep their order in an RTL line.

    Isolation is applied to the raw text and each piece is escaped separately, so it can never split an entity."""
    parts, pos = [], 0
    for match in NON_RTL_RUN.finditer(text):
        start, end = match.span()
        while start < end:
            first, last, run = text[start], text[end - 1], text[start:end]
            if first.isspace() or first in EDGE_PUNCT or _unmatched(run, first):
                start += 1
            elif last.isspace() or last in EDGE_PUNCT or _unmatched(run, last):
                end -= 1
            else:
                break
        if LTR_LETTER.search(text, start, end):
            parts += [esc(text[pos:start]), f'<bdi dir="ltr">{esc(text[start:end])}</bdi>']
            pos = end
    parts.append(esc(text[pos:]))
    return "".join(parts)


def localize_digits(text: str, digits: str) -> str:
    return text.translate(ARABIC_DIGITS) if digits == "arabic" else text


def format_date(value: date, digits: str = "arabic") -> str:
    return localize_digits(f"{value.day} {MONTHS[value.month - 1]} {value.year}", digits)


def clean_image(data: bytes, max_bytes: int = MAX_UPLOAD_BYTES, max_side: int = LOGO_MAX_SIDE) -> bytes:
    """Check an image by its content (never its filename) and re-encode it as a fresh PNG.

    Re-encoding keeps only decoded pixels: EXIF/GPS metadata and anything appended to the file are dropped."""
    if not data:
        raise UploadError("الملف فارغ")
    if len(data) > max_bytes:
        raise UploadError(f"حجم الصورة أكبر من الحد المسموح ({max_bytes // (1024 * 1024)} ميغابايت)")
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in IMAGE_FORMATS:
            raise UploadError("نوع الملف غير مدعوم؛ المسموح: PNG أو JPEG أو WEBP")
        if img.width * img.height > MAX_UPLOAD_PIXELS:
            raise UploadError("أبعاد الصورة كبيرة جدًا")
        img = ImageOps.exif_transpose(img).convert("RGBA")
    except UploadError:
        raise
    except Exception as exc:  # crafted files raise more than OSError (e.g. struct.error from a bad EXIF tag)
        raise UploadError("الملف ليس صورة صالحة") from exc
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def load_brand(path: str | Path) -> Brand:
    """Read and validate a brand JSON; logo and font paths are relative to the JSON file."""
    path = Path(path)
    brand = Brand.model_validate_json(path.read_text(encoding="utf-8-sig"))
    if brand.logo:
        brand._logo_png = clean_image((path.parent / brand.logo).read_bytes(), max_bytes=MAX_BRAND_FILE_BYTES)
    if brand.font_file:
        font = path.parent / brand.font_file
        suffix = font.suffix.lower()
        if suffix not in FONT_FORMATS:
            raise ValueError(f"font_file must be one of {', '.join(FONT_FORMATS)}: {font.name}")
        data = font.read_bytes()
        if len(data) > MAX_BRAND_FILE_BYTES:
            raise ValueError(f"font_file is larger than {MAX_BRAND_FILE_BYTES // (1024 * 1024)} MB: {font.name}")
        brand._font = (suffix, data)
    return brand


def data_uri(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def font_css(brand: Brand) -> tuple[str, str]:
    """(@font-face rule or "", font-family value)."""
    if not brand._font:
        return "", brand.font_stack
    mime, fmt = FONT_FORMATS[brand._font[0]]
    face = f"@font-face {{ font-family: 'BrandFont'; src: url({data_uri(mime, brand._font[1])}) format('{fmt}'); }}\n"
    return face, f"'BrandFont', {brand.font_stack}"


def colour_vars(brand: Brand) -> str:
    c = brand.colors
    return (f":root {{ --primary: {c.primary}; --on-primary: {c.on_primary}; --accent: {c.accent}; "
            f"--text: {c.text}; --bg: {c.background}; --surface: {c.surface}; }}\n")


REPORT_CSS = """
@page { size: A4; margin: 12mm 0; }
@page :first { margin-top: 0; }  /* the header band runs to the top edge; later pages keep a margin */
* { box-sizing: border-box; }
html, body { margin: 0; }
body { background: var(--bg); color: var(--text); font-size: 11pt; line-height: 1.65; overflow-wrap: break-word;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.page { width: 210mm; margin: 0 auto; background: var(--bg); }
.sample { margin: 0; padding: 2mm 16mm; background: #FFF4CC; color: #6B5000; border-bottom: 0.3mm solid #E8D48A;
          font-size: 9pt; text-align: center; }
.masthead { display: flex; align-items: center; gap: 7mm; padding: 10mm 16mm 8mm; }
.band .masthead { background: var(--primary); color: var(--on-primary); border-bottom: 2mm solid var(--accent); }
.minimal .masthead { border-bottom: 0.7mm solid var(--accent); }
.masthead > div { min-width: 0; }
.logo { flex: none; width: 24mm; height: 24mm; object-fit: contain; background: #FFFFFF; border-radius: 3mm; padding: 1.5mm; }
.brand { margin: 0; font-size: 10pt; font-weight: bold; opacity: 0.9; }
.minimal .brand { color: var(--primary); }
.tagline { margin: 0; font-size: 9pt; opacity: 0.85; }
h1 { margin: 1mm 0; font-size: 20pt; line-height: 1.4; }
.minimal h1 { color: var(--primary); }
.meta { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 4mm; margin: 6mm 16mm 0;
        padding: 3.5mm 5mm; background: var(--surface); border-radius: 2mm; }
.meta dt { font-size: 8.5pt; opacity: 0.75; }
.meta dd { margin: 0; font-weight: bold; }
section { margin: 6mm 16mm 0; }
h2 { margin: 0 0 2.5mm; padding-inline-start: 3mm; border-inline-start: 1.2mm solid var(--accent);
     color: var(--primary); font-size: 13pt; line-height: 1.5; break-after: avoid; }
p { margin: 0 0 2mm; }
table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: 10pt; }  /* never wider than A4 */
col.num { width: 12mm; }
col.label { width: 26%; }
col.value { width: 20%; }
th { padding: 2mm 3mm; background: var(--primary); color: var(--on-primary); text-align: start; }
td { padding: 2mm 3mm; border-bottom: 0.3mm solid #D9DEE3; vertical-align: top; }
tr { break-inside: avoid; }
tbody tr:nth-child(even) td { background: var(--surface); }
td.num { color: var(--primary); font-weight: bold; white-space: nowrap; }
td.value { font-weight: bold; }
ol { margin: 0; padding-inline-start: 7mm; }
.digits-arabic ol { list-style-type: arabic-indic; }
li { margin-bottom: 1.5mm; }
footer { display: flex; justify-content: space-between; gap: 6mm; margin: 8mm 16mm 0; padding: 3mm 0 6mm;
         border-top: 0.3mm solid #D9DEE3; font-size: 8.5pt; opacity: 0.8; }
.toolbar { position: sticky; top: 0; z-index: 1; display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
           padding: 10px 16px; background: #FFFFFF; border-bottom: 1px solid #D9DEE3; font-size: 14px; }
.toolbar input { flex: 1 1 260px; min-width: 0; padding: 7px 9px; border: 1px solid #C5CCD3; border-radius: 6px;
                 direction: ltr; font: inherit; }
.toolbar button, .toolbar a { padding: 7px 14px; border: 0; border-radius: 6px; background: var(--primary);
                              color: var(--on-primary); font: inherit; text-decoration: none; cursor: pointer; }
.toolbar .notice { flex-basis: 100%; margin: 0; color: #5B6770; font-size: 12px; }
body.view { background: #E9ECEF; }
.view .page { margin: 16px auto; box-shadow: 0 1px 8px rgba(0, 0, 0, 0.12); }
@media screen and (max-width: 820px) {  /* phones, web view only: the PNG is shot at 794px and must keep A4 layout */
  .view .page { width: auto; margin: 0; }
  .view .masthead, .view .meta, .view section, .view footer { margin-inline: 0; padding-inline: 16px; }
  .view .meta { grid-template-columns: 1fr; }
}
@media print {
  .toolbar { display: none; }
  body.view { background: var(--bg); }
  .view .page { margin: 0; box-shadow: none; }
}
"""

REPORT_PAGE = Template("""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="$csp">
<meta name="robots" content="noindex, nofollow">
<title>$title</title>
<style>
$font_face$colours
body { font-family: $font_family; }
$css</style>
</head>
<body class="$body_class">
$toolbar<main class="page">
$sample<header class="masthead">
$logo<div>
$brand_name<h1>$heading</h1>
$tagline</div>
</header>
<dl class="meta">$meta</dl>
$sections<footer><span>$footer</span><span>$date</span></footer>
</main>
$script</body>
</html>
""")


def _toolbar(token: str, report_date: date) -> str:
    name = f"report-{report_date.isoformat()}"
    return (f'<nav class="toolbar" aria-label="أدوات التقرير">\n'
            f'<input id="share-url" readonly aria-label="رابط المشاركة" value="">\n'
            f'<button id="copy" type="button">نسخ رابط المشاركة</button>\n'
            f'<button id="share" type="button" hidden>مشاركة</button>\n'
            f'<a href="{esc(token)}.pdf" download="{name}.pdf">تحميل PDF</a>\n'
            f'<a href="{esc(token)}.png" download="{name}.png">تحميل صورة PNG</a>\n'
            f'<p class="notice">أي شخص يملك هذا الرابط يستطيع عرض التقرير وتحميله؛ شاركه مع المعنيين فقط.</p>\n'
            f'</nav>\n')


def _sections(report: ReportIn, digits: str) -> str:
    # Only free paragraphs get dir="auto" (an English paragraph reads LTR). Cells, list items and headings stay RTL
    # so a column or list never flips alignment per row; esc_bidi keeps any Latin inside them in order.
    parts = []
    paragraphs = [line.strip() for line in report.summary.splitlines() if line.strip()]
    if paragraphs:
        body = "".join(f'<p dir="auto">{esc_bidi(p)}</p>\n' for p in paragraphs)
        parts.append(f"<section>\n<h2>الملخص التنفيذي</h2>\n{body}</section>\n")
    if report.rows:
        rows = "".join(
            f'<tr><td class="num">{localize_digits(str(i), digits)}</td><td>{esc_bidi(r.label)}</td>'
            f'<td class="value"><bdi>{esc_bidi(r.value)}</bdi></td><td>{esc_bidi(r.note)}</td></tr>\n'
            for i, r in enumerate(report.rows, 1))
        parts.append("<section>\n<h2>المؤشرات الرئيسية</h2>\n<table>\n"
                     '<colgroup><col class="num"><col class="label"><col class="value"><col></colgroup>\n'
                     "<thead><tr><th>م</th><th>البند</th><th>القيمة</th><th>ملاحظات</th></tr></thead>\n"
                     f"<tbody>\n{rows}</tbody>\n</table>\n</section>\n")
    recommendations = [r for r in report.recommendations if r]
    if recommendations:
        items = "".join(f"<li>{esc_bidi(r)}</li>\n" for r in recommendations)
        parts.append(f"<section>\n<h2>التوصيات</h2>\n<ol>\n{items}</ol>\n</section>\n")
    return "".join(parts)


def render_html(report: ReportIn, brand: Brand, logo_png: bytes | None = None, token: str | None = None) -> str:
    """The report page for Chrome; with a share token, the web view of it (toolbar + one hashed script)."""
    digits = brand.digits
    face, family = font_css(brand)
    logo = logo_png or brand._logo_png
    when = format_date(report.report_date, digits)
    meta = [("الجهة", report.organisation), ("تاريخ التقرير", when), ("إعداد", report.prepared_by)]
    return REPORT_PAGE.substitute(
        csp=VIEW_CSP if token else REPORT_CSP,
        title=esc(report.title),
        heading=esc_bidi(report.title),
        font_face=face,
        colours=colour_vars(brand),
        font_family=family,
        css=REPORT_CSS,
        body_class=f"{brand.layout} digits-{digits}" + (" view" if token else ""),
        toolbar=_toolbar(token, report.report_date) if token else "",
        sample=f'<p class="sample">{esc(SAMPLE_BANNER)}</p>\n' if report.sample else "",
        logo=f'<img class="logo" src="{data_uri("image/png", logo)}" alt="{esc(brand.name or "الشعار")}">\n'
        if logo else "",
        brand_name=f'<p class="brand">{esc_bidi(brand.name)}</p>\n' if brand.name else "",
        tagline=f'<p class="tagline">{esc_bidi(brand.tagline)}</p>\n' if brand.tagline else "",
        meta="".join(f"<div><dt>{label}</dt><dd>{esc_bidi(value)}</dd></div>" for label, value in meta if value),
        sections=_sections(report, digits),
        footer=esc_bidi(brand.footer),
        date=when,
        script=f"<script>{TOOLBAR_SCRIPT}</script>\n" if token else "",
    )
