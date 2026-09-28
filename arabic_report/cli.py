from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from .chrome import TIMEOUT, ChromeNotFound, RenderError, render_files
from .core import Brand, ReportIn, UploadError, clean_image, load_brand, render_html


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="arabic_report",
                                     description="Branded Arabic (RTL) reports: web form -> PDF + PNG + share link")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the web app with uvicorn")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--store", default=None, help="report folder (default: $ARABIC_REPORT_STORE or arabic_reports)")
    serve.add_argument("--brand", default=None, help="brand config JSON (default: $ARABIC_REPORT_BRAND or built-in)")
    render = sub.add_parser("render", help="render one report from a JSON file with the form's fields")
    render.add_argument("data", help="report JSON: title, organisation, report_date, summary, rows, recommendations...")
    render.add_argument("--brand", default=None, help="brand config JSON")
    render.add_argument("--logo", default=None, help="logo image that overrides the brand logo")
    render.add_argument("--out", default=".", help="output folder")
    render.add_argument("--name", default="report", help="base file name (writes <name>.html/.pdf/.png)")
    render.add_argument("--timeout", type=float, default=TIMEOUT, help="seconds allowed per Chrome run")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(store_dir=args.store, brand=args.brand), host=args.host, port=args.port)
        return 0
    try:
        brand = load_brand(args.brand) if args.brand else Brand()
        report = ReportIn.model_validate_json(Path(args.data).read_text(encoding="utf-8-sig"))
        logo = clean_image(Path(args.logo).read_bytes()) if args.logo else None
    except (ValidationError, UploadError, ValueError, OSError) as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    html_path, pdf_path, png_path = (out / f"{args.name}{ext}" for ext in (".html", ".pdf", ".png"))
    html_path.write_text(render_html(report, brand, logo), encoding="utf-8")
    try:
        render_files(html_path, pdf_path, png_path, timeout=args.timeout)
    except (ChromeNotFound, RenderError) as exc:
        print(f"render failed: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {html_path}, {pdf_path}, {png_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
