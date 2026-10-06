"""Thin command-line frontend; exit 2 for input errors, 1 with --strict diagnostics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sad import Analyzer, __version__
from sad.crawler import CrawlConfig
from sad.ingest import BurpImporter, read_bounded
from sad.output import render


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sad", description="Static frontend API discovery")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("analyze", "analyze-js", "import-burp", "crawl"):
        sub = commands.add_parser(command)
        sub.add_argument("input", nargs="+" if command == "crawl" else None)
        sub.add_argument("--base-url")
        sub.add_argument("--format", choices=("json", "jsonl", "csv", "table"), default="table")
        sub.add_argument("-o", "--output", type=Path)
        sub.add_argument(
            "--max-steps",
            type=int,
            default=200_000,
            help="global static-analysis step budget, shared fairly among supplied sources",
        )
        sub.add_argument(
            "--max-context-steps",
            type=int,
            default=1000,
            help="maximum steps per top-level statement or symbolic entry point",
        )
        sub.add_argument(
            "--max-loop-steps",
            type=int,
            default=2000,
            help="maximum analysis steps per loop, including nested calls",
        )
        sub.add_argument(
            "--max-symbolic-loop-iterations",
            type=int,
            default=2,
            help="maximum fixed-point iterations for loops with unknown conditions (1-64)",
        )
        sub.add_argument(
            "--max-resource-bytes",
            type=int,
            default=8_000_000,
            help="maximum bytes per source resource",
        )
        sub.add_argument(
            "--strict", action="store_true", help="exit 1 if any diagnostic is emitted"
        )
        if command == "crawl":
            sub.add_argument("--depth", type=int, default=2)
            sub.add_argument("--max-pages", type=int, default=30)
            sub.add_argument("--max-resources", type=int, default=300)
            sub.add_argument("--allow-origin", action="append", default=[])
            sub.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        analyzer = Analyzer(
            base_url=args.base_url,
            max_steps=args.max_steps,
            max_loop_steps=args.max_loop_steps,
            max_context_steps=args.max_context_steps,
            max_symbolic_loop_iterations=args.max_symbolic_loop_iterations,
            max_resource_bytes=args.max_resource_bytes,
        )
        if args.command == "crawl":
            result = analyzer.analyze_urls(
                args.input,
                config=CrawlConfig(
                    depth=args.depth,
                    max_pages=args.max_pages,
                    max_resources=args.max_resources,
                    max_bytes=args.max_resource_bytes,
                    allowed_origins=tuple(args.allow_origin),
                    exclusions=tuple(args.exclude),
                ),
            )
        elif args.command == "import-burp":
            result = analyzer.analyze_dataset(BurpImporter.load(args.input, base_url=args.base_url))
        elif args.command == "analyze-js":
            result = analyzer.analyze_js(
                read_bounded(args.input, args.max_resource_bytes).decode("utf-8", errors="replace"),
                filename=args.input,
            )
        else:
            result = analyzer.analyze_path(args.input)
        output = render(result, args.format)
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
        if result.analysis_incomplete:
            print("sad: analysis incomplete; results are partial", file=sys.stderr)
        for diagnostic in result.diagnostics:
            print(f"sad: {diagnostic}", file=sys.stderr)
        return 1 if args.strict and result.diagnostics else 0
    except (OSError, ValueError) as exc:
        print(f"sad: {exc}", file=sys.stderr)
        return 2
