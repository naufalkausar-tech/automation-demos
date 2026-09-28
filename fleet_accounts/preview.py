"""Dashboard preview as Markdown and PNG (HTML rendered by headless Chrome), from the Python mirror of the
workbook formulas. Excel recalculates the real workbook on open; this is for READMEs and quick review."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from html import escape
from pathlib import Path

from .core import ALL, BANK_METRICS, INT, METRICS, RULE_NOTE, SAMPLE_TAG

CHROME_PATHS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]
TITLE = "لوحة التحكم المالية - تأجير المركبات"


def _fmt(value, fmt):
    return f"{value:,.0f}" if fmt == INT else f"{value:,.2f}"


def _cell(text):
    return str(text).replace("|", "\\|")  # a pipe in a name would split the Markdown table row


def dashboard_markdown(figures, period, sample=False):
    lines = [f"# {TITLE}", ""]
    if sample:
        lines += [f"**{SAMPLE_TAG}** - all names and amounts are invented for the demo.", ""]
    lines += [f"Period / الفترة: {period[0]:%Y-%m-%d} → {period[1]:%Y-%m-%d}", "", f"> {RULE_NOTE}", "",
              "## مقارنة المؤسسات / Organisation comparison", ""]
    lines.append("| المؤسسة | " + " | ".join(h for _, h, _ in METRICS) + " |")
    lines.append("|---" * (len(METRICS) + 1) + "|")
    for org, fig in figures["orgs"].items():
        name = f"**{org}**" if org == ALL else _cell(org)
        lines.append(f"| {name} | " + " | ".join(_fmt(fig[k], f) for k, _, f in METRICS) + " |")
    lines += ["", "## الرصيد حسب الحساب البنكي / Balance by bank account", "",
              "| الحساب البنكي | " + " | ".join(h for _, h, _ in BANK_METRICS) + " |",
              "|---" * (len(BANK_METRICS) + 1) + "|"]
    for bank, fig in figures["banks"].items():
        lines.append(f"| {_cell(bank)} | " + " | ".join(_fmt(fig[k], f) for k, _, f in BANK_METRICS) + " |")
    return "\n".join(lines) + "\n"


def dashboard_html(figures, period, sample=False):
    total = figures["orgs"][ALL]
    tiles = "".join(f'<div class="tile{" hero" if k == "bank_balance" else ""}"><span>{escape(h)}</span>'
                    f"<b>{_fmt(total[k], f)}</b></div>" for k, h, f in METRICS)
    head = "".join(f"<th>{escape(h)}</th>" for _, h, _ in METRICS)
    rows = "".join(f'<tr{" class=total" if org == ALL else ""}><th>{escape(org)}</th>'
                   + "".join(f"<td>{_fmt(fig[k], f)}</td>" for k, _, f in METRICS) + "</tr>"
                   for org, fig in figures["orgs"].items())
    bank_head = "".join(f"<th>{escape(h)}</th>" for _, h, _ in BANK_METRICS)
    bank_rows = "".join(f"<tr><th>{escape(b)}</th>" + "".join(f"<td>{_fmt(fig[k], f)}</td>" for k, _, f in BANK_METRICS)
                        + "</tr>" for b, fig in figures["banks"].items())
    banner = f'<p class="sample">{escape(SAMPLE_TAG)}</p>' if sample else ""
    return f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>{TITLE}</title><style>
body{{margin:0;padding:24px 32px;background:#f6f7f9;color:#1b1b1b;font:15px Tahoma,'Segoe UI',Arial,sans-serif}}
h1{{margin:0 0 4px;font-size:24px}} h2{{font-size:17px;margin:22px 0 8px}}
.sample{{margin:4px 0;color:#c00000;font-weight:bold;font-size:17px}} .meta{{color:#555;margin:4px 0 14px}}
.tiles{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}}
.tile{{background:#fff;border:1px solid #dde1e6;border-radius:8px;padding:10px 14px}}
.tile span{{display:block;color:#555;font-size:13px}} .tile b{{font-size:20px}}
.tile.hero{{background:#1f4e78;color:#fff;border-color:#1f4e78}} .tile.hero span{{color:#dce8f5}}
table{{border-collapse:collapse;background:#fff;width:100%}}
th,td{{border:1px solid #dde1e6;padding:6px 8px;font-size:13px}} thead th{{background:#1f4e78;color:#fff}}
td{{text-align:left;font-variant-numeric:tabular-nums}} tbody th{{text-align:right;white-space:nowrap}}
tr.total{{font-weight:bold;background:#eef3f9}} .rule{{margin-top:16px;color:#9c0006;font-weight:bold}}
.banks{{width:60%}} .foot{{color:#666;font-size:12px}}</style></head><body>
<h1>{TITLE}</h1>{banner}
<p class="meta">الفترة: <bdi dir="ltr">{period[0]:%Y-%m-%d}</bdi> إلى <bdi dir="ltr">{period[1]:%Y-%m-%d}</bdi>
· المؤسسة المختارة: {ALL}</p>
<div class="tiles">{tiles}</div>
<h2>مقارنة المؤسسات</h2><table><thead><tr><th>المؤسسة</th>{head}</tr></thead><tbody>{rows}</tbody></table>
<h2>الرصيد حسب الحساب البنكي</h2><table class="banks"><thead><tr><th>الحساب البنكي</th>{bank_head}</tr></thead>
<tbody>{bank_rows}</tbody></table>
<p class="rule">{escape(RULE_NOTE)}</p>
<p class="foot">معاينة محسوبة بمحاكاة Python لصيغ المصنف؛ ملف Excel نفسه يعيد الحساب بالصيغ عند فتحه.</p></body></html>"""


def find_chrome():
    for path in CHROME_PATHS:
        if Path(path).exists():
            return path
    return shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium")


def render_png(html, png_path, chrome=None, size=(1400, 760)):
    """Screenshot the HTML with headless Chrome. Returns the PNG path, or None when Chrome is unavailable."""
    chrome = chrome or find_chrome()
    if not chrome:
        return None
    png_path = Path(png_path).resolve()
    png_path.unlink(missing_ok=True)  # so a PNG left by an earlier run is never reported as this one
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # Chrome may still hold profile files
        page = Path(tmp) / "dashboard.html"
        page.write_text(html, encoding="utf-8")
        # a throwaway profile keeps this separate from any Chrome window the user has open
        try:
            subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={tmp}",
                            f"--window-size={size[0]},{size[1]}", f"--screenshot={png_path}", page.as_uri()],
                           capture_output=True, timeout=90, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
    return png_path if png_path.exists() else None
