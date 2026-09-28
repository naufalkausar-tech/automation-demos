from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core import build_index, llm_reranker, load_inventory, results_markdown


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="library_search",
                                     description="Arabic + English search over a library's own inventory (CSV/Excel)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("inventory", help="CSV or XLSX with title, author, category, description, isbn, copies_available, shelf")
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", parents=[common], help="run queries, print the in-stock matches")
    search.add_argument("queries", nargs="+")
    search.add_argument("--synonyms", default=None, help="synonyms JSON (default: the bundled synonyms.json)")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--json", action="store_true", help="print JSON instead of text")
    search.add_argument("--markdown", default=None, help="also write the results as a Markdown file")
    search.add_argument("--title", default="Example queries")
    search.add_argument("--note", default="", help="quoted note at the top of the Markdown (e.g. 'SAMPLE DATA ...')")

    clean = sub.add_parser("clean", parents=[common], help="write the cleaned, de-duplicated inventory as CSV")
    clean.add_argument("--out", required=True)

    serve = sub.add_parser("serve", parents=[common], help="run the search page + API with uvicorn")
    serve.add_argument("--synonyms", default=None)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--notice", default="", help="banner on the page, e.g. 'SAMPLE DATA'")
    serve.add_argument("--reload-key", default=os.environ.get("LIBRARY_RELOAD_KEY"),
                       help="require this X-Reload-Key on POST /api/reload (default: $LIBRARY_RELOAD_KEY)")
    serve.add_argument("--rerank-provider", default=None,
                       help="optional LLM rerank via llm_extract clients: anthropic | package.module:factory (off by default)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # Arabic output on a Windows console or pipe
        sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "clean":
        inventory = load_inventory(args.inventory)
        inventory.write_csv(args.out)
        print(f"{inventory.rows_read} rows read, {inventory.rows_skipped} skipped (no title), "
              f"{len(inventory.merged)} duplicates merged -> {len(inventory.books)} titles in {args.out}")
        for line in inventory.merged:
            print("  " + line)
        return 0

    if args.command == "serve":
        import uvicorn

        from .app import create_app

        rerank = None
        if args.rerank_provider:
            from llm_extract.clients import load_client
            rerank = llm_reranker(load_client(args.rerank_provider))
        app = create_app(args.inventory, args.synonyms, rerank=rerank, notice=args.notice, reload_key=args.reload_key)
        if not args.reload_key and args.host not in ("127.0.0.1", "localhost", "::1"):
            print(f"warning: anyone who can reach {args.host}:{args.port} can call POST /api/reload; "
                  "set --reload-key to protect it", file=sys.stderr)
        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    index = build_index(args.inventory, args.synonyms)
    results = [index.search(q, limit=args.limit) for q in args.queries]
    if args.markdown:
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(results_markdown(results, index.stats(), args.title, args.note), encoding="utf-8")
    if args.json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
        return 0
    for result in results:
        print(f"\n== {result.query}  ({result.total} in stock, {result.hidden_out_of_stock} out of stock)")
        for n, hit in enumerate(result.hits, 1):
            print(f"{n}. {hit.book.title} / {hit.book.author} [{hit.book.shelf}] x{hit.book.copies_available} "
                  f"score={hit.score:.3f}  why: {hit.why_text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
