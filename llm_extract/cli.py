from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .clients import load_client
from .core import Extractor, LLMClientError, LLMConfigError, load_documents, write_results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm_extract", description="Schema-validated structured extraction with any LLM")
    parser.add_argument("input", help="a document, a folder of documents, a .jsonl, or a .csv")
    parser.add_argument("--schema", required=True, help="JSON schema file describing one record")
    parser.add_argument("-o", "--output", required=True, help="output .jsonl, .json or .csv")
    parser.add_argument("--instructions", default="", help="extra task instructions")
    parser.add_argument("--instructions-file", default=None)
    parser.add_argument("--provider", default=None, help="anthropic | fake | package.module:factory (default: $LLM_EXTRACT_PROVIDER)")
    parser.add_argument("--model", default=None, help="model id for the provider (default: $LLM_EXTRACT_MODEL)")
    parser.add_argument("--fake-response", default=None, help="JSON file returned by the fake provider (dry runs)")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--text-column", default="text")
    parser.add_argument("--id-column", default=None)
    parser.add_argument("--explode", default=None, help="list field to expand into one CSV row per item")
    return parser


def main(argv: list[str] | None = None, client=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        schema = json.loads(Path(args.schema).read_text(encoding="utf-8-sig"))
        instructions = args.instructions
        if args.instructions_file:
            instructions = (instructions + "\n\n" + Path(args.instructions_file).read_text(encoding="utf-8-sig")).strip()
        if client is None:
            fake = [json.loads(Path(args.fake_response).read_text(encoding="utf-8-sig"))] if args.fake_response else None
            client = load_client(args.provider, model=args.model, fake_responses=fake)
        documents = load_documents(args.input, text_column=args.text_column, id_column=args.id_column)
        if not documents:
            print("error: no documents found", file=sys.stderr)
            return 2
        extractor = Extractor(client, schema, instructions=instructions, max_attempts=args.max_attempts)
        results = extractor.extract_many(documents, workers=args.workers)
        path = write_results(results, args.output, explode=args.explode)
    except (LLMConfigError, LLMClientError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    failed = [r for r in results if not r.ok]
    print(f"extracted {len(results) - len(failed)}/{len(results)} document(s) -> {path}")
    for result in failed[:10]:
        print(f"  failed {result.source}: {'; '.join(result.errors)[:200]}", file=sys.stderr)
    return 1 if failed else 0
